"""M1.1: pooled supervised dataset from cached gauge history (offline, no Kafka).

Features come from the SAME hourly windows the stream builds (`streams/windowing.py`, via
`ml.backtest.windows_from_readings`), so offline and live inputs match. One row per closed window.

Stage features are RELATIVE to the gauge's official minor stage (pooled model, decided with faculty):
`rel_minor = stage_last - minor`. Labels use official NWPS categories only.

Labels, for each horizon h in `horizons` (window end t):
  y_stage_h  stage at the first reading at/after t + h (missing if the record ends)
  y_cross_h  1 if the stage is below the category now and reaches it within (t, t + h], else 0
`event_id` is "<site>|<crossing_ts>" for rows within `lead_h` hours before an observed crossing, else "".
`slow` is True when that event's rise time is >= `slow_rise_h` (default 3 h).
Split by time with `time_split`; never at random.

Computation is vectorised per gauge (`build_site_columns` -> numpy columns); `build_site` returns the same data
as a list of dicts (missing = None) for small inputs and tests. The CLI writes Parquet.

    python -m ml.dataset [--data-dir D] [--gauges-only DRWT2,LCPT2] --out ml/figures/pooled_dataset.parquet
"""
from __future__ import annotations

import csv
from collections import Counter

import numpy as np

from common import config, hydro
from common.nwps import CATEGORIES
from ml.backtest import windows_from_readings

HORIZONS = (1, 2, 3, 4, 5, 6)
LAGS_H = (1, 3, 6)


def primary_series(readings: list[dict]) -> list[dict]:
    primary = Counter(r["series"] for r in readings).most_common(1)[0][0]
    return [r for r in readings if r["series"] == primary]


def _nan(x):
    return np.nan if x is None else x


def _range_max(vals: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """max(vals[lo:hi]) per row, NaN where the range is empty."""
    ok = hi > lo
    ext = np.append(vals, -np.inf)                       # sentinel so index len(vals) is valid
    idx = np.empty(2 * len(lo), dtype=np.int64)
    idx[0::2] = np.minimum(lo, len(vals))
    idx[1::2] = np.where(ok, np.minimum(hi, len(vals)), np.minimum(lo, len(vals)) + 1)
    idx[1::2] = np.minimum(idx[1::2], len(vals))
    out = np.maximum.reduceat(ext, idx)[0::2]
    return np.where(ok, out, np.nan)


def build_site_columns(readings: list[dict], cats: dict, category: str = "minor", horizons=HORIZONS,
                       w: int = config.WINDOW_SECONDS, slow_rise_h: float = 3.0, lead_h: float = 6.0,
                       wins: list[dict] | None = None) -> dict[str, np.ndarray]:
    rs = primary_series(readings)
    if not rs:
        return {}
    site = str(rs[0]["site_no"])
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    times = np.array([t for t, _ in series], dtype=float)
    vals = np.array([v for _, v in series], dtype=float)
    thr, minor = cats[category], cats["minor"]
    cross_idx = hydro.find_crossings(series, thr)
    ev_t = np.array([series[i][0] for i in cross_idx], dtype=float)
    ev_slow = [hydro.rise_time_h(series, i) >= slow_rise_h for i in cross_idx]

    wins = [a for a in (wins if wins is not None else windows_from_readings(rs, w)) if a["stage_last"] is not None]
    t = np.array([a["window_start_ts"] + w for a in wins], dtype=float)
    cur = np.array([a["stage_last"] for a in wins], dtype=float)
    n = len(t)
    col: dict[str, np.ndarray] = {
        "site_no": np.full(n, site, dtype=object), "t": t, "stage": cur, "rel_minor": cur - minor,
        "rel_action": cur - cats["action"], "frac_to_minor": cur / minor if minor else np.full(n, np.nan),
        "rise_rate": np.array([a["rise_rate_ft_per_h"] or 0.0 for a in wins], dtype=float),
        "stage_range": np.array([(a["stage_max"] if a["stage_max"] is not None else c)
                                 - (a["stage_min"] if a["stage_min"] is not None else c)
                                 for a, c in zip(wins, cur)], dtype=float),
        "n_readings": np.array([a["n_readings"] for a in wins], dtype=np.int64),
        "carried_forward": np.array([int(a["carried_forward"]) for a in wins], dtype=np.int64),
        "provisional": np.array([int(a["provisional"]) for a in wins], dtype=np.int64),
    }
    for lag in LAGS_H:                                    # stage `lag` hours ago from the window sequence
        pos = np.searchsorted(t, t - lag * 3600)
        pos_c = np.minimum(pos, n - 1)
        hit = (pos < n) & (t[pos_c] == t - lag * 3600)
        col[f"d{lag}h"] = np.where(hit, cur - cur[pos_c], np.nan)
    lo = np.searchsorted(times, t, side="right")
    for h in horizons:
        end = t + h * 3600
        j = np.searchsorted(times, end, side="left")
        col[f"y_stage_{h}"] = np.where(j < len(vals), vals[np.minimum(j, len(vals) - 1)], np.nan)
        fmax = _range_max(vals, lo, np.searchsorted(times, end, side="right"))
        col[f"y_cross_{h}"] = ((cur < thr) & (fmax >= thr)).astype(np.int64)   # NaN >= thr is False
    ev_id = np.full(n, "", dtype=object)
    slow = np.full(n, None, dtype=object)
    if len(ev_t):
        k = np.searchsorted(ev_t, t, side="right")        # first event strictly after t
        kc = np.minimum(k, len(ev_t) - 1)
        hit = (k < len(ev_t)) & (ev_t[kc] - t <= lead_h * 3600)
        for i in np.nonzero(hit)[0]:
            ev_id[i] = f"{site}|{int(ev_t[kc[i]])}"
            slow[i] = ev_slow[kc[i]]
    col["event_id"], col["slow"] = ev_id, slow
    return col


def build_site(readings: list[dict], cats: dict, **kw) -> list[dict]:
    col = build_site_columns(readings, cats, **kw)
    if not col:
        return []
    n = len(col["t"])
    out = []
    for i in range(n):
        row = {}
        for k, v in col.items():
            x = v[i]
            if isinstance(x, (float, np.floating)):
                x = None if np.isnan(x) else float(x)
            elif isinstance(x, np.integer):
                x = int(x)
            row[k] = x
        out.append(row)
    return out


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
    import pandas as pd
    from ml.gauges import load_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="Build the pooled dataset from cached USGS history.")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only", help="comma list of NWPS ids or site numbers, e.g. DRWT2,LCPT2")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--data-dir", help="folder holding raw/usgs/ (default: DATA_DIR env or ./data)")
    p.add_argument("--out", default="ml/figures/pooled_dataset.parquet", help=".parquet (default) or .csv")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer = Timer()
    gauges = load_gauges(a.gauges, a.gauges_only, a.start, a.end, config.DATA_DIR)
    print(f"ml.dataset: building windows + labels for {len(gauges)} gauges ({a.start}..{a.end}) from "
          f"{config.DATA_DIR}/raw/usgs -> {a.out}")
    if not gauges:
        raise SystemExit("ERROR: no gauge had cached data; check --data-dir and --gauges-only")
    frames = []
    for g in bar(gauges, "gauges"):
        col = build_site_columns(g["readings"], g["cats"])
        if col:
            frames.append(pd.DataFrame(col))
    df = pd.concat(frames, ignore_index=True).sort_values("t", kind="stable")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    if a.out.endswith(".csv"):
        df.to_csv(a.out, index=False)
    else:
        df["slow"] = df["slow"].astype("boolean")
        df.to_parquet(a.out, index=False)
    print(f"done: {len(df)} rows, {len(frames)} gauges, {int((df['event_id'] != '').sum())} rows in events "
          f"-> {a.out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
