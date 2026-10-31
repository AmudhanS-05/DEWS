"""M1.1: pooled supervised dataset from cached gauge history (offline, no Kafka).

Features come from the SAME hourly windows the stream builds (`streams/windowing.py`, via
`ml.backtest.windows_from_readings`), so offline and live inputs match. One row per closed window.

Stage features are RELATIVE to the gauge's official minor stage (pooled model, decided with faculty):
`rel_minor = stage_last - minor`. Labels use official NWPS categories only.

Labels, for each horizon h in `horizons` (window end t):
  y_stage_h  stage at the first reading at/after t + h (None if the record ends)
  y_cross_h  1 if the stage is below the category now and reaches it within (t, t + h], else 0
`event_id` is "<site>|<crossing_ts>" for rows within `lead_h` hours before an observed crossing, else "".
`slow` is True when that event's rise time is >= `slow_rise_h` (default 3 h).
Split by time with `time_split`; never at random.
"""
from __future__ import annotations

import bisect
import csv
from collections import Counter

from common import config, hydro
from common.nwps import CATEGORIES
from ml.backtest import windows_from_readings

HORIZONS = (1, 2, 3, 4, 5, 6)
LAGS_H = (1, 3, 6)


def primary_series(readings: list[dict]) -> list[dict]:
    primary = Counter(r["series"] for r in readings).most_common(1)[0][0]
    return [r for r in readings if r["series"] == primary]


def build_site(readings: list[dict], cats: dict, category: str = "minor", horizons=HORIZONS,
               w: int = config.WINDOW_SECONDS, slow_rise_h: float = 3.0, lead_h: float = 6.0) -> list[dict]:
    rs = primary_series(readings)
    if not rs:
        return []
    site = str(rs[0]["site_no"])
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    times = [t for t, _ in series]
    thr, minor = cats[category], cats["minor"]
    crossings = hydro.find_crossings(series, thr)
    events = [(series[i][0], f"{site}|{int(series[i][0])}", hydro.rise_time_h(series, i) >= slow_rise_h)
              for i in crossings]
    by_t: dict[float, float] = {}                 # window end -> stage_last, for lag features
    wins = windows_from_readings(rs, w)
    for a in wins:
        by_t[a["window_start_ts"] + w] = a["stage_last"]

    rows = []
    for a in wins:
        t, cur = a["window_start_ts"] + w, a["stage_last"]
        if cur is None:
            continue
        row = {"site_no": site, "t": t, "stage": cur, "rel_minor": cur - minor,
               "rel_action": cur - cats["action"], "frac_to_minor": cur / minor if minor else None,
               "rise_rate": a["rise_rate_ft_per_h"] or 0.0, "stage_range": (a["stage_max"] or cur) - (a["stage_min"] or cur),
               "n_readings": a["n_readings"], "carried_forward": int(a["carried_forward"]),
               "provisional": int(a["provisional"])}
        for lag in LAGS_H:
            prev = by_t.get(t - lag * 3600)
            row[f"d{lag}h"] = cur - prev if prev is not None else None
        for h in horizons:
            j = bisect.bisect_left(times, t + h * 3600)
            row[f"y_stage_{h}"] = series[j][1] if j < len(series) else None
            hi = bisect.bisect_right(times, t + h * 3600)
            lo = bisect.bisect_right(times, t)
            future_max = max((v for _, v in series[lo:hi]), default=None)
            row[f"y_cross_{h}"] = int(cur < thr and future_max is not None and future_max >= thr)
        ev = next(((et, eid, slow) for et, eid, slow in events if 0 < et - t <= lead_h * 3600), None)
        row["event_id"], row["slow"] = (ev[1], ev[2]) if ev else ("", None)
        rows.append(row)
    return rows


def build_pooled(readings_by_site: dict[str, list[dict]], cats_by_site: dict[str, dict], **kw) -> list[dict]:
    rows = []
    for site, rs in readings_by_site.items():
        rows += build_site(rs, cats_by_site[site], **kw)
    return sorted(rows, key=lambda r: r["t"])


def event_weights(rows: list[dict]) -> list[float]:
    """Each gauge carries equal total weight, so a creek with many fast events cannot dominate the pooled fit;
    within a gauge every positive event is weighted equally (its rows share weight 1 / rows-in-event)."""
    per_event = Counter(r["event_id"] for r in rows if r["event_id"])
    n_events = Counter()
    for eid in per_event:
        n_events[eid.split("|")[0]] += 1
    n_rows = Counter(r["site_no"] for r in rows)
    out = []
    for r in rows:
        if r["event_id"]:
            out.append(1.0 / (per_event[r["event_id"]] * max(n_events[r["site_no"]], 1)))
        else:
            out.append(1.0 / n_rows[r["site_no"]])
    return out


def time_split(rows: list[dict], train_end_ts: float, test_start_ts: float | None = None) -> tuple[list, list]:
    """Train: t < train_end_ts. Test: t >= test_start_ts (default train_end_ts). Rolling-origin = call repeatedly."""
    test_start_ts = train_end_ts if test_start_ts is None else test_start_ts
    return [r for r in rows if r["t"] < train_end_ts], [r for r in rows if r["t"] >= test_start_ts]


def to_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)


def main(argv=None):
    import argparse
    import os
    from producers.replay_producer import load_readings
    p = argparse.ArgumentParser(description="Build the pooled dataset from cached USGS history.")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--data-dir", help="folder holding raw/usgs/ (default: DATA_DIR env or ./data)")
    p.add_argument("--out", default="ml/figures/pooled_dataset.csv")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    rbs, cbs = {}, {}
    with open(a.gauges, encoding="utf-8") as f:
        for g in csv.DictReader(f):
            try:
                cbs[g["site_no"]] = {k: float(g[k]) for k in CATEGORIES}
                rbs[g["site_no"]] = load_readings(g["site_no"], a.start, a.end)
            except (SystemExit, ValueError):
                cbs.pop(g["site_no"], None)
                print(f"skip {g['site_no']}: no cached data or missing categories")
    rows = build_pooled(rbs, cbs)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    to_csv(rows, a.out)
    print(f"{len(rows)} rows, {len(rbs)} gauges -> {a.out}")


if __name__ == "__main__":
    main()
