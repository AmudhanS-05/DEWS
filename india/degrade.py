"""I4.1 / INDIA_STUDY step C: degradation experiment on REAL US data.

Take the cached USGS readings of all 38 gauges over the S3 test period (2024-01-01 .. 2026-10-31; s3_v0 never
trained on it), degrade ONE property at a time towards what Indian open data looks like, rebuild the hourly windows
with the stream's own code (streams/windowing.py via ml.backtest) and score the 3 h rise-rate rule and the active
forecaster s3_v0 with the harness alert rule (below minor, 6 h cooldown). The truth is always the crossings of the
official NWPS minor stage in the UNdegraded readings, so a degraded feed that misses a flood is counted as a miss.

Scenarios (each changes one property; everything else at the baseline: native interval, no feed delay (alerts at
window end, as ml/harness.py, so the baseline equals the S3 test numbers), no gaps, official thresholds):
  interval   1 h, 3 h, daily (one reading per day, the one nearest 08:00 IST = 02:30 UTC, like the India-WRIS
             manual staff-gauge readings seen on 31 Oct 2026)
  delay      feed delay 1 h (about the US live feed), 6 h, 24 h: an alert computed at window end t is available only at
             t + delay, and counts only if it still arrives before the crossing (within the 3 h horizon)
  gaps       10 % and 30 % of readings removed in random blocks of 6 to 48 h (seeded)
  thresholds official minor (baseline) vs. no official levels: minor estimated as the 99.0 / 99.5 / 99.9th percentile
             of the gauge's own 2015-2023 stage (action = minor); the truth stays official
Reported per model and scenario (POOLED over gauges): crossings, detected, recall, alerts, false alarms, false alarms
per gauge-year, precision, F1, median lead time after the feed delay.
All replayed data: mode SIMULATION; not live results.

    python -m india.degrade --out india/figures/degradation.csv          # ~30-40 min, 3 worker processes
    python -m india.degrade plot --csv india/figures/degradation.csv --out india/figures/degradation.png
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from statistics import median

import numpy as np

START, END = "2024-01-01", "2026-10-31"
H = 3
BASE_DELAY_H = 0.0
SCENARIOS = ([("baseline", "US native", {})]
             + [("interval", lab, {"interval_h": x}) for lab, x in (("1 h", 1.0), ("3 h", 3.0), ("daily 08:00 IST", 24.0))]
             + [("delay", f"{d:g} h", {"delay_h": d}) for d in (1.0, 6.0, 24.0)]
             + [("gaps", f"{int(g * 100)} %", {"gap_frac": g}) for g in (0.10, 0.30)]
             + [("thresholds", f"p{p:g}", {"thr_pct": p}) for p in (99.0, 99.5, 99.9)])
COLUMNS = ["scenario", "level", "model", "crossings", "detected", "recall", "alerts", "false_alarms",
           "false_alarms_per_gauge_year", "precision", "f1", "median_lead_h", "gauge_years"]


# ---------------- degradations (pure) ----------------

def subsample(readings: list[dict], interval_h: float, offset_s: float = 0.0) -> list[dict]:
    """Keep one reading per interval: the first reading at/after each grid point; daily (24 h) keeps the reading
    nearest to the daily grid time (offset_s after midnight UTC, e.g. 02:30 UTC = 08:00 IST)."""
    if not readings:
        return []
    step = interval_h * 3600
    t = np.array([r["reading_ts"] for r in readings])
    if interval_h >= 24:
        days = np.arange(np.floor((t[0] - offset_s) / 86400) * 86400 + offset_s, t[-1] + 1, 86400)
        idx = np.searchsorted(t, days)
        out = []
        for d, i in zip(days, idx):
            cand = [k for k in (i - 1, i) if 0 <= k < len(t) and abs(t[k] - d) <= 3 * 3600]
            if cand:
                out.append(readings[min(cand, key=lambda k: abs(t[k] - d))])
        return out
    grid = np.arange(np.ceil(t[0] / step) * step, t[-1] + 1, step)
    idx = np.unique(np.searchsorted(t, grid))
    return [readings[i] for i in idx if i < len(readings)]


def remove_gaps(readings: list[dict], frac: float, seed: int = 0, min_h: float = 6, max_h: float = 48) -> list[dict]:
    """Remove random blocks (min_h..max_h hours) until about `frac` of the time span is without readings."""
    if not readings or frac <= 0:
        return list(readings)
    rng = np.random.default_rng(seed)
    t = np.array([r["reading_ts"] for r in readings])
    span = t[-1] - t[0]
    keep = np.ones(len(t), bool)
    removed = 0.0
    while removed < frac * span:
        L = rng.uniform(min_h, max_h) * 3600
        a = rng.uniform(t[0], t[-1] - L)
        m = (t >= a) & (t < a + L)
        if not m.any():
            continue
        newly = m & keep
        keep &= ~m
        removed += L if newly.any() else 0.0
    return [r for r, k in zip(readings, keep) if k]


# ---------------- scoring ----------------

def score(alerts: list[float], crossings: list[float], h: float = H) -> dict:
    from ml import evaluate
    r = evaluate.score_alerts([(a, "minor") for a in alerts], [(c, "minor") for c in crossings], h)
    return {"alerts": r["alerts"], "tp": r["true_positive_alerts"], "fa": r["false_alarms"], "det": r["detected"],
            "n": r["crossings"], "leads": r["lead_times_h"]}


def run_gauge(args) -> list[dict]:
    """All scenarios for one gauge (worker process). Returns per-scenario, per-model raw counts."""
    site, lid, cats, thr_hist, model_path = args
    import joblib
    from common import config, hydro
    from ml.backtest import windows_from_readings
    from ml.dataset import build_site_columns, primary_series
    from ml.s3_model import fast_alerts, lag_change
    from producers.replay_producer import load_readings
    config.DATA_DIR = os.environ.get("DATA_DIR", "data")
    m = joblib.load(model_path)
    rs = primary_series(load_readings(site, START, END))
    if len(rs) < 100:
        return []
    ser = [(r["reading_ts"], r["value"]) for r in rs]
    truth = [ser[i][0] for i in hydro.find_crossings(ser, cats["minor"])]
    years = (ser[-1][0] - ser[0][0]) / (365.25 * 86400)
    out = []
    cache = {}
    for scen, level, p in SCENARIOS:
        key = (p.get("interval_h"), p.get("gap_frac"))
        if key not in cache:
            x = rs
            if p.get("interval_h"):
                x = subsample(x, p["interval_h"], offset_s=2.5 * 3600 if p["interval_h"] >= 24 else 0.0)
            if p.get("gap_frac"):
                x = remove_gaps(x, p["gap_frac"], seed=int(site) % 9973)
            cache[key] = (x, windows_from_readings(x, 3600))
        x, aggs = cache[key]
        c = dict(cats)
        if p.get("thr_pct"):
            c["minor"] = c["action"] = float(thr_hist[str(p["thr_pct"])])
        delay = p.get("delay_h", BASE_DELAY_H)
        wins = [a for a in aggs if a["stage_last"] is not None]
        if not wins:
            continue
        t = np.array([a["window_start_ts"] + 3600 for a in wins], dtype=float)
        cur = np.array([a["stage_last"] for a in wins], dtype=float)
        rr = np.array([a["rise_rate_ft_per_h"] or 0.0 for a in wins], dtype=float).clip(min=0)
        al_rr = [a + delay * 3600 for a, _ in fast_alerts(t, cur, c["minor"], cur + rr * H >= c["minor"])]
        col = build_site_columns(x, c, wins=aggs)
        import pandas as pd
        X = pd.DataFrame({f: (col[f] if f in col else lag_change(col["t"], col["stage"], 12 if f == "d12h" else 24))
                          for f in m["features"]})
        pr = m["clf"].predict_proba(X)[:, 1]
        al_v0 = [a + delay * 3600 for a, _ in fast_alerts(col["t"], col["stage"], c["minor"], pr >= m["tau"])]
        for name, al in (("rise_rate", al_rr), ("s3_v0", al_v0)):
            s = score(al, truth)
            out.append({"scenario": scen, "level": level, "model": name, "site_no": site, "years": years, **s})
    return out


def pooled(rows: list[dict]) -> list[dict]:
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "det": 0, "alerts": 0, "tp": 0, "fa": 0, "leads": [], "years": 0.0})
    order = []
    for r in rows:
        k = (r["scenario"], r["level"], r["model"])
        if k not in agg:
            order.append(k)
        a = agg[k]
        for f in ("n", "det", "alerts", "tp", "fa", "years"):
            a[f] += r[f]
        a["leads"] += r["leads"]
    out = []
    for k in order:
        a = agg[k]
        p = a["tp"] / a["alerts"] if a["alerts"] else None
        rc = a["det"] / a["n"] if a["n"] else None
        f1 = (2 * p * rc / (p + rc)) if p is not None and rc is not None and p + rc > 0 else (0.0 if a["alerts"] else None)
        out.append({"scenario": k[0], "level": k[1], "model": k[2], "crossings": a["n"], "detected": a["det"],
                    "recall": round(rc, 3) if rc is not None else None, "alerts": a["alerts"],
                    "false_alarms": a["fa"], "false_alarms_per_gauge_year": round(a["fa"] / a["years"], 2),
                    "precision": round(p, 3) if p is not None else None,
                    "f1": round(f1, 3) if f1 is not None else None,
                    "median_lead_h": round(median(a["leads"]), 2) if a["leads"] else None,
                    "gauge_years": round(a["years"], 1)})
    return out


def hist_thresholds(gauges_csv: str) -> dict:
    """Per gauge: 99.0 / 99.5 / 99.9th percentile of the hourly stage 2015-2023 (from the S3 frame)."""
    import pandas as pd
    from ml.s3_model import FRAME, ts
    f = pd.read_parquet(FRAME, columns=["site_no", "t", "stage"])
    f = f[f.t < ts("2024-01-01")]
    return {s: {str(p): float(np.nanpercentile(g.stage, p)) for p in (99.0, 99.5, 99.9)} for s, g in f.groupby("site_no")}


def cmd_run(a):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ml.progress import Timer, bar
    timer = Timer()
    with open(a.gauges, encoding="utf-8") as f:
        gs = list(csv.DictReader(f))
    th = hist_thresholds(a.gauges)
    jobs = [(g["site_no"], g["lid"], {k: float(g[k]) for k in ("action", "minor", "moderate", "major")},
             th.get(g["site_no"], {}), a.model) for g in gs]
    print(f"india.degrade: {len(jobs)} gauges x {len(SCENARIOS)} scenarios, {START}..{END}, models rise_rate and "
          f"s3_v0 ({a.model}) at {H} h -> {a.out}")
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_gauge, j) for j in jobs]
        for fu in bar(as_completed(futs), "gauges", total=len(futs)):
            rows += fu.result()
    res = pooled(rows)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(res)
    with open(a.out.replace(".csv", "_per_gauge.json"), "w") as f:
        json.dump([{k: v for k, v in r.items() if k != "leads"} | {"leads": r["leads"]} for r in rows], f)
    for r in res:
        print(f"  {r['scenario']:10s} {r['level']:16s} {r['model']:9s} recall {r['recall']} FA/gauge-yr "
              f"{r['false_alarms_per_gauge_year']} F1 {r['f1']} lead {r['median_lead_h']}")
    print(f"done: {len(res)} rows -> {a.out} in {timer.elapsed()}")


def cmd_plot(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    d = pd.read_csv(a.csv)
    base = d[d.scenario == "baseline"]
    # reference palette slots 1 and 3 (validated with the dataviz validator); line style + direct labels as relief
    col = {"rise_rate": ("#1baf7a", "--", "rise-rate rule"), "s3_v0": ("#2a78d6", "-", "s3_v0 (active, 3 h)")}
    INK, MUTED, GRID, SURF = "#1f1f1e", "#6b6a64", "#e4e3de", "#fcfcfb"
    panels = [("interval", ["US native", "1 h", "3 h", "daily 08:00 IST"], "reading interval"),
              ("delay", ["0 h", "1 h", "6 h", "24 h"], "feed delay"),
              ("gaps", ["0 %", "10 %", "30 %"], "readings missing"),
              ("thresholds", ["official", "p99.9", "p99.5", "p99"], "minor stage used")]
    metrics = [("recall", "share of floods detected", (0, 1)), ("false_alarms_per_gauge_year",
               "false alarms per gauge-year", None), ("median_lead_h", "median lead after delay (h)", None)]
    plt.rcParams.update({"font.size": 8.5, "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                         "axes.labelcolor": INK})
    fig, axes = plt.subplots(3, 4, figsize=(12, 7.4), facecolor=SURF, sharey="row")
    for j, (scen, levels, xlabel) in enumerate(panels):
        for i, (met, ylab, ylim) in enumerate(metrics):
            ax = axes[i, j]
            ax.set_facecolor(SURF)
            for model, (c, ls, lab) in col.items():
                ys = []
                for lv in levels:
                    if (scen == "delay" and lv == "0 h") or (scen == "gaps" and lv == "0 %") or \
                       (scen == "thresholds" and lv == "official") or (scen == "interval" and lv == "US native"):
                        row = base[base.model == model]
                    else:
                        lvq = {"p99.9": "p99.9", "p99.5": "p99.5", "p99": "p99"}.get(lv, lv)
                        row = d[(d.scenario == scen) & (d.level == lvq) & (d.model == model)]
                    ys.append(float(row[met].iloc[0]) if len(row) and pd.notna(row[met].iloc[0]) else np.nan)
                ax.plot(range(len(levels)), ys, color=c, ls=ls, lw=2, marker="o", ms=5)
            ax.set_xticks(range(len(levels)))
            ax.set_xticklabels(levels, fontsize=7.5)
            ax.grid(color=GRID, lw=0.7)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
            if ylim:
                ax.set_ylim(*ylim)
            if j == 0:
                ax.set_ylabel(ylab)
            if i == 0:
                ax.set_title(xlabel, loc="left", color=INK, fontsize=9.5)
    from matplotlib.lines import Line2D
    fig.legend(handles=[Line2D([], [], color=c, ls=ls, lw=2, marker="o", label=lab) for c, ls, lab in col.values()],
               loc="upper center", ncol=2, frameon=False, labelcolor=INK)
    n = int(base.crossings.iloc[0])
    fig.text(0.01, 0.01, f"Real USGS readings, 38 gauges, {START} to {END} ({n} minor crossings in the undegraded "
             "data = truth), 3 h horizon, one property degraded at a time, others at the baseline (no delay, as the harness). "
             "Replayed data (SIMULATION).\nWhere detection is low, the lead-time medians rest on 1 to 3 detected floods "
             "(counts in india/figures/degradation.csv); s3_v0 raises no alerts on hourly or coarser readings.",
             fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(a.out, dpi=200, facecolor=SURF)
    print(f"done -> {a.out}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="run", choices=["run", "plot"])
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--model", default="ml/models/s3_v0_h3.joblib")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--csv", default="india/figures/degradation.csv")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    if a.cmd == "run":
        a.out = a.out or "india/figures/degradation.csv"
        cmd_run(a)
    else:
        a.out = a.out or "india/figures/degradation.png"
        cmd_plot(a)


if __name__ == "__main__":
    main()
