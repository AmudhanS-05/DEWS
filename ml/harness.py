"""M1.2 + M1.4: event-based evaluation harness -> `evaluation_runs` rows.

For each gauge: run a forecaster (a function `agg, horizon_h -> stage`) over the hourly windows, raise an alert when
the forecast reaches the category while the current stage is below it (6 h cooldown, as ml/backtest.py), and score
alerts against observed crossings with ml/evaluate.py. Three views per model and horizon:
  * per gauge         site_no = the USGS site
  * pooled            site_no = "POOLED": counts summed over gauges (micro-average)
  * pooled slow-only  site_no = "POOLED-SLOW": only crossings with rise time >= slow_rise_h; an alert whose only
                      match is a fast crossing is dropped, not counted as a false alarm.
Lead time is reported gross and net of the feed delay (`feed_delay_h`, default 1 h) in `notes`; `data_source` is
"replay" and rows are never merged with live. `notes` also carries the number of crossings and distinct storms
(crossings of different gauges closer than `storm_gap_h` count as one storm).
"""
from __future__ import annotations

import bisect
import csv
from statistics import median

from common import config, hydro
from ml import baselines, evaluate
from ml.backtest import windows_from_readings
from ml.dataset import primary_series

COLUMNS = ["run_label", "data_source", "site_no", "model", "horizon_h", "precision", "recall", "f1",
           "false_alarms", "median_lead_time_h", "mae_ft", "rmse_ft", "notes"]


def collect(readings: list[dict], cats: dict, forecast_fn, horizon_h: float, category: str = "minor",
            w: int = config.WINDOW_SECONDS, cooldown_h: float = 6.0, slow_rise_h: float = 3.0) -> dict:
    """Raw alerts, crossings (with slow flag) and stage pairs for one gauge."""
    rs = primary_series(readings)
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    times = [t for t, _ in series]
    thr = cats[category]
    alerts, pairs, last = [], [], -1e18
    for agg in windows_from_readings(rs, w):
        t, cur = agg["window_start_ts"] + w, agg["stage_last"]
        f = forecast_fn(agg, horizon_h)
        j = bisect.bisect_left(times, t + horizon_h * 3600)
        if f is not None and j < len(series):
            pairs.append((f, series[j][1]))
        if f is not None and cur is not None and f >= thr > cur and t - last >= cooldown_h * 3600:
            alerts.append((t, category))
            last = t
    crossings = [(series[i][0], category, hydro.rise_time_h(series, i) >= slow_rise_h)
                 for i in hydro.find_crossings(series, thr)]
    return {"alerts": alerts, "crossings": crossings, "pairs": pairs, "site": str(rs[0]["site_no"]) if rs else ""}


def _slow_only(alerts, crossings, horizon_h):
    H = horizon_h * 3600
    fast = [ct for ct, _, slow in crossings if not slow]
    keep_a = [a for a in alerts if not any(a[0] < ct <= a[0] + H for ct in fast)]
    return keep_a, [(ct, c, True) for ct, c, slow in crossings if slow]


def distinct_storms(crossing_ts: list[float], storm_gap_h: float = 24.0) -> int:
    ts = sorted(crossing_ts)
    return sum(1 for i, t in enumerate(ts) if i == 0 or t - ts[i - 1] > storm_gap_h * 3600)


def _row(label, site, model, h, alerts, crossings, pairs, feed_delay_h, extra=""):
    res = evaluate.score_alerts(alerts, [(t, c) for t, c, _ in crossings], h)
    err = evaluate.stage_errors(pairs)
    lead = evaluate.report_lead_times(res["lead_times_h"], [feed_delay_h] * len(res["lead_times_h"]))
    fmt = lambda x: "NA" if x is None else f"{x:.2f}"
    notes = (f"crossings={res['crossings']} detected={res['detected']} alerts={res['alerts']} "
             f"storms={distinct_storms([t for t, _, _ in crossings])} lead_gross_h={fmt(lead['gross_median_h'])} "
             f"lead_net_h={fmt(lead['net_median_h'])} (feed delay {feed_delay_h:g} h){extra}")
    return {"run_label": label, "data_source": "replay", "site_no": site, "model": model, "horizon_h": h,
            "precision": res["precision"], "recall": res["recall"], "f1": res["f1"],
            "false_alarms": res["false_alarms"], "median_lead_time_h": res["median_lead_time_h"],
            "mae_ft": err["mae"], "rmse_ft": err["rmse"], "notes": notes}


def evaluate_model(readings_by_site: dict[str, list[dict]], cats_by_site: dict[str, dict], model: str,
                   forecast_fn, horizon_h: float, label: str, category: str = "minor",
                   feed_delay_h: float = 1.0, slow_rise_h: float = 3.0) -> list[dict]:
    rows = []
    for site, rs in readings_by_site.items():
        c = collect(rs, cats_by_site[site], forecast_fn, horizon_h, category, slow_rise_h=slow_rise_h)
        rows.append(_row(label, site, model, horizon_h, c["alerts"], c["crossings"], c["pairs"], feed_delay_h))
    rows.append(_pooled(label, "POOLED", model, horizon_h, readings_by_site, cats_by_site, forecast_fn,
                        category, feed_delay_h, slow_rise_h, slow=False))
    rows.append(_pooled(label, "POOLED-SLOW", model, horizon_h, readings_by_site, cats_by_site, forecast_fn,
                        category, feed_delay_h, slow_rise_h, slow=True))
    return rows


def _pooled(label, site_name, model, h, rbs, cbs, fn, category, delay, slow_rise_h, slow):
    """Micro-average: score each gauge separately, then add the counts (alerts never match across gauges)."""
    tp = fa = det = n_cross = n_alert = 0
    leads, pairs, cross_ts = [], [], []
    for site, rs in rbs.items():
        c = collect(rs, cbs[site], fn, h, category, slow_rise_h=slow_rise_h)
        a, cr = (_slow_only(c["alerts"], c["crossings"], h) if slow else (c["alerts"], c["crossings"]))
        r = evaluate.score_alerts(a, [(t, k) for t, k, _ in cr], h)
        tp += r["true_positive_alerts"]; fa += r["false_alarms"]; det += r["detected"]
        n_cross += r["crossings"]; n_alert += r["alerts"]; leads += r["lead_times_h"]; pairs += c["pairs"]
        cross_ts += [t for t, _, _ in cr]
    prec = tp / n_alert if n_alert else None
    rec = det / n_cross if n_cross else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    err = evaluate.stage_errors(pairs)
    gross = median(leads) if leads else None
    net = median([l - delay for l in leads]) if leads else None
    fmt = lambda x: "NA" if x is None else f"{x:.2f}"
    notes = (f"crossings={n_cross} detected={det} alerts={n_alert} storms={distinct_storms(cross_ts)} "
             f"lead_gross_h={fmt(gross)} lead_net_h={fmt(net)} (feed delay {delay:g} h)")
    return {"run_label": label, "data_source": "replay", "site_no": site_name, "model": model, "horizon_h": h,
            "precision": prec, "recall": rec, "f1": f1, "false_alarms": fa, "median_lead_time_h": gross,
            "mae_ft": err["mae"], "rmse_ft": err["rmse"], "notes": notes}


def run_baselines(readings_by_site, cats_by_site, horizons=(3.0,), label="baseline", **kw) -> list[dict]:
    rows = []
    for name, fn in baselines.BASELINES.items():
        for h in horizons:
            rows += evaluate_model(readings_by_site, cats_by_site, name, fn, h, label, **kw)
    return rows


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(rows)


def insert_rows(rows: list[dict], dsn: str = config.PG_DSN) -> None:
    import psycopg2
    cols = COLUMNS
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(f"INSERT INTO evaluation_runs ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})",
                        [r[c] for c in cols])


def main(argv=None):
    import argparse
    import csv as _csv
    from producers.replay_producer import load_readings
    p = argparse.ArgumentParser(description="M1.4: baseline numbers for all gauges from the cached history.")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--horizons", default="3")
    p.add_argument("--label", default="m1.4-baselines")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--out", default="ml/figures/baselines.csv")
    p.add_argument("--insert", action="store_true", help="also INSERT into evaluation_runs (needs the DB up)")
    a = p.parse_args(argv)
    rbs, cbs = {}, {}
    with open(a.gauges, encoding="utf-8") as f:
        for g in _csv.DictReader(f):
            try:
                rbs[g["site_no"]] = load_readings(g["site_no"], a.start, a.end)
                cbs[g["site_no"]] = {k: float(g[k]) for k in ("action", "minor", "moderate", "major")}
            except (SystemExit, ValueError):
                print(f"skip {g['site_no']}: no cached data or missing categories")
    rows = run_baselines(rbs, cbs, [float(x) for x in a.horizons.split(",")], a.label)
    import os
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    write_csv(rows, a.out)
    if a.insert:
        insert_rows(rows)
    for r in rows:
        if r["site_no"].startswith("POOLED"):
            print(r["model"], r["site_no"], r["precision"], r["recall"], r["f1"], r["notes"])


if __name__ == "__main__":
    main()
