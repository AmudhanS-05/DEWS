"""Where the old FAQ rise-rate bar came from, and how it relates to the harness (ml/REFERENCE_BASELINES.md).

Scores the 3 h rise-rate baseline on the four reference gauges with five method variants, ONE change at a time, on
the same cached readings, the same events (common.hydro.find_crossings: first reading >= minor after a reading below
it, 24 h gap; identical to notebook 02's detect_events) and the same scoring (ml.evaluate.score_alerts, 6 h cooldown):

  A nb_mean_startlabel  notebook 02 as run: hourly MEAN, pandas default label = START of the hour (the value at 06:00
                        averages 06:00-06:59, so an alert stamped 06:00 has seen the next hour), gaps interpolated
                        up to 3 h, rate = difference of consecutive hourly means
  B nb_mean_endlabel    as A, labelled at the END of the hour (no look-ahead)
  C last_endlabel       as B, but the hour's LAST reading instead of the mean (rate = difference of hourly lasts)
  D harness             ml/harness.py: streams/windowing.py hourly windows (last reading, in-window rise rate,
                        zero-filled hours carry the last stage forward), the evaluation protocol
Each variant is run on every --windows date range (default: the notebook's 10 years ending 2026-10-02, and the
M1.4 range 2015-01-01..2025-12-31). Writes per gauge: crossings, hourly rows, alerts, true-positive alerts, false
alarms, detected crossings, precision, recall, F1, median lead time.

    python -m ml.reference_check --out ml/figures/reference_check.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import median

import numpy as np

from common import hydro

GAUGES = {"08158700": ("DRWT2", 5.5), "08172400": ("LCPT2", 14.0), "08150000": ("JNCT2", 16.0),
          "08156800": ("AHOT2", 7.0)}
OLD_FAQ_F1 = {"DRWT2": 0.65, "LCPT2": 0.26, "JNCT2": 0.64, "AHOT2": 0.41}
VARIANTS = ("A_nb_mean_startlabel", "B_nb_mean_endlabel", "C_last_endlabel", "D_harness")
COLUMNS = ["window", "variant", "lid", "site_no", "crossings", "hourly_rows", "alerts", "tp_alerts", "false_alarms",
           "detected", "precision", "recall", "f1", "median_lead_h"]


def pandas_alerts(times: np.ndarray, vals: np.ndarray, thr: float, how: str, label: str, horizon_h: float = 3.0,
                  cooldown_h: float = 6.0) -> tuple[list[tuple[float, str]], int]:
    """Notebook-02 style alerts from an hourly resample. Returns ([(t, 'minor')], number of hourly rows)."""
    import pandas as pd
    v = pd.Series(vals, index=pd.to_datetime(times, unit="s"))
    r = v.resample("h", label=label, closed="left")
    h = (r.mean() if how == "mean" else r.last()).interpolate(limit=3).dropna()
    fc = h + h.diff().clip(lower=0) * horizon_h
    alerts, last = [], None
    for t, cur, f in zip(h.index, h.to_numpy(), fc.to_numpy()):
        if f >= thr > cur and (last is None or t - last >= pd.Timedelta(hours=cooldown_h)):
            alerts.append((t.timestamp(), "minor"))
            last = t
    return alerts, len(h)


def score(alerts, crossings, horizon_h: float = 3.0) -> dict:
    from ml import evaluate
    r = evaluate.score_alerts(alerts, [(c, "minor") for c in crossings], horizon_h)
    return {"alerts": r["alerts"], "tp_alerts": r["true_positive_alerts"], "false_alarms": r["false_alarms"],
            "detected": r["detected"], "crossings": r["crossings"], "precision": r["precision"],
            "recall": r["recall"], "f1": r["f1"] if r["f1"] is not None else (0.0 if r["alerts"] else None),
            "median_lead_h": median(r["lead_times_h"]) if r["lead_times_h"] else None}


def run_window(readings: list[dict], thr: float, start_ts: float, end_ts: float) -> dict[str, dict]:
    from ml import baselines, harness
    from ml.dataset import primary_series
    rs = [r for r in primary_series(readings) if start_ts <= r["reading_ts"] <= end_ts]
    ser = sorted((r["reading_ts"], r["value"]) for r in rs)
    times = np.array([t for t, _ in ser])
    vals = np.array([v for _, v in ser])
    crossings = [ser[i][0] for i in hydro.find_crossings(ser, thr)]
    out = {}
    for name, how, label in (("A_nb_mean_startlabel", "mean", "left"), ("B_nb_mean_endlabel", "mean", "right"),
                             ("C_last_endlabel", "last", "right")):
        al, n = pandas_alerts(times, vals, thr, how, label)
        out[name] = {**score(al, crossings), "hourly_rows": n}
    prep = harness.prepare(rs, {"minor": thr, "action": thr, "moderate": thr, "major": thr})
    c = harness.collect(prep, baselines.rise_rate, 3.0)
    out["D_harness"] = {**score(c["alerts"], [t for t, _, _ in c["crossings"]]), "hourly_rows": len(prep["t"])}
    return out


def _f1(x) -> str:
    return "NA" if x is None else f"{x:.2f}"


def _ts(d: str, end: bool = False) -> float:
    from datetime import datetime, timezone
    t = datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()
    return t + 86399 if end else t


def main(argv=None):
    from ml.progress import Timer, bar
    from producers.replay_producer import load_readings
    p = argparse.ArgumentParser(description="Decompose the old FAQ rise-rate F1s vs the harness (3 h, 4 gauges).")
    p.add_argument("--windows", default="2016-10-03:2026-10-02,2015-01-01:2025-12-31",
                   help="comma list of START:END (UTC dates, inclusive)")
    p.add_argument("--out", default="ml/figures/reference_check.csv")
    a = p.parse_args(argv)
    timer = Timer()
    wins = [tuple(w.split(":")) for w in a.windows.split(",")]
    lo, hi = min(s for s, _ in wins), max(e for _, e in wins)
    print(f"ml.reference_check: rise-rate 3 h, 4 gauges x {len(VARIANTS)} variants x {len(wins)} windows -> {a.out}")
    rows = []
    for site, (lid, thr) in bar(GAUGES.items(), "gauges", total=len(GAUGES)):
        readings = load_readings(site, lo, hi)
        for s, e in wins:
            for v, r in run_window(readings, thr, _ts(s), _ts(e, True)).items():
                rows.append({"window": f"{s}..{e}", "variant": v, "lid": lid, "site_no": site, **r})
        del readings
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(r[k], 4) if isinstance(r[k], float) else r[k]) for k in COLUMNS})
    for win in sorted({r["window"] for r in rows}):
        print(f"\n{win}  F1 (crossings / alerts / detected)   old FAQ: " +
              " ".join(f"{k} {v}" for k, v in OLD_FAQ_F1.items()))
        for v in VARIANTS:
            cells = [r for r in rows if r["window"] == win and r["variant"] == v]
            print(f"  {v:22s} " + "  ".join(
                f"{r['lid']} {_f1(r['f1'])} "
                f"({r['crossings']}/{r['alerts']}/{r['detected']})" for r in cells))
    print(f"\ndone: {len(rows)} rows -> {a.out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
