"""S3 (user-approved): sensor-fault detector on hourly window features (Isolation Forest).

Flags windows whose readings look like a sensor problem (spike, stuck / flat-lined sensor, step offset, dropout,
slow drift), not floods. It uses only `aggregates.window` fields (streams/windowing.py), so the same function serves
training and the live stream:  score_window(features) -> {anomaly_score, suspect, reason}
where `features` is the latest aggregates.window record plus `history` (earlier windows, oldest first), exactly what
streams/forecaster.py already passes to ml.predict.

Features (stage changes divided by the gauge's own scale s = 99th percentile of |hourly change| in training, so one
pooled model fits every gauge). Raw rise size is deliberately NOT a feature: a fast genuine rise must not look
anomalous by its size alone; the features describe fault SHAPES:
  excursion       how far the window's max/min went beyond both its own last value and the previous last, / s
                  (a one-reading spike leaves and returns within the hour; a flood rise is sustained, so ~0)
  reversal        min(|d1|, |d1_prev|) / s when consecutive hourly changes have opposite signs (up-then-down)
  jump_then_flat  |previous change| minus the changes around it, / s (a step: one jump, flat before and after;
                  a flood keeps rising after its first big hour)
  flat_h          consecutive windows with readings whose stage did not change at all, / 24 (stuck sensor)
  n_ratio         n_readings / the gauge's usual readings per hour
  gap_h           consecutive zero-filled windows (no readings), / 6 (dropout)
  creep           |24 h change| / (6 s), divided by (1 + largest |hourly change| in those 24 h / s): slow, smooth
                  change without the hourly jumps of a flood (drift)
Plus one rule, chosen on the 2021-2023 validation years: no readings for >= GAP_RULE_H hours -> suspect, reason
"no_readings" (validation: 1.2 such gaps per gauge-year, none in the 39 flood windows). Isolation Forest alone
cannot flag gaps or flat runs because both are common in normal data (flat runs >= 24 h occur ~10 times per
gauge-year at low flow, and once within 12 h of a real crossing), so a stuck sensor at low flow is NOT detectable
from stage alone; it would need a second signal (discharge, rain, a neighbouring gauge).
`reason` names the feature furthest beyond its training 99.9th percentile (or "pattern" if none is).
Threshold: the anomaly score's 99.9th percentile on clean training windows (about 9 flags per gauge-year by
construction on data like the training data).

    python -m ml.anomaly train                 # fit on 2015-2023 windows -> ml/models/anomaly_if.joblib
    python -m ml.anomaly evaluate              # inject faults into 2024-2026 real series; rates -> ml/figures/
"""
from __future__ import annotations

import os

import numpy as np

FEATURES = ["excursion", "reversal", "jump_then_flat", "flat_h", "n_ratio", "gap_h", "creep"]
MODEL_PATH = os.getenv("ANOMALY_MODEL_PATH", "ml/models/anomaly_if.joblib")
TRAIN_END = "2024-01-01"
GAP_RULE_H = 3
FAULTS = ("spike", "flatline", "step", "dropout", "drift")


def window_arrays(aggs: list[dict]) -> dict[str, np.ndarray]:
    nan = np.nan
    g = lambda k: np.array([nan if a.get(k) is None else float(a[k]) for a in aggs], dtype=float)
    return {"t": np.array([float(a["window_start_ts"]) for a in aggs]), "last": g("stage_last"), "max": g("stage_max"),
            "min": g("stage_min"), "n": np.array([float(a.get("n_readings") or 0) for a in aggs])}


def features_from_arrays(w: dict[str, np.ndarray], scale: float, n_usual: float) -> np.ndarray:
    """Feature matrix (len(windows) x len(FEATURES)); row i uses windows <= i only (causal)."""
    last, mx, mn, n = w["last"], w["max"], w["min"], w["n"]
    s = max(scale, 1e-3)
    prev = np.concatenate([[np.nan], last[:-1]])
    d1 = np.nan_to_num((last - prev) / s, nan=0.0)
    d1p = np.concatenate([[0.0], d1[:-1]])
    d1pp = np.concatenate([[0.0, 0.0], d1[:-2]])[:len(d1)]
    rev = np.where(np.sign(d1) * np.sign(d1p) < 0, np.minimum(np.abs(d1), np.abs(d1p)), 0.0)
    has = n > 0
    hi = np.where(has, mx - np.fmax(last, prev), 0.0)
    lo = np.where(has, np.fmin(last, prev) - mn, 0.0)
    exc = np.fmax(np.nan_to_num(hi, nan=0.0), np.nan_to_num(lo, nan=0.0)).clip(min=0) / s
    jtf = (np.abs(d1p) - np.abs(d1) - np.abs(d1pp)).clip(min=0)
    flat = np.zeros(len(last))
    gap = np.zeros(len(last))
    run_f = run_g = 0
    for i in range(len(last)):
        same = has[i] and (mx[i] - mn[i] == 0) and (i > 0 and last[i] == last[i - 1])
        run_f = run_f + 1 if same else 0
        run_g = run_g + 1 if not has[i] else 0
        flat[i], gap[i] = run_f, run_g
    lag = np.searchsorted(w["t"], w["t"] - 24 * 3600)
    lc = np.minimum(lag, len(last) - 1)
    hit = w["t"][lc] == w["t"] - 24 * 3600
    d24 = np.abs(np.where(hit, last - last[lc], 0.0)) / (6 * s)
    from numpy.lib.stride_tricks import sliding_window_view
    big = sliding_window_view(np.concatenate([np.zeros(23), np.abs(d1)]), 24).max(axis=1)
    creep = np.nan_to_num(d24, nan=0.0) / (1 + big)
    X = np.column_stack([exc, rev, jtf, np.minimum(flat, 48) / 24, n / max(n_usual, 1.0), np.minimum(gap, 48) / 6,
                         creep])
    return np.nan_to_num(X, nan=0.0)


def gauge_scale(aggs: list[dict]) -> tuple[float, float]:
    """(s = 99th percentile of |hourly change|, usual readings per hour) from clean history."""
    w = window_arrays(aggs)
    d = np.abs(np.diff(w["last"]))
    d = d[np.isfinite(d) & (d > 0)]
    n = w["n"][w["n"] > 0]
    return (float(np.percentile(d, 99)) if len(d) else 0.1), (float(np.median(n)) if len(n) else 4.0)


# ---------------- live scoring (contract) ----------------

_MODEL = None


def _load():
    global _MODEL
    if _MODEL is None:
        import joblib
        _MODEL = joblib.load(MODEL_PATH)
    return _MODEL


def score_window(features: dict, model: dict | None = None) -> dict:
    """{anomaly_score (higher = more unusual), suspect (bool), reason}. Never raises."""
    try:
        m = model or _load()
        aggs = list(features.get("history") or []) + [features]
        aggs = [a for a in aggs if a.get("window_start_ts") is not None]
        site = str(features.get("site_no", ""))
        scale, n_usual = m["scales"].get(site, (m["default_scale"], m["default_n"]))
        X = features_from_arrays(window_arrays(aggs[-25:]), scale, n_usual)[-1:]
        sc = float(-m["iforest"].score_samples(X)[0])
        suspect = sc >= m["threshold"]
        reason = ""
        if X[0, FEATURES.index("gap_h")] * 6 >= GAP_RULE_H:
            return {"anomaly_score": sc, "suspect": True, "reason": "no_readings"}
        if suspect:
            z = np.abs(X[0]) / np.maximum(m["q999"], 1e-6)
            k = int(np.argmax(z))
            reason = FEATURES[k] if z[k] > 1 else "pattern"
        return {"anomaly_score": sc, "suspect": bool(suspect), "reason": reason}
    except Exception:  # noqa: BLE001 - the stream must never stop on the detector
        return {"anomaly_score": None, "suspect": False, "reason": "unavailable"}


def score_series(aggs: list[dict], model: dict, site: str) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised scores and flags for a whole window sequence of one gauge (same features as score_window)."""
    scale, n_usual = model["scales"].get(site, (model["default_scale"], model["default_n"]))
    X = features_from_arrays(window_arrays(aggs), scale, n_usual)
    sc = -model["iforest"].score_samples(X)
    return sc, (sc >= model["threshold"]) | (X[:, FEATURES.index("gap_h")] * 6 >= GAP_RULE_H)


# ---------------- fault injection (evaluation) ----------------

def inject(readings: list[dict], kind: str, t0: float, scale: float, rng) -> tuple[list[dict], float]:
    """Copy of `readings` with one fault starting at t0. Returns (readings, fault end time)."""
    out = [dict(r) for r in readings]
    amp = max(3 * scale, 0.5)
    if kind == "spike":
        i = next(k for k, r in enumerate(out) if r["reading_ts"] >= t0)
        out[i]["value"] += amp * rng.choice([-1, 1])
        return out, out[i]["reading_ts"]
    if kind == "flatline":
        t1 = t0 + 24 * 3600
        v0 = next(r["value"] for r in out if r["reading_ts"] >= t0)
        for r in out:
            if t0 <= r["reading_ts"] < t1:
                r["value"] = v0
        return out, t1
    if kind == "step":
        t1 = t0 + 48 * 3600
        off = max(2 * scale, 0.3) * rng.choice([-1, 1])
        for r in out:
            if t0 <= r["reading_ts"] < t1:
                r["value"] += off
        return out, t1
    if kind == "dropout":
        t1 = t0 + 6 * 3600
        return [r for r in out if not (t0 <= r["reading_ts"] < t1)], t1
    if kind == "drift":
        t1 = t0 + 72 * 3600
        sgn = rng.choice([-1, 1])
        for r in out:
            if t0 <= r["reading_ts"] < t1:
                r["value"] += sgn * max(6 * scale, 0.5) * (r["reading_ts"] - t0) / (t1 - t0)
        return out, t1
    raise ValueError(kind)


# ---------------- CLI ----------------

def _ts(d: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()


def cmd_train(a):
    import joblib
    from sklearn.ensemble import IsolationForest
    from common import config
    from ml.backtest import windows_from_readings
    from ml.dataset import primary_series
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    timer = Timer()
    print(f"ml.anomaly train: windows {a.start}..{a.train_end} (exclusive), all gauges -> {MODEL_PATH}")
    rng = np.random.default_rng(0)
    Xs, scales = [], {}
    from datetime import date, timedelta
    last_day = (date.fromisoformat(a.train_end) - timedelta(days=1)).isoformat()
    for g in bar(iter_gauges(a.gauges, None, a.start, last_day, config.DATA_DIR), "gauges",
                 total=count_gauges(a.gauges, None)):
        aggs = windows_from_readings(primary_series(g["readings"]), 3600)
        del g["readings"]
        sc = gauge_scale(aggs)
        scales[g["site_no"]] = sc
        X = features_from_arrays(window_arrays(aggs), *sc)
        Xs.append(X[rng.random(len(X)) < a.sample])
    X = np.vstack(Xs)
    iso = IsolationForest(n_estimators=200, max_samples=4096, random_state=0, n_jobs=-1).fit(X)
    s = -iso.score_samples(X)
    thr = float(np.quantile(s, 1 - a.flag_rate))
    m = {"iforest": iso, "threshold": thr, "scales": scales,
         "default_scale": float(np.median([v[0] for v in scales.values()])),
         "default_n": float(np.median([v[1] for v in scales.values()])), "features": FEATURES,
         "q999": np.quantile(np.abs(X), 0.999, axis=0), "flag_rate": a.flag_rate, "trained_on": f"{a.start} <= t < {a.train_end}"}
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(m, MODEL_PATH, compress=3)
    print(f"done: {len(X)} training windows ({a.sample:.0%} sample), threshold {thr:.4f} (top {a.flag_rate:.2%}), "
          f"{os.path.getsize(MODEL_PATH) / 1e6:.2f} MB in {timer.elapsed()}")


def cmd_evaluate(a):
    import csv
    import joblib
    from common import config, hydro
    from ml.backtest import windows_from_readings
    from ml.dataset import primary_series
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    timer = Timer()
    m = joblib.load(MODEL_PATH)
    rng = np.random.default_rng(1)
    print(f"ml.anomaly evaluate: model {MODEL_PATH}; {a.per_gauge} faults per type per gauge injected into "
          f"{a.start}..{a.end}; "
          f"types {FAULTS}")
    res = {k: {"n": 0, "detected": 0, "ttf": []} for k in FAULTS}
    clean = {"windows": 0, "flags": 0, "episodes": 0, "episodes_if": 0, "episodes_gap": 0, "gauge_years": 0.0,
             "flood_windows": 0, "flood_flags": 0}
    by_reason, per_gauge = {}, []
    for g in bar(iter_gauges(a.gauges, None, a.start, a.end, config.DATA_DIR), "gauges",
                 total=count_gauges(a.gauges, None)):
        rs = primary_series(g["readings"])
        del g["readings"]
        site = g["site_no"]
        aggs = windows_from_readings(rs, 3600)
        sc, fl = score_series(aggs, m, site)
        t = np.array([x["window_start_ts"] for x in aggs])
        ser = [(r["reading_ts"], r["value"]) for r in rs]
        cr = [ser[i][0] for i in hydro.find_crossings(ser, g["cats"]["minor"])]
        near = np.zeros(len(t), bool)
        for c in cr:
            near |= (t + 3600 >= c - 12 * 3600) & (t <= c + 6 * 3600)
        yrs = (t[-1] - t[0]) / (365.25 * 86400) if len(t) > 1 else 0
        clean["windows"] += len(t); clean["flags"] += int(fl.sum()); clean["gauge_years"] += yrs
        ep = int(np.sum(fl & ~np.concatenate([[False], fl[:-1]])))           # runs of consecutive flagged windows
        gap_fl = features_from_arrays(window_arrays(aggs), *m["scales"].get(site, (m["default_scale"],
                                      m["default_n"])))[:, FEATURES.index("gap_h")] * 6 >= GAP_RULE_H
        ep_gap = int(np.sum(gap_fl & ~np.concatenate([[False], gap_fl[:-1]])))
        clean["episodes"] += ep; clean["episodes_gap"] += ep_gap
        clean["flood_windows"] += int(near.sum()); clean["flood_flags"] += int((fl & near).sum())
        per_gauge.append({"site_no": site, "lid": g["lid"], "windows": len(t), "flags": int(fl.sum()),
                          "flag_episodes": ep, "gap_episodes": ep_gap,
                          "episodes_per_year": round(ep / yrs, 2) if yrs else None,
                          "flood_windows": int(near.sum()), "flood_flags": int((fl & near).sum())})
        scale = m["scales"].get(site, (m["default_scale"],))[0]
        act = g["cats"]["action"]
        rts = np.array([r["reading_ts"] for r in rs])
        quiet = [i for i in range(48, len(rs) - 2000) if rs[i]["value"] < act]
        if not quiet:
            continue
        for kind in FAULTS:
            for _ in range(a.per_gauge):
                i0 = quiet[int(rng.integers(len(quiet)))]
                t0 = rs[i0]["reading_ts"]
                lo = np.searchsorted(rts, t0 - 48 * 3600)
                hi = np.searchsorted(rts, t0 + 100 * 3600)
                seg = rs[lo:hi]
                bad, t1 = inject(seg, kind, t0, scale, rng)
                wa, wb = windows_from_readings(seg, 3600), windows_from_readings(bad, 3600)
                sa, fa_ = score_series(wa, m, site)
                sb, fb = score_series(wb, m, site)
                tb = np.array([x["window_start_ts"] for x in wb])
                ta = {x["window_start_ts"]: k for k, x in enumerate(wa)}
                excess = np.array([fb[k] and not (tb[k] in ta and fa_[ta[tb[k]]]) for k in range(len(tb))])
                # spike/dropout: the hour of the fault + 2 h; persistent faults: only while the fault is active
                # (the injected fault's END is an artificial step back to the true value and must not count)
                if kind in ("spike", "dropout"):
                    inwin = (tb + 3600 > t0) & (tb <= t1 + 2 * 3600)
                else:
                    inwin = (tb + 3600 > t0) & (tb + 3600 <= t1)
                hit = np.nonzero(excess & inwin)[0]
                res[kind]["n"] += 1
                if len(hit):
                    res[kind]["detected"] += 1
                    res[kind]["ttf"].append((tb[hit[0]] + 3600 - t0) / 3600)
                    k0 = hit[0]
                    out = score_window({**wb[k0], "site_no": site, "history": wb[max(0, k0 - 24):k0]}, m)
                    by_reason.setdefault(kind, {}).setdefault(out["reason"], 0)
                    by_reason[kind][out["reason"]] += 1
    rows = [{"fault": k, "injected": v["n"], "detected": v["detected"],
             "detection_rate": round(v["detected"] / v["n"], 3) if v["n"] else None,
             "median_time_to_flag_h": round(float(np.median(v["ttf"])), 2) if v["ttf"] else None,
             "reasons": ";".join(f"{r}:{c}" for r, c in sorted(by_reason.get(k, {}).items(), key=lambda x: -x[1]))}
            for k, v in res.items()]
    rows.append({"fault": "CLEAN test windows", "injected": clean["windows"], "detected": clean["flags"],
                 "detection_rate": round(clean["flags"] / clean["windows"], 5),
                 "median_time_to_flag_h": None,
                 "reasons": f"flagged_windows_per_gauge_year={clean['flags'] / clean['gauge_years']:.2f}; "
                            f"flag_episodes_per_gauge_year={clean['episodes'] / clean['gauge_years']:.2f} "
                            f"(of which no-readings gaps {clean['episodes_gap'] / clean['gauge_years']:.2f})"})
    rows.append({"fault": "REAL FLOOD windows (12 h before to 6 h after a minor crossing)",
                 "injected": clean["flood_windows"], "detected": clean["flood_flags"],
                 "detection_rate": round(clean["flood_flags"] / clean["flood_windows"], 4)
                 if clean["flood_windows"] else None, "median_time_to_flag_h": None, "reasons": ""})
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(a.out.replace(".csv", "_per_gauge.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(per_gauge[0]))
        w.writeheader()
        w.writerows(per_gauge)
    for r in rows:
        print(f"  {r['fault'][:40]:40s} n={r['injected']:>8} flagged={r['detected']:>6} rate={r['detection_rate']} "
              f"ttf={r['median_time_to_flag_h']} {r['reasons']}")
    print(f"done -> {a.out} (+ _per_gauge.csv) in {timer.elapsed()}")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--gauges", default="config/gauge_locations.csv")
    t.add_argument("--start", default="2015-01-01")
    t.add_argument("--train-end", default=TRAIN_END, help="exclusive; 2021-01-01 for the design/validation run")
    t.add_argument("--sample", type=float, default=0.25, help="fraction of windows per gauge used for fitting")
    t.add_argument("--flag-rate", type=float, default=0.001, help="share of clean training windows above threshold")
    e = sub.add_parser("evaluate")
    e.add_argument("--gauges", default="config/gauge_locations.csv")
    e.add_argument("--start", default=TRAIN_END)
    e.add_argument("--end", default="2026-10-31")
    e.add_argument("--per-gauge", type=int, default=10)
    e.add_argument("--out", default="ml/figures/anomaly_eval.csv")
    a = p.parse_args(argv)
    {"train": cmd_train, "evaluate": cmd_evaluate}[a.cmd](a)


if __name__ == "__main__":
    main()
