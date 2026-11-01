"""I5.1 / INDIA_STUDY step D (as scoped on 1 Nov 2026): what an Indian station would need, and a SYNTHETIC replay that
shows the pipeline handles data with those properties.

1. Requirements (from step C, india/figures/degradation.csv, real US data): for each degraded property, the coarsest
   TESTED level whose rise-rate recall stays >= 90 % of the US baseline and whose false alarms per gauge-year stay
   <= 110 % of it. Only tested levels can be named; finer levels than tested (e.g. delays below 1 h) are not claimed.
2. Synthetic replay: one real US flood with radar rain (default BDAT2, 2025-07-04 21:30 UTC, from
   ml/figures/demo_flood_candidates.csv) rebuilt two ways and written as replay.gauge envelopes with mode SIMULATION,
   source SYNTHETIC and site_no "SYN-IND-<lid>-<variant>" (so it can never be mistaken for real Indian or US data):
     cwc_hourly    one reading per hour, what most CWC river stations reported in July 2022 (user's --list-stations)
     daily_dams    one reading per day at 08:00 IST (02:30 UTC): the dams / off-season case (3 to 24 h at dams)
     ideal_india   the requirement levels: 15-minute readings, official thresholds
   Each file is run through the stream windowing (streams/windowing.py via ml.backtest) and the 3 h rise-rate rule and
   s3_v0, and the alert lead for that flood is reported. Stage stays in feet with the gauge's NWPS levels: this is a
   real US hydrograph with Indian-like reporting, NOT Indian data and NOT evidence of skill in India.

    python -m india.ideal_india            # -> india/figures/ideal_india_requirements.{csv,md}, ideal_india_replay.csv,
                                           #    data/replay/synthetic_india_<variant>.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import os

import numpy as np

LEVEL_ORDER = {"interval": ["US native", "1 h", "3 h", "daily (dams/off-season case)"], "delay": ["0 h", "1 h", "6 h", "24 h"],
               "gaps": ["0 %", "10 %", "30 %"], "thresholds": ["official", "p99.9", "p99.5", "p99"]}
BASE_LEVEL = {"interval": "US native", "delay": "0 h", "gaps": "0 %", "thresholds": "official"}


def requirements(rows: list[dict], model: str = "rise_rate", recall_frac: float = 0.9, fa_frac: float = 1.1) -> list[dict]:
    base = next(r for r in rows if r["scenario"] == "baseline" and r["model"] == model)
    b_rec, b_fa = float(base["recall"]), float(base["false_alarms_per_gauge_year"])
    out = []
    for scen, levels in LEVEL_ORDER.items():
        ok_level, measured = BASE_LEVEL[scen], []
        for lv in levels:
            r = base if lv == BASE_LEVEL[scen] else next(
                (x for x in rows if x["scenario"] == scen and x["level"] == lv and x["model"] == model), None)
            if r is None:
                continue
            rec, fa = float(r["recall"] or 0), float(r["false_alarms_per_gauge_year"])
            measured.append(f"{lv}: recall {rec:.2f}, FA/gauge-yr {fa:.2f}")
            if rec >= recall_frac * b_rec and fa <= fa_frac * b_fa:
                ok_level = lv
            else:
                break
        out.append({"property": scen, "needed (coarsest tested level that keeps the US numbers)": ok_level,
                    "measured (rise-rate, 3 h)": "; ".join(measured)})
    return out


def variant(readings: list[dict], name: str) -> list[dict]:
    from india.degrade import subsample
    if name == "daily_dams":
        return subsample(readings, 24.0, offset_s=2.5 * 3600)
    if name == "cwc_hourly":
        return subsample(readings, 1.0)
    if name == "ideal_india":
        return subsample(readings, 0.25)
    raise ValueError(name)


def run_variant(rs: list[dict], cats: dict, crossing_ts: float, model_path: str, h: float = 3.0) -> dict:
    import joblib
    import pandas as pd
    from ml.backtest import windows_from_readings
    from ml.dataset import build_site_columns
    from ml.s3_model import fast_alerts, lag_change
    aggs = windows_from_readings(rs, 3600)
    wins = [a for a in aggs if a["stage_last"] is not None]
    t = np.array([a["window_start_ts"] + 3600 for a in wins], dtype=float)
    cur = np.array([a["stage_last"] for a in wins], dtype=float)
    rr = np.array([a["rise_rate_ft_per_h"] or 0.0 for a in wins], dtype=float).clip(min=0)
    al_rr = [a for a, _ in fast_alerts(t, cur, cats["minor"], cur + rr * h >= cats["minor"])]
    m = joblib.load(model_path)
    col = build_site_columns(rs, cats, wins=aggs)
    X = pd.DataFrame({f: (col[f] if f in col else lag_change(col["t"], col["stage"], 12 if f == "d12h" else 24))
                      for f in m["features"]})
    al_v0 = [a for a, _ in fast_alerts(col["t"], col["stage"], cats["minor"],
                                        m["clf"].predict_proba(X)[:, 1] >= m["tau"])]

    def lead(al):
        prior = [a for a in al if crossing_ts - h * 3600 <= a < crossing_ts]
        return round((crossing_ts - min(prior)) / 3600, 2) if prior else None
    return {"readings": len(rs), "windows": len(aggs), "zero_filled_windows": sum(1 for a in aggs if not a["n_readings"]),
            "riserate_alerts": len(al_rr), "riserate_lead_h": lead(al_rr), "s3_v0_alerts": len(al_v0),
            "s3_v0_lead_h": lead(al_v0)}


def main(argv=None):
    from datetime import datetime, timezone
    from common import config
    from ml.dataset import primary_series
    from ml.progress import Timer
    from producers.replay_producer import load_readings
    from producers.tracking import make_envelope
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--degradation", default="india/figures/degradation.csv")
    p.add_argument("--lid", default="BDAT2")
    p.add_argument("--crossing", default="2025-07-04 21:30", help="UTC, from ml/figures/demo_flood_candidates.csv")
    p.add_argument("--days", type=float, default=14, help="history before the crossing in the replay")
    p.add_argument("--model", default="ml/models/s3_v0_h3.joblib")
    p.add_argument("--replay-dir", default=os.path.join(os.getenv("DATA_DIR", "data"), "replay"))
    a = p.parse_args(argv)
    timer = Timer()
    rows = list(csv.DictReader(open(a.degradation, encoding="utf-8")))
    req = requirements(rows)
    with open("india/figures/ideal_india_requirements.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(req[0]))
        w.writeheader()
        w.writerows(req)
    with open("india/figures/ideal_india_requirements.md", "w", encoding="utf-8") as f:
        f.write("| Property | Needed | Measured (rise-rate, 3 h, real US data, step C) |\n|---|---|---|\n")
        for r in req:
            f.write("| " + " | ".join(str(v) for v in r.values()) + " |\n")
    for r in req:
        print(f"  {r['property']:10s} needed: {list(r.values())[1]}")
    g = next(x for x in csv.DictReader(open("config/gauge_locations.csv", encoding="utf-8")) if x["lid"] == a.lid)
    cats = {k: float(g[k]) for k in ("action", "minor", "moderate", "major")}
    ct = datetime.strptime(a.crossing, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    d0 = datetime.fromtimestamp(ct - a.days * 86400, timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(ct + 2 * 86400, timezone.utc).strftime("%Y-%m-%d")
    rs = primary_series(load_readings(g["site_no"], d0, d1))
    os.makedirs(a.replay_dir, exist_ok=True)
    out = []
    for name in ("ideal_india", "cwc_hourly", "daily_dams"):
        vr = variant(rs, name)
        sid = f"SYN-IND-{a.lid}-{name}"
        syn = [{**r, "site_no": sid, "site_name": f"SYNTHETIC from {a.lid} ({name})", "series": "SYNTHETIC"} for r in vr]
        path = os.path.join(a.replay_dir, f"synthetic_india_{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in syn:
                f.write(json.dumps(make_envelope(r, r["reading_ts"], "SIMULATION", source="SYNTHETIC")) + "\n")
        res = run_variant(syn, cats, ct, a.model)
        out.append({"variant": name, "site_no": sid, "replay_file": path, **res})
        print(f"  {name:12s} {res}")
    with open("india/figures/ideal_india_replay.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"done: requirements -> india/figures/ideal_india_requirements.md; synthetic replays ({a.lid} "
          f"{a.crossing} UTC) -> {a.replay_dir}, summary india/figures/ideal_india_replay.csv in {timer.elapsed()}")


if __name__ == "__main__":
    main()
