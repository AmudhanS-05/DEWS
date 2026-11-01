"""I3.1 / INDIA_STUDY step B: Indian data through the pipeline -> failure log (Table 2). One command once the
India-WRIS data exists (Excel downloads or `python -m india.indiawris_api --fetch` output under data/raw/indiawris/):

    python -m india.replay_india data/raw/indiawris [--thresholds india/thresholds.csv] [--kafka]

What it does, per station:
  1. loads the exports with india/indiawris_loader.py (both layouts) and the CWC levels from india/thresholds.csv
     (warning -> action, danger -> minor; official CWC levels only, nothing estimated);
  2. writes the replay messages (producers/tracking.make_envelope, mode SIMULATION, source INDIA-WRIS) to
     data/replay/india_wris.jsonl, oldest first; with --kafka also publishes them to replay.gauge (core's producer);
  3. runs the same hourly windowing as the stream (streams/windowing.py via ml.backtest) and the 3 h rise-rate rule
     with the harness alert rule, and scores alerts against crossings of the danger level;
  4. writes Table 2 (india/figures/table2_failure_log.csv and .md): one row per pipeline stage with what happened and
     the measured number. The trained US forecaster (s3_v0) is NOT applied: it was trained on US gauges in feet with
     NWPS categories, so its output on Indian stations would not mean anything.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from statistics import median

import numpy as np

STAGES = ["data access", "replay producer", "hourly windows", "official thresholds", "events (danger crossings)",
          "rise time resolvable", "forecaster (rise-rate, 3 h)", "alerts and lead time"]


def station_report(code: str, readings: list[dict], th: dict | None, h: float = 3.0, delay_h: float = 24.0) -> dict:
    """Measured facts for one station. delay_h: ASSUMED publication delay of the archive (no live India-WRIS feed was
    found and the delay was not measured; 24 h is the default assumption for the 'net' lead, stated in the table)."""
    from common import hydro
    from india.indiawris_loader import categories
    from ml import evaluate
    from ml.backtest import windows_from_readings
    rs = sorted(readings, key=lambda r: r["reading_ts"])
    ser = [(r["reading_ts"], r["value"]) for r in rs]
    gaps = np.diff([t for t, _ in ser]) / 60.0 if len(ser) > 1 else np.array([])
    aggs = windows_from_readings([{**r, "series": r.get("series", "INDIA-WRIS")} for r in rs], 3600)
    zero = sum(1 for a in aggs if not a["n_readings"])
    out = {"station": code, "readings": len(rs), "first": rs[0]["reading_time"] if rs else "",
           "last": rs[-1]["reading_time"] if rs else "", "median_interval_min": float(np.median(gaps)) if len(gaps) else None,
           "windows": len(aggs), "zero_filled_windows": zero,
           "zero_filled_share": round(zero / len(aggs), 3) if aggs else None,
           "method": (rs[0].get("method") or "") if rs else "", "danger_level_m": None, "warning_level_m": None,
           "crossings": None, "median_rise_h": None, "alerts": None, "detected": None, "false_alarms": None,
           "median_lead_h": None, "median_lead_net_h": None}
    cats = categories(th) if th else None
    if not cats or cats.get("minor") is None:
        return out
    out["danger_level_m"], out["warning_level_m"] = cats["minor"], cats["action"]
    idx = hydro.find_crossings(ser, cats["minor"])
    out["crossings"] = len(idx)
    if idx:
        out["median_rise_h"] = round(median(hydro.rise_time_h(ser, i) for i in idx), 2)
    wins = [a for a in aggs if a["stage_last"] is not None]
    alerts, last = [], -1e18
    for a in wins:
        cur, rr = a["stage_last"], max(a["rise_rate_ft_per_h"] or 0.0, 0.0)     # m per h for Indian data
        t = a["window_start_ts"] + 3600
        if cur < cats["minor"] <= cur + rr * h and t - last >= 6 * 3600:
            alerts.append((t, "minor"))
            last = t
    r = evaluate.score_alerts(alerts, [(ser[i][0], "minor") for i in idx], h)
    out.update(alerts=r["alerts"], detected=r["detected"], false_alarms=r["false_alarms"])
    if r["lead_times_h"]:
        out["median_lead_h"] = round(median(r["lead_times_h"]), 2)
        out["median_lead_net_h"] = round(median(r["lead_times_h"]) - delay_h, 2)
    return out


def failure_log(reports: list[dict], delay_h: float = 24.0) -> list[dict]:
    """Table 2: one row per pipeline stage; every number is measured from `reports` (or says it is missing)."""
    n = len(reports)

    def agg(key, fn=sum):
        v = [r[key] for r in reports if r.get(key) is not None]
        return fn(v) if v else None
    with_th = [r for r in reports if r["danger_level_m"] is not None]
    manual = sum("MANUAL" in str(r["method"]).upper() for r in reports)
    med_int = agg("median_interval_min", lambda v: median(v))
    zshare = agg("zero_filled_share", lambda v: median(v))
    rises = [r["median_rise_h"] for r in reports if r["median_rise_h"] is not None]
    leads = [r["median_lead_h"] for r in reports if r["median_lead_h"] is not None]
    rows = [
        {"stage": "data access", "what_happened": "no live feed; archive by manual download or the POST API from an "
         "Indian connection", "measured": f"{n} stations loaded; {manual} of {n} marked manual staff gauge"},
        {"stage": "replay producer", "what_happened": "readings converted to replay.gauge envelopes "
         "(mode SIMULATION, source INDIA-WRIS)", "measured": f"{agg('readings')} readings"},
        {"stage": "hourly windows", "what_happened": "stream windowing runs unchanged; hours without a reading are "
         "zero-filled (stage carried forward)", "measured": f"median reading interval {med_int} min; median "
         f"{zshare:.0%} of hourly windows zero-filled" if zshare is not None else "no windows"},
        {"stage": "official thresholds", "what_happened": "CWC warning/danger levels copied by hand from bulletins "
         "into india/thresholds.csv (no machine-readable source found)",
         "measured": f"{len(with_th)} of {n} stations with a danger level"},
        {"stage": "events (danger crossings)", "what_happened": "crossings of the CWC danger level",
         "measured": f"{agg('crossings')} crossings" if with_th else "not measurable without thresholds"},
        {"stage": "rise time resolvable", "what_happened": "rise time vs reading interval",
         "measured": (f"median rise {median(rises)} h vs interval {med_int / 60:.1f} h" if rises and med_int else
                      "no crossings to measure")},
        {"stage": "forecaster (rise-rate, 3 h)", "what_happened": "rise rate from zero-filled windows; the US model "
         "(s3_v0) is not applied (trained on US gauges in feet)", "measured": f"{agg('alerts')} alerts, "
         f"{agg('false_alarms')} false" if with_th else "not run without thresholds"},
        {"stage": "alerts and lead time", "what_happened": f"lead time gross and net of an assumed {delay_h:g} h "
         "publication delay (archive data; delay not measured)", "measured": (f"{agg('detected')} of {agg('crossings')} "
         f"detected; median lead {median(leads)} h gross, {median(leads) - delay_h:.2f} h net" if leads else
         (f"{agg('detected')} of {agg('crossings')} detected" if with_th else "not measurable"))},
    ]
    return rows


def write_table2(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["stage", "what_happened", "measured"])
        w.writeheader()
        w.writerows(rows)
    with open(path.replace(".csv", ".md"), "w", encoding="utf-8") as f:
        f.write("| Pipeline stage | What happened with India-WRIS data | Measured |\n|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['stage']} | {r['what_happened']} | {r['measured']} |\n")


def main(argv=None):
    from india import indiawris_loader as L
    from ml.progress import Timer
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?", default=os.path.join(os.getenv("DATA_DIR", "data"), "raw", "indiawris"))
    p.add_argument("--thresholds", default="india/thresholds.csv")
    p.add_argument("--replay-out", default=os.path.join(os.getenv("DATA_DIR", "data"), "replay", "india_wris.jsonl"))
    p.add_argument("--out", default="india/figures/table2_failure_log.csv")
    p.add_argument("--delay-h", type=float, default=24.0, help="assumed publication delay for the net lead time")
    p.add_argument("--kafka", action="store_true", help="also publish the envelopes to replay.gauge")
    a = p.parse_args(argv)
    timer = Timer()
    print(f"india.replay_india: {a.folder} -> {a.replay_out}, Table 2 -> {a.out}")
    data = L.load_folder(a.folder)
    if not data:
        raise SystemExit(f"ERROR: no India-WRIS readings under {a.folder} (download or run india.indiawris_api --fetch)")
    th = L.load_thresholds(a.thresholds) if os.path.exists(a.thresholds) else {}
    os.makedirs(os.path.dirname(a.replay_out) or ".", exist_ok=True)
    envs = []
    for code, rs in data.items():
        envs += L.to_envelopes(rs)
    envs.sort(key=lambda e: e["reading_ts"])
    with open(a.replay_out, "w", encoding="utf-8") as f:
        for e in envs:
            f.write(json.dumps(e) + "\n")
    if a.kafka:
        from common import config
        from common.kafka_io import JsonProducer
        prod = JsonProducer("india-replay")
        for e in envs:
            prod.send(config.TOPIC_REPLAY_GAUGE, e["site_no"], e)
        prod.flush(60)
        print(f"  published {prod.sent} envelopes to {config.TOPIC_REPLAY_GAUGE} (failed {prod.failed})")
    reports = []
    for code, rs in sorted(data.items()):
        r = station_report(code, rs, th.get(code), delay_h=a.delay_h)
        reports.append(r)
        print(f"  {code}: {r['readings']} readings, interval {r['median_interval_min']} min, zero-filled windows "
              f"{r['zero_filled_share']}, danger level {r['danger_level_m']}, crossings {r['crossings']}, "
              f"alerts {r['alerts']}, detected {r['detected']}")
    write_table2(failure_log(reports, a.delay_h), a.out)
    with open(a.out.replace(".csv", "_stations.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(reports[0]))
        w.writeheader()
        w.writerows(reports)
    print(f"done: {len(envs)} envelopes (SIMULATION, INDIA-WRIS), {len(reports)} stations -> {a.out} (+ .md, "
          f"_stations.csv) in {timer.elapsed()}")


if __name__ == "__main__":
    main()
