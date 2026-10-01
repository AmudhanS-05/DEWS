"""Offline backtest of a forecaster on cached gauge history (Phase 4).

Uses the SAME window code as the live stream (streams/windowing.py), so the
features in the backtest are exactly the features the live pipeline produces.

For each closed window ending at time t:
  forecast = model(window, horizon_h)
  ALERT if forecast >= threshold and current stage < threshold (cooldown applies)
Observed crossings come from the raw readings (common/hydro.find_crossings).

    python -m ml.backtest --site 09510200 --start 2015-01-01 --end 2025-12-31 \
        --thresholds 8,10,12,14 --category minor --horizon-h 3
(--thresholds = action,minor,moderate,major in ft from NWPS; omit with --lid to fetch them)
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter

from common import config, hydro
from common.nwps import CATEGORIES
from ml import baselines, evaluate
from streams import windowing


def windows_from_readings(readings: list[dict], w: int) -> list[dict]:
    state, out = {}, []
    for r in readings:
        state, em, _ = windowing.on_reading(state, r, w, "SIMULATION")
        out += [a for a in em if not a["revision"]]
    return out


def run(readings: list[dict], cats: dict, category: str, horizon_h: float, model: str = "rise_rate",
        w: int = config.WINDOW_SECONDS, cooldown_h: float = 6.0) -> dict:
    counts = Counter(r["series"] for r in readings)
    primary = counts.most_common(1)[0][0]
    rs = [r for r in readings if r["series"] == primary]
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    times = [t for t, _ in series]
    thr = cats[category]
    forecast_fn = baselines.BASELINES[model]

    alerts, pairs, last_alert = [], [], -1e18
    for agg in windows_from_readings(rs, w):
        t = agg["window_start_ts"] + w
        f = forecast_fn(agg, horizon_h)
        cur = agg["stage_last"]
        # stage error vs the first observation at/after t + horizon
        j = bisect.bisect_left(times, t + horizon_h * 3600)
        if j < len(series) and f is not None:
            pairs.append((f, series[j][1]))
        if f is not None and cur is not None and f >= thr > cur and t - last_alert >= cooldown_h * 3600:
            alerts.append((t, category))
            last_alert = t
    crossings = [(series[i][0], category) for i in hydro.find_crossings(series, thr)]
    res = evaluate.score_alerts(alerts, crossings, horizon_h)
    res.update({"model": model, "category": category, "threshold": thr, "horizon_h": horizon_h,
                "series": primary, "stage_error": evaluate.stage_errors(pairs)})
    return res


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--thresholds", help="action,minor,moderate,major in ft")
    p.add_argument("--lid", help="NWPS id to fetch thresholds instead")
    p.add_argument("--category", default="minor", choices=CATEGORIES)
    p.add_argument("--horizon-h", type=float, default=3.0)
    a = p.parse_args(argv)

    if a.thresholds:
        cats = dict(zip(CATEGORIES, (float(x) for x in a.thresholds.split(","))))
    elif a.lid:
        from common.http import get_json
        from common.nwps import parse_flood_categories
        cats = parse_flood_categories(get_json(config.NWPS_GAUGE_URL.format(lid=a.lid)))
    else:
        raise SystemExit("give --thresholds or --lid")
    from producers.replay_producer import load_readings
    readings = load_readings(a.site, a.start, a.end)
    for model in baselines.BASELINES:
        res = run(readings, cats, a.category, a.horizon_h, model)
        res.pop("lead_times_h")
        print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
