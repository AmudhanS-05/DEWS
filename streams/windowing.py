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
series_state = {"cur_start": float|None, "cur": [[ts, value, provisional, fetched_ts, ingest_ts]],
                "closed": {"<start>": [[ts, value, provisional, fetched_ts, ingest_ts], ...]},
                "last_stage": float|None, "mode": "LIVE"|"SIMULATION"}
fetched_ts and ingest_ts are the latency trace of a reading. State saved before they existed holds 3-item readings.

Rain (C2.2): the same site state holds one extra entry, RAIN_KEY = "_rain" (never a USGS series id):
    {"hours": {"<hour_end_ts>": rain_mm_10km | None}, "mode": ..., "late": n, "product": ...}
filled by on_rain() from rain.mrms / replay.rain (same key = site_no, so the same partition and the same state).
attach_rain() adds rain_1h, rain_3h, rain_6h, rain_24h to a window when it is emitted. Rule:
  - event time: window [ws, ws + w) gets the hours ENDING in (end - N h, end], end = ws + w (an MRMS file is valid at
    the end of its accumulation hour). rain_Nh = sum when all N hours are present, else None (never a 0 for
    "unknown"). An hour with no radar coverage (rain_mm null) counts as missing.
  - late rain never re-emits a window: rain for an hour whose window was already emitted is stored (it still counts in
    later windows' 3/6/24 h sums) and counted in "late"; the emitted window keeps what it had. So "no window emitted
    twice" is unchanged. A window re-emitted as a revision (late gauge reading) takes the rain known at that time.
  - live and replay never mix: the Faust app keeps them in separate tables and accepts only rain whose mode matches.
on_reading() / aggregate() are unchanged, so ml.backtest (which calls on_reading without rain) is not affected.
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
        "last_reading_ts": None, "fetched_ts": None, "ingest_ts": None,
    }
    if n == 0:
        agg["stage_last"] = prev_last
        agg["carried_forward"] = prev_last is not None
        return agg
    vals = [x[1] for x in rs]
    agg.update(stage_last=vals[-1], stage_max=max(vals), stage_min=min(vals),
               provisional=any(x[2] for x in rs), last_reading_ts=rs[-1][0])
    if len(rs[-1]) > 3:
        agg.update(fetched_ts=rs[-1][3], ingest_ts=rs[-1][4])
    if n >= 2 and rs[-1][0] > rs[0][0]:
        agg["rise_rate_ft_per_h"] = (vals[-1] - vals[0]) / ((rs[-1][0] - rs[0][0]) / 3600.0)
    elif prev_last is not None:
        agg["rise_rate_ft_per_h"] = (vals[-1] - prev_last) / (w / 3600.0)
    return agg


def _upsert(readings: list, ts: float, value: float, prov: bool, fetched_ts=None, ingest_ts=None) -> bool:
    """Insert or replace the reading at ts. Returns True if anything changed. A duplicate keeps its first trace times."""
    for item in readings:
        if item[0] == ts:
            if item[1] == value and bool(item[2]) == prov:
                return False
            item[1:] = [value, prov, fetched_ts, ingest_ts]
            return True
    readings.append([ts, value, prov, fetched_ts, ingest_ts])
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
    trace = (r.get("fetched_ts"), r.get("ingest_ts"))
    ws = window_start(ts, w)
    emitted = []
    if st["cur_start"] is None:
        st["cur_start"] = ws
    if ws == st["cur_start"]:
        _upsert(st["cur"], ts, v, prov, *trace)
    elif ws > st["cur_start"]:
        while st["cur_start"] < ws:          # close current + zero-fill any gap
            emitted.append(_close_current(r["site_no"], series, st, w, max_late_windows))
        _upsert(st["cur"], ts, v, prov, *trace)
    else:                                    # late reading
        key = str(int(ws))
        if key not in st["closed"]:
            return site_state, emitted, True
        if _upsert(st["closed"][key], ts, v, prov, *trace):
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
        if series == RAIN_KEY:
            continue
        if st["mode"] != "LIVE" or st["cur_start"] is None:
            continue
        while st["cur_start"] + w + grace_s <= now_ts:
            emitted.append(_close_current(site_no, series, st, w, max_late_windows))
    return site_state, emitted


# ---- rain (C2.2) --------------------------------------------------------------------------------------------------

RAIN_KEY = "_rain"
RAIN_SUMS_H = (1, 3, 6, 24)
RAIN_KEEP_H = 24 + 6          # hours kept behind the oldest open window: 24 h sums plus late-window revisions
RAIN_MAX_HOURS = 2000         # hard cap per site (a replay may preload rain for days ahead)


def rain_value(r: dict) -> float | None:
    v = r.get("rain_mm_10km", r.get("rain_mm"))
    return None if v is None else float(v)


def on_rain(site_state: dict, r: dict, w: int, mode: str) -> tuple[dict, bool]:
    """Store one rain record (one gauge-hour). Returns (site_state, late) where late = the window ending at that
    hour was already emitted (it is not re-emitted; see the module doc)."""
    rs = site_state.setdefault(RAIN_KEY, {"hours": {}, "mode": mode, "late": 0, "product": None})
    end = float(r["hour_end_ts"])
    rs["hours"][str(int(end))] = rain_value(r)
    rs["product"] = r.get("product", rs.get("product"))
    opens = [st["cur_start"] for k, st in site_state.items() if k != RAIN_KEY and st.get("cur_start") is not None]
    late = bool(opens) and end <= min(opens)          # window [end - w, end) closed before this rain arrived
    if late:
        rs["late"] = rs.get("late", 0) + 1
    if opens:
        keep_from = min(opens) - RAIN_KEEP_H * 3600
        for k in [k for k in rs["hours"] if float(k) < keep_from]:
            del rs["hours"][k]
    if len(rs["hours"]) > RAIN_MAX_HOURS:
        for k in sorted(rs["hours"], key=float)[:len(rs["hours"]) - RAIN_MAX_HOURS]:
            del rs["hours"][k]
    return site_state, late


def rain_sums(site_state: dict, window_end_ts: float) -> dict:
    hours = (site_state.get(RAIN_KEY) or {}).get("hours") or {}
    end = int(window_end_ts) - int(window_end_ts) % 3600
    out = {}
    for n in RAIN_SUMS_H:
        vals = [hours.get(str(end - k * 3600)) for k in range(n)]
        out[f"rain_{n}h"] = None if any(v is None for v in vals) else round(sum(vals), 3)
    return out


def attach_rain(site_state: dict, agg: dict, w: int) -> dict:
    """Add rain_1h/3h/6h/24h (and the product) to an emitted window, from the rain known now."""
    agg.update(rain_sums(site_state, float(agg["window_start_ts"]) + w))
    agg["rain_product"] = (site_state.get(RAIN_KEY) or {}).get("product")
    return agg


def on_record(state: dict, msg: dict, expected_mode: str, now: float, w: int, grace_s: float,
              max_late_windows: int = 3) -> tuple[dict, list[dict], list[dict]]:
    """Everything the Faust agent does for one record of its two topics (gauge + rain of ONE mode):
    reading / gauge heartbeat / rain / rain heartbeat -> (state, emitted windows with rain, deadletter records)."""
    site = msg.get("site_no")
    kind = msg.get("type", "reading")
    if kind == "rain":
        if msg.get("mode", "LIVE") != expected_mode:
            return state, [], [{"reason": f"mode_mismatch: {msg.get('mode')} on the {expected_mode} stream",
                                "record": msg}]
        state, _late = on_rain(state, msg, w, expected_mode)
        return state, [], []
    dlq: list[dict] = []
    if kind == "heartbeat":
        if "product" in msg:          # rain producer heartbeat: never closes (zero-fills) gauge windows
            return state, [], []
        state, emitted = on_tick(site, state, float(msg["now_ts"]), w, grace_s, max_late_windows)
    else:
        state, emitted, too_late = on_reading(state, {**msg, "ingest_ts": now}, w, msg.get("mode", "LIVE"),
                                              max_late_windows)
        if too_late:
            dlq.append({"reason": "too_late", "record": msg})
    for agg in emitted:
        attach_rain(state, agg, w)
    return state, emitted, dlq
