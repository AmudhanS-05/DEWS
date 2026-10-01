"""Event-time tumbling windows per gauge series, as pure functions.

Faust (streams/faust_app.py) keeps this state in a changelog-backed table keyed by
site_no, so it survives a worker crash; all the logic lives here so it can be unit
tested without a broker.

Why hand-rolled instead of a Faust windowed table:
- USGS delivers readings in hourly BATCHES, so a window is only complete after the
  next batch arrives. We close a window when a reading for a later window arrives
  (event time), or, in LIVE mode, when a wall-clock heartbeat passes
  window end + grace.
- Silent gauges must still produce a row (zero-fill, n_readings = 0); otherwise
  "no data" is invisible to the forecaster and the dashboard.
- Late or revised readings for an already-closed window (provisional data gets
  revised) re-emit that window as a revision, while it is within `max_late_windows`.

State per site (JSON-serialisable): {series_id: series_state}
series_state = {"cur_start": float|None, "cur": [[ts, value, provisional]],
                "closed": {"<start>": [[ts, value, provisional], ...]},
                "last_stage": float|None, "mode": "LIVE"|"SIMULATION"}
"""
from __future__ import annotations

from common.timeutil import epoch_to_iso


def window_start(ts: float, w: int) -> float:
    return float(int(ts) - int(ts) % w)


def new_series_state(mode: str) -> dict:
    return {"cur_start": None, "cur": [], "closed": {}, "last_stage": None, "mode": mode}


def aggregate(site_no: str, series: str, start: float, w: int, readings: list, prev_last,
              mode: str, revision: bool = False) -> dict:
    rs = sorted(readings, key=lambda x: x[0])
    n = len(rs)
    agg = {
        "site_no": site_no, "series": series, "mode": mode,
        "window_start": epoch_to_iso(start), "window_end": epoch_to_iso(start + w),
        "window_start_ts": start, "n_readings": n, "revision": revision,
        "stage_last": None, "stage_max": None, "stage_min": None,
        "rise_rate_ft_per_h": None, "carried_forward": False, "provisional": False,
        "last_reading_ts": None,
    }
    if n == 0:
        agg["stage_last"] = prev_last
        agg["carried_forward"] = prev_last is not None
        return agg
    vals = [v for _, v, _ in rs]
    agg.update(stage_last=vals[-1], stage_max=max(vals), stage_min=min(vals),
               provisional=any(p for _, _, p in rs), last_reading_ts=rs[-1][0])
    if n >= 2 and rs[-1][0] > rs[0][0]:
        agg["rise_rate_ft_per_h"] = (vals[-1] - vals[0]) / ((rs[-1][0] - rs[0][0]) / 3600.0)
    elif prev_last is not None:
        agg["rise_rate_ft_per_h"] = (vals[-1] - prev_last) / (w / 3600.0)
    return agg


def _upsert(readings: list, ts: float, value: float, prov: bool) -> bool:
    """Insert or replace the reading at ts. Returns True if anything changed."""
    for item in readings:
        if item[0] == ts:
            if item[1] == value and bool(item[2]) == prov:
                return False
            item[1], item[2] = value, prov
            return True
    readings.append([ts, value, prov])
    return True


def _close_current(site_no: str, series: str, st: dict, w: int, max_late_windows: int) -> dict:
    agg = aggregate(site_no, series, st["cur_start"], w, st["cur"], st["last_stage"], st["mode"])
    st["closed"][str(int(st["cur_start"]))] = st["cur"]
    # keep only the most recent closed windows for late data
    keys = sorted(st["closed"], key=float)
    if len(keys) > max_late_windows:
        for k in keys[:len(keys) - max_late_windows]:
            del st["closed"][k]
    if st["cur"]:
        st["last_stage"] = sorted(st["cur"], key=lambda x: x[0])[-1][1]
    st["cur_start"] += w
    st["cur"] = []
    return agg


def on_reading(site_state: dict, r: dict, w: int, mode: str, max_late_windows: int = 3):
    """Apply one reading. Returns (site_state, emitted_aggregates, dropped_too_late: bool)."""
    series = str(r["series"])
    st = site_state.setdefault(series, new_series_state(mode))
    ts, v, prov = float(r["reading_ts"]), float(r["value"]), "P" in (r.get("qualifiers") or [])
    ws = window_start(ts, w)
    emitted = []
    if st["cur_start"] is None:
        st["cur_start"] = ws
    if ws == st["cur_start"]:
        _upsert(st["cur"], ts, v, prov)
    elif ws > st["cur_start"]:
        while st["cur_start"] < ws:          # close current + zero-fill any gap
            emitted.append(_close_current(r["site_no"], series, st, w, max_late_windows))
        _upsert(st["cur"], ts, v, prov)
    else:                                    # late reading
        key = str(int(ws))
        if key not in st["closed"]:
            return site_state, emitted, True
        if _upsert(st["closed"][key], ts, v, prov):
            older = [k for k in st["closed"] if float(k) < ws]
            prev = None
            if older:
                prev_rs = st["closed"][max(older, key=float)]
                prev = sorted(prev_rs, key=lambda x: x[0])[-1][1] if prev_rs else None
            emitted.append(aggregate(r["site_no"], series, ws, w, st["closed"][key], prev, st["mode"],
                                     revision=True))
    return site_state, emitted, False


def on_tick(site_no: str, site_state: dict, now_ts: float, w: int, grace_s: float,
            max_late_windows: int = 3):
    """Wall-clock heartbeat (LIVE only): close every window whose end + grace has passed,
    including empty ones (zero-fill). Returns (site_state, emitted)."""
    emitted = []
    for series, st in site_state.items():
        if st["mode"] != "LIVE" or st["cur_start"] is None:
            continue
        while st["cur_start"] + w + grace_s <= now_ts:
            emitted.append(_close_current(site_no, series, st, w, max_late_windows))
    return site_state, emitted
