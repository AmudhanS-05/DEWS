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

Speed: windows (streams/windowing.py, unchanged) and crossings are built ONCE per gauge by `prepare`, then reused for
every model and horizon; the two built-in baselines are evaluated with numpy.

    python -m ml.harness [--data-dir D] [--gauges-only DRWT2,LCPT2,JNCT2,AHOT2] --horizons 1,2,3,4,5,6
"""
from __future__ import annotations

import csv
from statistics import median

import numpy as np

from common import config, hydro
from ml import baselines, evaluate
from ml.backtest import windows_from_readings
from ml.dataset import primary_series

COLUMNS = ["run_label", "data_source", "site_no", "model", "horizon_h", "precision", "recall", "f1",
           "false_alarms", "median_lead_time_h", "mae_ft", "rmse_ft", "notes"]
# Rise-rate 3 h F1 per gauge from FAQ.md ("Baselines"); the harness prints measured vs these, it never tunes to them.
REFERENCE_F1 = {"DRWT2": 0.65, "LCPT2": 0.26, "JNCT2": 0.64, "AHOT2": 0.41}


def prepare(readings: list[dict], cats: dict, category: str = "minor", w: int = config.WINDOW_SECONDS,
            slow_rise_h: float = 3.0) -> dict:
    """Everything that does not depend on the model or horizon, computed once per gauge."""
    rs = primary_series(readings)
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    aggs = windows_from_readings(rs, w)
    nan = np.nan
    return {
        "site": str(rs[0]["site_no"]) if rs else "", "thr": cats[category], "category": category, "aggs": aggs,
        "times": np.array([t for t, _ in series], dtype=float), "vals": np.array([v for _, v in series], dtype=float),
        "t": np.array([a["window_start_ts"] + w for a in aggs], dtype=float),
        "cur": np.array([nan if a["stage_last"] is None else a["stage_last"] for a in aggs], dtype=float),
        "rr": np.array([nan if a["rise_rate_ft_per_h"] is None else a["rise_rate_ft_per_h"] for a in aggs], dtype=float),
        "crossings": [(series[i][0], category, hydro.rise_time_h(series, i) >= slow_rise_h)
                      for i in hydro.find_crossings(series, cats[category])],
    }


def _forecasts(prep: dict, fn, h: float) -> np.ndarray:
    cur, rr = prep["cur"], prep["rr"]
    if fn is baselines.persistence:
        return cur
    if fn is baselines.rise_rate:                      # numpy twin of baselines.rise_rate (damping 1.0)
        return np.where(np.isnan(cur), np.nan, np.where(np.isnan(rr) | (rr <= 0), cur, cur + rr * h))
    return np.array([np.nan if (f := fn(a, h)) is None else f for a in prep["aggs"]], dtype=float)


def collect(prep: dict, forecast_fn, horizon_h: float, cooldown_h: float = 6.0) -> dict:
    """Alerts and (forecast, observed) stage pairs for one prepared gauge."""
    t, cur, thr = prep["t"], prep["cur"], prep["thr"]
    f = _forecasts(prep, forecast_fn, horizon_h)
    j = np.searchsorted(prep["times"], t + horizon_h * 3600, side="left")
    ok = ~np.isnan(f) & (j < len(prep["vals"]))
    obs = prep["vals"][np.minimum(j, len(prep["vals"]) - 1)] if len(prep["vals"]) else f
    alerts, last = [], -1e18
    for i in np.nonzero(~np.isnan(f) & ~np.isnan(cur) & (f >= thr) & (thr > cur))[0]:   # cooldown over candidates only
        if t[i] - last >= cooldown_h * 3600:
            alerts.append((float(t[i]), prep["category"]))
            last = t[i]
    return {"alerts": alerts, "crossings": prep["crossings"], "f": f[ok], "obs": obs[ok]}


def _slow_only(alerts, crossings, horizon_h):
    H = horizon_h * 3600
    fast = [ct for ct, _, slow in crossings if not slow]
    keep_a = [a for a in alerts if not any(a[0] < ct <= a[0] + H for ct in fast)]
    return keep_a, [(ct, c, True) for ct, c, slow in crossings if slow]


def distinct_storms(crossing_ts: list[float], storm_gap_h: float = 24.0) -> int:
    ts = sorted(crossing_ts)
    return sum(1 for i, t in enumerate(ts) if i == 0 or t - ts[i - 1] > storm_gap_h * 3600)


def _errors(f: np.ndarray, o: np.ndarray) -> dict:
    if not len(f):
        return {"mae": None, "rmse": None}
    e = f - o
    return {"mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e * e).mean()))}


def _fmt(x):
    return "NA" if x is None else f"{x:.2f}"


def _f1(p, r):
    if p is None or r is None:
        return None
    return 2 * p * r / (p + r) if p + r > 0 else 0.0


def _scored_row(label, site, model, h, parts, feed_delay_h):
    """parts: list of (alerts, crossings, f, obs), one per gauge; counts are summed (alerts never match across gauges)."""
    tp = fa = det = n_cross = n_alert = 0
    leads, cross_ts = [], []
    for alerts, cross, _, _ in parts:
        r = evaluate.score_alerts(alerts, [(t, c) for t, c, _ in cross], h)
        tp += r["true_positive_alerts"]; fa += r["false_alarms"]; det += r["detected"]
        n_cross += r["crossings"]; n_alert += r["alerts"]; leads += r["lead_times_h"]
        cross_ts += [t for t, _, _ in cross]
    prec = tp / n_alert if n_alert else None
    rec = det / n_cross if n_cross else None
    err = _errors(np.concatenate([p[2] for p in parts]), np.concatenate([p[3] for p in parts])) if parts else _errors(np.array([]), np.array([]))
    gross = median(leads) if leads else None
    net = median([l - feed_delay_h for l in leads]) if leads else None
    notes = (f"crossings={n_cross} detected={det} alerts={n_alert} storms={distinct_storms(cross_ts)} "
             f"lead_gross_h={_fmt(gross)} lead_net_h={_fmt(net)} (feed delay {feed_delay_h:g} h)")
    return {"run_label": label, "data_source": "replay", "site_no": site, "model": model, "horizon_h": h,
            "precision": prec, "recall": rec, "f1": _f1(prec, rec), "false_alarms": fa,
            "median_lead_time_h": gross, "mae_ft": err["mae"], "rmse_ft": err["rmse"], "notes": notes}


def evaluate_prepared(preps: dict[str, dict], model: str, forecast_fn, horizon_h: float, label: str,
                      feed_delay_h: float = 1.0) -> list[dict]:
    collected = {s: collect(p, forecast_fn, horizon_h) for s, p in preps.items()}
    parts = {s: (c["alerts"], c["crossings"], c["f"], c["obs"]) for s, c in collected.items()}
    rows = [_scored_row(label, s, model, horizon_h, [pt], feed_delay_h) for s, pt in parts.items()]
    rows.append(_scored_row(label, "POOLED", model, horizon_h, list(parts.values()), feed_delay_h))
    slow = []
    for a, cr, f, o in parts.values():
        sa, sc = _slow_only(a, cr, horizon_h)
        slow.append((sa, sc, f, o))
    rows.append(_scored_row(label, "POOLED-SLOW", model, horizon_h, slow, feed_delay_h))
    return rows


def evaluate_model(readings_by_site: dict[str, list[dict]], cats_by_site: dict[str, dict], model: str,
                   forecast_fn, horizon_h: float, label: str, category: str = "minor",
                   feed_delay_h: float = 1.0, slow_rise_h: float = 3.0) -> list[dict]:
    preps = {s: prepare(rs, cats_by_site[s], category, slow_rise_h=slow_rise_h) for s, rs in readings_by_site.items()}
    return evaluate_prepared(preps, model, forecast_fn, horizon_h, label, feed_delay_h)


def run_baselines(readings_by_site, cats_by_site, horizons=(3.0,), label="baseline", progress=None, **kw) -> list[dict]:
    prog = progress or (lambda it, desc, total=None: it)
    preps = {s: prepare(rs, cats_by_site[s], **{k: v for k, v in kw.items() if k in ("category", "slow_rise_h")})
             for s, rs in prog(readings_by_site.items(), "windows per gauge", len(readings_by_site))}
    delay = kw.get("feed_delay_h", 1.0)
    rows = []
    combos = [(n, fn, h) for n, fn in baselines.BASELINES.items() for h in horizons]
    for name, fn, h in prog(combos, "model x horizon", len(combos)):
        rows += evaluate_prepared(preps, name, fn, h, label, delay)
    return rows


def reference_table(rows: list[dict], lid_by_site: dict[str, str]) -> list[tuple]:
    """(lid, reference F1, measured F1, diff) for rise_rate at 3 h on the reference gauges."""
    out = []
    for r in rows:
        lid = lid_by_site.get(r["site_no"])
        if r["model"] == "rise_rate" and r["horizon_h"] == 3.0 and lid in REFERENCE_F1:
            m = r["f1"]
            out.append((lid, REFERENCE_F1[lid], m, None if m is None else m - REFERENCE_F1[lid]))
    return out


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
    import os
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="M1.4: baseline numbers from the cached history.")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--horizons", default="3")
    p.add_argument("--label", default="m1.4-baselines")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only", help="comma list of NWPS ids or site numbers, e.g. DRWT2,LCPT2,JNCT2,AHOT2")
    p.add_argument("--data-dir", help="folder holding raw/usgs/ (default: DATA_DIR env or ./data)")
    p.add_argument("--out", default="ml/figures/baselines.csv")
    p.add_argument("--insert", action="store_true", help="also INSERT into evaluation_runs (needs the DB up)")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer = Timer()
    horizons = [float(x) for x in a.horizons.split(",")]
    print(f"ml.harness: baselines (persistence, rise_rate) at h={horizons} on cached USGS history "
          f"{a.start}..{a.end}; reading from {config.DATA_DIR}/raw/usgs")
    preps, gauges = {}, []
    for g in bar(iter_gauges(a.gauges, a.gauges_only, a.start, a.end, config.DATA_DIR), "windows per gauge",
                 total=count_gauges(a.gauges, a.gauges_only)):
        prep = prepare(g["readings"], g["cats"])
        prep["aggs"] = None                     # built-in baselines are vectorised; frees ~1 GB per gauge
        preps[g["site_no"]] = prep
        gauges.append({"site_no": g["site_no"], "lid": g["lid"]})
        del g
    if not preps:
        raise SystemExit("ERROR: no gauge could be evaluated; check --data-dir and --gauges-only")
    rows = []
    combos = [(n, fn, h) for n, fn in baselines.BASELINES.items() for h in horizons]
    for name, fn, h in bar(combos, "model x horizon", len(combos)):
        rows += evaluate_prepared(preps, name, fn, h, a.label)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_csv(rows, a.out)
    if a.insert:
        insert_rows(rows)
    for r in rows:
        if r["site_no"].startswith("POOLED") and r["horizon_h"] == horizons[0]:
            print(f"{r['model']:12s} {r['site_no']:12s} P={_fmt(r['precision'])} R={_fmt(r['recall'])} "
                  f"F1={_fmt(r['f1'])} FA={r['false_alarms']}  {r['notes']}")
    table = reference_table(rows, {g["site_no"]: g["lid"] for g in gauges})
    if table:
        print("\nrise-rate 3 h F1 vs FAQ reference (no tuning):\n  gauge   ref    measured  diff")
        for lid, ref, m, d in table:
            print(f"  {lid:6s} {ref:5.2f}  {_fmt(m):>8s}  {'NA' if d is None else f'{d:+.2f}'}")
    print(f"\ndone: {len(gauges)} gauges, {len(rows)} rows -> {a.out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
