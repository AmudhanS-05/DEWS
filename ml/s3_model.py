"""S3 / M3.1-M3.3: pooled forecaster v0 (stage only, full history) and v1 (v0 + MRMS rain), false-alarm-budget
alert thresholds, test by time, radius sensitivity.

Protocol (decided before looking at any test number; written in ml/MODEL_CARD.md):
  rows      hourly windows from streams/windowing.py (via ml.dataset.build_site_columns), 38 gauges,
            2015-01-01 .. --end; labels: official NWPS MINOR stage only (y_cross_h = crosses minor within h hours).
  split     train t < 2024-01-01, test t >= 2024-01-01. Inside train: fit on t < 2021-01-01, validate 2021-2023
            (all design and threshold choices are made there; the test period is used once, at the end).
  v0        HistGradientBoosting classifier per horizon on stage features. Two candidate feature sets (V0_BASE,
            V0_LONG = + 12 h and 24 h changes); the one with the higher pooled validation event F1 at the
            false-alarm budget is kept.
  v1        rain-era rows only (windows whose 1/3/6 h MRMS rain is known: event and dry hours from Oct 2020). The rain
            features are rain_1h/3h/6h because those are what aggregates.window carries live (docs/CONTRACTS.md).
            Candidates, compared by leave-one-year-out over 2021, 2022, 2023 (train years only):
              stack  logistic regression on [logit p_v0, log1p rain_1h/3h/6h] ("v0 score plus a rain correction")
              joint  HistGradientBoosting on v0 features + rain (rain-era train rows)
  threshold tau = the value with the most detected crossings whose false alarms on the validation rows do not
            exceed the rise-rate rule's false alarms on the same rows (false-alarm budget); then fixed for test.
  final     v0 = the model fitted on t < 2021 together with the tau chosen for it on 2021-2023; v1 = the mean of its
            leave-one-year-out fold models (the ones whose out-of-fold scores set tau). A first run refitted v0 on
            all of 2015-2023 and reused the validation tau; the refit's probabilities ran higher, so test false
            alarms exceeded the budget (3 h: 378 vs rise-rate 224). That run is kept as
            ml/figures/s3_eval_run1_refit*.csv and the protocol was corrected to keep tau on its own model.
  scoring   ml.harness event rules (6 h cooldown, alert only below minor), per gauge, POOLED, POOLED-SLOW; rise-rate
            rescored on exactly the same rows. Lead time gross and net of the 1 h feed delay.
Training rows: every positive and every row in the 6 h before a crossing, plus a fixed 20 % sample of the rest
(weight x5), weights equal per gauge and per event (ml.rain_model.equal_gauge_event_weights).

    python -m ml.s3_model build   [--end 2026-10-31]            # frame -> ml/figures/s3_frame.parquet (local)
    python -m ml.s3_model train   [--horizons 1,2,3,4,5,6]      # v0, v1, thresholds, test rows, models
    python -m ml.s3_model radius  [--radii 5,10,25]             # M3.3 with the v1 design chosen at 10 km
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np

from ml import harness
from ml.ensemble import FoldEnsemble, stack_X
from ml.rain_model import V0 as V0_BASE, crossings_in_rows, equal_gauge_event_weights, rain_features

V0_LONG = V0_BASE + ["d12h", "d24h"]
RAIN3 = ["rain_1h", "rain_3h", "rain_6h"]
RADII = (5.0, 10.0, 25.0)
TRAIN_END = "2024-01-01"
VAL_START = "2021-01-01"
FRAME = "ml/figures/s3_frame.parquet"
CROSSINGS = "ml/figures/s3_crossings.json"
SEED = 0


def ts(d: str) -> float:
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()


# ---------------- frame ----------------

def lag_change(t: np.ndarray, cur: np.ndarray, lag_h: float) -> np.ndarray:
    """cur(t) - cur(t - lag) where a window exists exactly lag hours earlier, else NaN (as ml.dataset)."""
    pos = np.searchsorted(t, t - lag_h * 3600)
    pc = np.minimum(pos, len(t) - 1)
    hit = (pos < len(t)) & (t[pc] == t - lag_h * 3600)
    return np.where(hit, cur - cur[pc], np.nan)


def build_frame(gauges, rain, progress=None):
    """gauges: iterable of {site_no, lid, cats, readings}; rain: mrms_circle DataFrame (all radii)."""
    import pandas as pd
    from ml.dataset import build_site_columns
    prog = progress or (lambda it, desc, total=None: it)
    frames, crossings = [], {}
    by = {(s, r): g.sort_values("hour_ts") for (s, r), g in rain.groupby(["site_no", "radius_km"])}
    for g in prog(gauges, "gauges"):
        prep = harness.prepare(g["readings"], g["cats"])
        crossings[g["site_no"]] = {"thr": prep["thr"], "lid": g["lid"], "cats": g["cats"],
                                   "crossings": [[float(c[0]), c[1], bool(c[2])] for c in prep["crossings"]]}
        col = build_site_columns(g["readings"], g["cats"], wins=prep["aggs"])
        del prep
        if not col:
            continue
        df = pd.DataFrame(col)
        t, cur = df.t.to_numpy(), df.stage.to_numpy()
        df["d12h"], df["d24h"] = lag_change(t, cur, 12), lag_change(t, cur, 24)
        for rad in RADII:
            r = by.get((g["site_no"], rad))
            if r is None:
                continue
            f = rain_features(t, r.hour_ts.to_numpy(), r.rain_mm.to_numpy(), sums=(1, 3, 6))
            for k, v in f.items():
                df[f"{k}_r{int(rad)}"] = v.astype("float32")
        df["minor"] = g["cats"]["minor"]
        for c in df.columns:
            if df[c].dtype == "float64" and not c.startswith(("t", "y_stage")) and c not in ("stage", "minor"):
                df[c] = df[c].astype("float32")
        frames.append(df)
        del g
    df = pd.concat(frames, ignore_index=True).sort_values(["t", "site_no"], kind="stable").reset_index(drop=True)
    df["w"] = equal_gauge_event_weights(df.site_no.to_numpy(), df.event_id.to_numpy()).astype("float32")
    return df, crossings


# ---------------- alerts and scoring ----------------

def fast_alerts(t: np.ndarray, cur: np.ndarray, thr: float, fire: np.ndarray, cooldown_h: float = 6.0):
    """Same rule as ml.harness.collect, iterating only over candidate rows (t sorted ascending)."""
    idx = np.nonzero(fire & np.isfinite(cur) & (cur < thr))[0]
    out, last = [], -1e18
    for i in idx:
        if t[i] - last >= cooldown_h * 3600:
            out.append((float(t[i]), "minor"))
            last = t[i]
    return out


def per_site_parts(df, fire: np.ndarray, stage_hat: np.ndarray | None, h: int, info: dict) -> dict:
    """site -> (alerts, crossings, forecast stage, observed stage) on the given rows (sorted by t within site)."""
    out = {}
    d = df.assign(_fire=fire, _f=stage_hat if stage_hat is not None else np.full(len(df), np.nan))
    for s, g in d.groupby("site_no", sort=True):
        g = g.sort_values("t", kind="stable")
        t = g.t.to_numpy()
        al = fast_alerts(t, g.stage.to_numpy(), info[s]["thr"], g._fire.to_numpy())
        cr = [tuple(c) for c in crossings_in_rows(info[s]["crossings"], t, h) if t.min() <= c[0]]
        ok = g[f"y_stage_{h}"].notna().to_numpy() & np.isfinite(g._f.to_numpy())
        out[s] = (al, cr, g._f.to_numpy()[ok], g[f"y_stage_{h}"].to_numpy()[ok])
    return out


def pooled_counts(parts: dict, h: int) -> dict:
    from ml import evaluate
    tp = fa = det = nc = na = 0
    for al, cr, _, _ in parts.values():
        r = evaluate.score_alerts(al, [(c[0], c[1]) for c in cr], h)
        tp += r["true_positive_alerts"]; fa += r["false_alarms"]; det += r["detected"]
        nc += r["crossings"]; na += r["alerts"]
    p = tp / na if na else 0.0
    rc = det / nc if nc else 0.0
    return {"alerts": na, "fa": fa, "detected": det, "crossings": nc, "precision": p, "recall": rc,
            "f1": 2 * p * rc / (p + rc) if p + rc else 0.0}


def rise_rate_fire(df, h: int, info: dict) -> tuple[np.ndarray, np.ndarray]:
    rr = np.nan_to_num(df.rise_rate.to_numpy(dtype=float), nan=0.0).clip(min=0)
    f = df.stage.to_numpy() + rr * h
    thr = df.site_no.map({s: v["thr"] for s, v in info.items()}).to_numpy(dtype=float)
    return f >= thr, f


def tau_by_budget(df, p: np.ndarray, h: int, info: dict, budget_fa: int) -> tuple[float, dict]:
    """Most detections with false alarms <= budget (ties: fewer false alarms). Candidates: quantiles of p."""
    qs = 1 - np.geomspace(0.2, 1e-5, 60)
    cands = np.unique(np.quantile(p, qs))
    best, best_c = None, None
    for tau in cands[::-1]:                                     # high -> low: false alarms grow
        c = pooled_counts(per_site_parts(df, p >= tau, None, h, info), h)
        if c["fa"] > budget_fa:
            break
        if best is None or (c["detected"], -c["fa"]) > (best_c["detected"], -best_c["fa"]):
            best, best_c = float(tau), c
    if best is None:
        best = float(cands[-1]) + 1e-9
        best_c = pooled_counts(per_site_parts(df, p >= best, None, h, info), h)
    return best, best_c


# ---------------- models ----------------

def train_rows(df, h: int, frac: float = 0.2, seed: int = SEED):
    """All positives and event rows, plus a fixed fraction of the rest with weight / frac."""
    keep_all = (df[f"y_cross_{h}"].to_numpy() == 1) | (df.event_id.to_numpy() != "")
    rng = np.random.default_rng(seed + h)
    pick = keep_all | (rng.random(len(df)) < frac)
    out = df[pick].copy()
    out["wt"] = np.where(keep_all[pick], out.w.to_numpy(), out.w.to_numpy() / frac)
    return out


def fit_clf(tr, feats: list[str], h: int):
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                       random_state=SEED)
    return m.fit(tr[feats], tr[f"y_cross_{h}"], sample_weight=tr["wt"])


def fit_reg(tr, feats: list[str], h: int):
    from sklearn.ensemble import HistGradientBoostingRegressor
    ok = tr[f"y_stage_{h}"].notna()
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=SEED)
    return m.fit(tr.loc[ok, feats], tr.loc[ok, f"y_stage_{h}"] - tr.loc[ok, "minor"],
                 sample_weight=tr.loc[ok, "wt"])


def fit_stack(p0, rain, y, w):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=1.0, max_iter=1000).fit(stack_X(p0, rain), y, sample_weight=w)


def rain_cols(radius: float) -> list[str]:
    return [f"{k}_r{int(radius)}" for k in RAIN3]


def fit_reg_delta(df, feats: list[str], h: int, frac: float = 0.15, seed: int = SEED):
    """Stage regressor (fix of 1 Nov 2026): target = stage at t + h minus the current stage, fitted on a uniform
    random sample of ALL windows without event weights, so it is valid above minor stage too. The first version
    (fit_reg: target relative to minor, trained on the classifier's event-weighted rows, which are below minor)
    pulled every forecast towards the minor stage (DRWT2, 2019-05: 22.7 ft now -> about 5.4 ft forecast)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    ok = df[f"y_stage_{h}"].notna().to_numpy()
    rng = np.random.default_rng(seed + 100 + h)
    pick = ok & (rng.random(len(df)) < frac)
    d = df[pick]
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, random_state=SEED)
    return m.fit(d[feats], d[f"y_stage_{h}"] - d["stage"])


def cmd_refit_reg(a):
    """Refit only the stage regressors of the committed s3_v0 models (classifiers, tau and p_minor untouched) and
    report stage MAE / RMSE on the test period against persistence and the rise-rate extrapolation."""
    import joblib
    import pandas as pd
    from ml.progress import Timer, bar
    timer = Timer()
    horizons = [int(x) for x in a.horizons.split(",")]
    df, info = _load()
    fit_part = df[df.t < ts(VAL_START)]
    test = df[df.t >= ts(TRAIN_END)]
    rows = []
    print(f"ml.s3_model refit-reg: delta-stage regressors on a {0.15:.0%} uniform sample of {len(fit_part)} windows "
          f"(t < {VAL_START}); test {len(test)} windows")
    for h in bar(horizons, "horizons"):
        path = os.path.join(a.models_dir, f"s3_v0_h{h}.joblib")
        m = joblib.load(path)
        reg = fit_reg_delta(fit_part, m["features"], h)
        m["reg"], m["reg_target"] = reg, "delta"
        joblib.dump(m, path, compress=3)
        t = test[test[f"y_stage_{h}"].notna()]
        y = t[f"y_stage_{h}"].to_numpy()
        cur = t.stage.to_numpy()
        f_model = cur + reg.predict(t[m["features"]])
        f_rr = cur + np.nan_to_num(t.rise_rate.to_numpy(), nan=0.0).clip(min=0) * h
        above = (cur >= t.minor.to_numpy())
        for name, f in (("s3_v0_reg", f_model), ("persistence", cur), ("rise_rate", f_rr)):
            e = f - y
            rows.append({"horizon_h": h, "model": name, "mae_ft": round(float(np.abs(e).mean()), 4),
                         "rmse_ft": round(float(np.sqrt((e * e).mean())), 4),
                         "mae_above_minor_ft": round(float(np.abs(e[above]).mean()), 4) if above.any() else None,
                         "n": int(len(e)), "n_above_minor": int(above.sum())})
    pd.DataFrame(rows).to_csv(a.out, index=False)
    for r in rows:
        print(f"  h={r['horizon_h']} {r['model']:12s} MAE {r['mae_ft']} ft RMSE {r['rmse_ft']} ft "
              f"MAE above minor {r['mae_above_minor_ft']} ft (n {r['n_above_minor']})")
    print(f"done -> {a.out}; models rewritten in {a.models_dir} in {timer.elapsed()}")


def fit_reg_variant(df, feats: list[str], h: int, variant: str, seed: int = SEED):
    """Stage-change regressors compared on validation (1 Nov 2026):
    mean_uniform  mean change, uniform 15 % sample (the 1 Nov fix)
    q90_uniform   0.9 quantile of the change, same sample
    mean_event    mean change on the classifier's event-weighted rows (all positives and pre-crossing rows + 20 %)
    q90_event     0.9 quantile on the event-weighted rows"""
    from sklearn.ensemble import HistGradientBoostingRegressor
    loss = {"loss": "quantile", "quantile": 0.9} if variant.startswith("q90") else {}
    if variant.endswith("uniform"):
        ok = df[f"y_stage_{h}"].notna().to_numpy()
        rng = np.random.default_rng(seed + 100 + h)
        d = df[ok & (rng.random(len(df)) < 0.15)]
        w = None
    else:
        d = train_rows(df, h)
        d = d[d[f"y_stage_{h}"].notna()]
        w = d["wt"].to_numpy()
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, random_state=SEED, **loss)
    return m.fit(d[feats], d[f"y_stage_{h}"] - d["stage"], sample_weight=w)


REG_VARIANTS = ("mean_uniform", "q90_uniform", "mean_event", "q90_event")


def cmd_reg_variants(a):
    """Choose the shipped stage regressor on VALIDATION only (fit t < 2021, validate 2021-2023; test untouched)."""
    import joblib
    import pandas as pd
    from ml.progress import Timer, bar
    timer = Timer()
    h = a.h
    df, info = _load()
    fit_part = df[df.t < ts(VAL_START)]
    val = df[(df.t >= ts(VAL_START)) & (df.t < ts(TRAIN_END)) & df[f"y_stage_{h}"].notna()]
    m = joblib.load(os.path.join(a.models_dir, f"s3_v0_h{h}.joblib"))           # classifier fit < 2021
    feats = m["features"]
    p = m["clf"].predict_proba(val[feats])[:, 1]
    cur, y, minor = val.stage.to_numpy(), val[f"y_stage_{h}"].to_numpy(), val.minor.to_numpy()
    alert = (p >= m["tau"]) & (cur < minor)
    rising = (val.d1h.to_numpy() > 0.5) & (cur < minor)
    above = cur >= minor
    rows = []
    print(f"ml.s3_model reg-variants h={h}: fit {len(fit_part)} windows (t < {VAL_START}), validate {len(val)} "
          f"(2021-2023): {int(alert.sum())} model-alert windows, {int(rising.sum())} rising windows below minor, "
          f"{int(above.sum())} above minor")
    for v in bar(REG_VARIANTS, "variants"):
        reg = fit_reg_variant(fit_part, feats, h, v)
        f = cur + reg.predict(val[feats])
        e = f - y
        rows.append({"variant": v, "horizon_h": h,
                     "agree_on_alert_windows": round(float((f[alert] >= minor[alert]).mean()), 3) if alert.any() else None,
                     "mae_ft": round(float(np.abs(e).mean()), 4),
                     "mae_above_minor_ft": round(float(np.abs(e[above]).mean()), 4),
                     "mean_underprediction_rising_ft": round(float(np.clip(y[rising] - f[rising], 0, None).mean()), 3),
                     "median_error_rising_ft": round(float(np.median(e[rising])), 3),
                     "n_alert": int(alert.sum()), "n_rising": int(rising.sum())})
    pd.DataFrame(rows).to_csv(a.out, index=False)
    for r in rows:
        print("  " + "  ".join(f"{k}={v}" for k, v in r.items()))
    print(f"done -> {a.out} in {timer.elapsed()}")


# ---------------- CLI ----------------

def _load():
    import pandas as pd
    df = pd.read_parquet(FRAME)
    info = json.load(open(CROSSINGS, encoding="utf-8"))
    return df, info


def cmd_build(a):
    import pandas as pd
    from common import config
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    timer = Timer()
    rain_path = os.path.join(config.DATA_DIR, "rain", "mrms_circle.parquet")
    if not os.path.exists(rain_path):                     # laptop without the M2.1 run: the committed copy
        rain_path = os.path.join("ml", "rain_cache", "mrms_circle.parquet")
    rain = pd.read_parquet(rain_path)
    print(f"ml.s3_model build: windows + labels + d12h/d24h + rain (radii {RADII}) {a.start}..{a.end} -> {FRAME}")
    gauges = iter_gauges(a.gauges, None, a.start, a.end, config.DATA_DIR)
    df, info = build_frame(gauges, rain, progress=lambda it, d, total=None: bar(it, d, count_gauges(a.gauges, None)))
    df.to_parquet(FRAME, index=False)
    json.dump(info, open(CROSSINGS, "w", encoding="utf-8"))
    n_rain = int(df["rain_6h_r10"].notna().sum()) if "rain_6h_r10" in df else 0
    print(f"done: {len(df)} rows, {df.site_no.nunique()} gauges, {sum(len(v['crossings']) for v in info.values())} "
          f"minor crossings, {n_rain} rows with 10 km rain -> {FRAME} in {timer.elapsed()}")


def rain_mask(df) -> np.ndarray:
    """Rows where 1/3/6 h rain is known at every radius (so radii are compared on identical rows)."""
    cols = [c for r in RADII for c in rain_cols(r)]
    return df[cols].notna().all(axis=1).to_numpy()


def v1_oof(design: str, val_rain, train_rain, p0_val: np.ndarray, feats0: list[str], rc: list[str], h: int,
           years=(2021, 2022, 2023)) -> np.ndarray:
    """Leave-one-year-out predictions on val_rain (2021-2023 rain rows) for one v1 design."""
    yr = val_rain.t.map(lambda x: datetime.fromtimestamp(x, timezone.utc).year).to_numpy()
    out = np.full(len(val_rain), np.nan)
    models = []
    for y in years:
        te = yr == y
        if not te.any():
            continue
        if design == "stack":
            tr = ~te
            m = fit_stack(p0_val[tr], val_rain[rc].to_numpy()[tr], val_rain[f"y_cross_{h}"].to_numpy()[tr],
                          val_rain.w.to_numpy()[tr])
            out[te] = m.predict_proba(stack_X(p0_val[te], val_rain[rc].to_numpy()[te]))[:, 1]
            models.append(m)
        else:
            tyr = train_rain.t.map(lambda x: datetime.fromtimestamp(x, timezone.utc).year).to_numpy()
            tr = train_rows(train_rain[tyr != y], h, frac=1.0)
            m = fit_clf(tr, feats0 + rc, h)
            out[te] = m.predict_proba(val_rain[feats0 + rc][te])[:, 1]
            models.append(m)
    return out, FoldEnsemble(design, models)


def v1_predict(model: "FoldEnsemble", p0: np.ndarray, rows, feats0, rc) -> np.ndarray:
    return model.predict_proba_from(p0, rows[rc].to_numpy(), rows[feats0 + rc] if model.design == "joint" else None)


def _ap(y, p) -> float:
    from sklearn.metrics import average_precision_score
    return float(average_precision_score(y, p)) if y.sum() else float("nan")


def _counts_str(c: dict) -> str:
    return (f"F1 {c['f1']:.2f} P {c['precision']:.2f} R {c['recall']:.2f} FA {c['fa']} "
            f"det {c['detected']}/{c['crossings']}")


def pr_points(y: np.ndarray, p: np.ndarray, n: int = 60) -> list[tuple]:
    from sklearn.metrics import precision_recall_curve
    if not y.sum():
        return []
    pr, rc, th = precision_recall_curve(y, p)
    idx = np.unique(np.linspace(0, len(th) - 1, n).astype(int))
    return [(float(th[i]), float(pr[i]), float(rc[i])) for i in idx]


def cmd_train(a):
    import joblib
    import pandas as pd
    from ml.progress import Timer, bar
    from ml.rain_model import score
    timer = Timer()
    horizons = [int(x) for x in a.horizons.split(",")]
    df, info = _load()
    tr_end, val_start = ts(TRAIN_END), ts(VAL_START)
    train, test = df[df.t < tr_end], df[df.t >= tr_end]
    fit_part, val = train[train.t < val_start], train[train.t >= val_start]
    rm = rain_mask(df)
    train_rain, val_rain, test_rain = df[rm & (df.t < tr_end)], df[rm & (df.t >= val_start) & (df.t < tr_end)], \
        df[rm & (df.t >= tr_end)]
    nocross = lambda d: pooled_counts(per_site_parts(d, np.zeros(len(d), bool), None, 3, info), 3)["crossings"]
    print(f"ml.s3_model train: {len(df)} rows; fit {len(fit_part)} (<2021, {nocross(fit_part)} crossings), "
          f"validate {len(val)} (2021-2023, {nocross(val)}), test {len(test)} (2024-, {nocross(test)}); "
          f"rain rows: train {len(train_rain)}, validate {len(val_rain)} ({nocross(val_rain)}), "
          f"test {len(test_rain)} ({nocross(test_rain)})")
    os.makedirs(a.models_dir, exist_ok=True)
    rows, choices, pr, scores = [], {}, [], []
    rc = rain_cols(10.0)
    for h in bar(horizons, "horizons"):
        # ---- v0: feature set and tau on the validation years ----
        budget_val = pooled_counts(per_site_parts(val, rise_rate_fire(val, h, info)[0], None, h, info), h)
        trf = train_rows(fit_part, h)
        cand = {}
        for name, feats in (("base", V0_BASE), ("long", V0_LONG)):
            m = fit_clf(trf, feats, h)
            p = m.predict_proba(val[feats])[:, 1]
            tau, c = tau_by_budget(val, p, h, info, budget_val["fa"])
            cand[name] = {"feats": feats, "model": m, "tau": tau, "val": c, "ap": _ap(val[f"y_cross_{h}"].to_numpy(), p)}
        pick = max(cand, key=lambda k: (cand[k]["val"]["f1"], cand[k]["val"]["detected"], -cand[k]["val"]["fa"]))
        feats0, tau0, m0_val = cand[pick]["feats"], cand[pick]["tau"], cand[pick]["model"]
        # ---- v1 (10 km): design by leave-one-year-out on the 2021-2023 rain rows ----
        p0_valrain = m0_val.predict_proba(val_rain[feats0])[:, 1]
        budget_vr = pooled_counts(per_site_parts(val_rain, rise_rate_fire(val_rain, h, info)[0], None, h, info), h)
        tau_v0r, c_v0r = tau_by_budget(val_rain, p0_valrain, h, info, budget_vr["fa"])
        v1c = {}
        for design in ("stack", "joint"):
            oof, ens = v1_oof(design, val_rain, train_rain, p0_valrain, feats0, rc, h)
            tau, c = tau_by_budget(val_rain, oof, h, info, budget_vr["fa"])
            v1c[design] = {"tau": tau, "val": c, "ap": _ap(val_rain[f"y_cross_{h}"].to_numpy(), oof), "model": ens}
        d1 = max(v1c, key=lambda k: (v1c[k]["val"]["f1"], v1c[k]["ap"]))
        choices[h] = {"v0_features": pick, "v0_tau": tau0,
                      "v0_candidates": {k: {"val": v["val"], "ap": v["ap"], "tau": v["tau"]} for k, v in cand.items()},
                      "riserate_val": budget_val, "v1_design": d1, "v1_tau": v1c[d1]["tau"],
                      "v1_candidates": {k: {kk: vv for kk, vv in v.items() if kk != "model"} for k, v in v1c.items()},
                      "v0_on_val_rain": {"val": c_v0r, "tau": tau_v0r,
                                                               "ap": _ap(val_rain[f"y_cross_{h}"].to_numpy(), p0_valrain)},
                      "riserate_val_rain": budget_vr}
        print(f"\n  h={h}: v0 features '{pick}' (val {_counts_str(cand[pick]['val'])}, tau {tau0:.4f}) vs rise-rate "
              f"(val {_counts_str(budget_val)}); other set '{[k for k in cand if k != pick][0]}' "
              f"{_counts_str(cand[[k for k in cand if k != pick][0]]['val'])}")
        print(f"        v1 design '{d1}' (val-rain LOYO {_counts_str(v1c[d1]['val'])} AP {v1c[d1]['ap']:.3f}); "
              + "; ".join(f"{k} {_counts_str(v['val'])} AP {v['ap']:.3f}" for k, v in v1c.items() if k != d1)
              + f"; v0 on same rows {_counts_str(c_v0r)}; rise-rate {_counts_str(budget_vr)}")
        # ---- final models: the validated ones (tau stays on the scale it was chosen on) ----
        clf0, reg0 = m0_val, fit_reg(trf, feats0, h)
        m1 = v1c[d1]["model"]
        joblib.dump({"kind": "v0", "clf": clf0, "reg": reg0, "tau": tau0, "features": feats0, "horizon_h": h,
                     "category": "minor", "trained_on": f"t < {VAL_START} (20 % negative sample); tau on {VAL_START}..{TRAIN_END}",
                     "seed": SEED}, os.path.join(a.models_dir, f"s3_v0_h{h}.joblib"), compress=3)
        joblib.dump({"kind": "v1", "design": d1, "model": m1, "tau": v1c[d1]["tau"], "base": f"s3_v0_h{h}.joblib",
                     "v0_features": feats0, "rain_features": RAIN3, "radius_km": 10.0, "horizon_h": h,
                     "category": "minor", "trained_on": "leave-one-year-out fold models over 2020-10..2023 rain rows (mean)",
                     "seed": SEED}, os.path.join(a.models_dir, f"s3_v1_r10_h{h}.joblib"), compress=3)
        # ---- test (used once) ----
        p0 = clf0.predict_proba(test[feats0])[:, 1]
        s0 = reg0.predict(test[feats0]) + test.minor.to_numpy()
        fr, frs = rise_rate_fire(test, h, info)
        rows += score(a.label, "s3_v0", h, per_site_parts(test, p0 >= tau0, s0, h, info), 1.0,
                      f"rows=all_test tau={tau0:.4f} row_AP={_ap(test[f'y_cross_{h}'].to_numpy(), p0):.3f}")
        rows += score(a.label, "rise_rate", h, per_site_parts(test, fr, frs, h, info), 1.0, "rows=all_test")
        p0r = clf0.predict_proba(test_rain[feats0])[:, 1]
        s0r = reg0.predict(test_rain[feats0]) + test_rain.minor.to_numpy()
        p1r = v1_predict(m1, p0r, test_rain, feats0, rc)
        frr, frrs = rise_rate_fire(test_rain, h, info)
        yr = test_rain[f"y_cross_{h}"].to_numpy()
        rl = a.label + "-rainrows"
        rows += score(rl, "s3_v0", h, per_site_parts(test_rain, p0r >= tau0, s0r, h, info), 1.0,
                      f"rows=rain_test tau={tau0:.4f} row_AP={_ap(yr, p0r):.3f}")
        rows += score(rl, f"s3_v1_{d1}_r10", h, per_site_parts(test_rain, p1r >= v1c[d1]["tau"], s0r, h, info), 1.0,
                      f"rows=rain_test tau={v1c[d1]['tau']:.4f} row_AP={_ap(yr, p1r):.3f}")
        rows += score(rl, "rise_rate", h, per_site_parts(test_rain, frr, frrs, h, info), 1.0, "rows=rain_test")
        yt = test[f"y_cross_{h}"].to_numpy()
        for name, y, pp in (("s3_v0_all_test", yt, p0), ("s3_v0_rain_test", yr, p0r), ("s3_v1_rain_test", yr, p1r)):
            pr += [{"curve": name, "horizon_h": h, "threshold": t_, "precision": pp_, "recall": r_}
                   for t_, pp_, r_ in pr_points(y, pp)]
            scores.append(pd.DataFrame({"curve": name, "horizon_h": h, "y": y.astype("int8"),
                                        "p": pp.astype("float32")}))
    harness.write_csv(rows, a.out)
    pd.DataFrame(pr).to_csv(a.out.replace(".csv", "_pr_curve.csv"), index=False)
    pd.concat(scores).to_parquet(a.out.replace(".csv", "_scores.parquet"), index=False)
    json.dump(choices, open(a.out.replace(".csv", "_choices.json"), "w"), indent=1, default=float)
    print("\n  TEST (2024-01-01 .. end), POOLED:")
    for r in rows:
        if r["site_no"] in ("POOLED", "POOLED-SLOW"):
            print(f"  {r['run_label']:20s} {r['model']:18s} {r['site_no']:11s} h={r['horizon_h']} "
                  f"P {harness._fmt(r['precision'])} R {harness._fmt(r['recall'])} F1 {harness._fmt(r['f1'])} "
                  f"FA {r['false_alarms']} | {r['notes']}")
    print(f"\ndone: {len(rows)} rows -> {a.out} (+ _pr_curve.csv, _choices.json, _scores.parquet) in {timer.elapsed()}")


def cmd_radius(a):
    """M3.3: the v1 design chosen at 10 km, rerun at each radius with the same protocol (LOYO tau, final fit, test)."""
    import pandas as pd
    import joblib
    from ml.progress import Timer, bar
    from ml.rain_model import score
    timer = Timer()
    horizons = [int(x) for x in a.horizons.split(",")]
    radii = [float(x) for x in a.radii.split(",")]
    choices = json.load(open("ml/figures/s3_eval_choices.json"))
    df, info = _load()
    tr_end, val_start = ts(TRAIN_END), ts(VAL_START)
    rm = rain_mask(df)
    train_rain, val_rain, test_rain = df[rm & (df.t < tr_end)], df[rm & (df.t >= val_start) & (df.t < tr_end)], \
        df[rm & (df.t >= tr_end)]
    print(f"ml.s3_model radius: radii {radii} km, h={horizons}, identical rain rows (train {len(train_rain)}, "
          f"validate {len(val_rain)}, test {len(test_rain)})")
    rows = []
    for h in bar(horizons, "horizons"):
        ch = choices[str(h)]
        v0 = joblib.load(f"ml/models/s3_v0_h{h}.joblib")
        feats0, d1 = v0["features"], ch["v1_design"]
        p0_valrain = v0["clf"].predict_proba(val_rain[feats0])[:, 1]          # the validated v0 (fit < 2021)
        budget = pooled_counts(per_site_parts(val_rain, rise_rate_fire(val_rain, h, info)[0], None, h, info), h)
        p0r = v0["clf"].predict_proba(test_rain[feats0])[:, 1]
        s0r = v0["reg"].predict(test_rain[feats0]) + test_rain.minor.to_numpy()
        yr = test_rain[f"y_cross_{h}"].to_numpy()
        for R in radii:
            rc = rain_cols(R)
            oof, m1 = v1_oof(d1, val_rain, train_rain, p0_valrain, feats0, rc, h)
            tau, c = tau_by_budget(val_rain, oof, h, info, budget["fa"])
            p1 = v1_predict(m1, p0r, test_rain, feats0, rc)
            rows += score(f"m3.3-radius-{int(R)}km", f"s3_v1_{d1}_r{int(R)}", h,
                          per_site_parts(test_rain, p1 >= tau, s0r, h, info), 1.0,
                          f"rows=rain_test radius_km={R:g} tau={tau:.4f} row_AP={_ap(yr, p1):.3f} "
                          f"val_F1={c['f1']:.2f} val_AP={_ap(val_rain[f'y_cross_{h}'].to_numpy(), oof):.3f}")
    harness.write_csv(rows, a.out)
    print("\n  TEST rain rows, POOLED:")
    for r in rows:
        if r["site_no"] == "POOLED":
            print(f"  {r['model']:20s} h={r['horizon_h']} P {harness._fmt(r['precision'])} R "
                  f"{harness._fmt(r['recall'])} F1 {harness._fmt(r['f1'])} FA {r['false_alarms']} | {r['notes']}")
    print(f"\ndone: {len(rows)} rows -> {a.out} in {timer.elapsed()}")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--gauges", default="config/gauge_locations.csv")
    b.add_argument("--start", default="2015-01-01")
    b.add_argument("--end", default="2026-10-31")
    t = sub.add_parser("train")
    t.add_argument("--horizons", default="1,2,3,4,5,6")
    t.add_argument("--label", default="m3.1-s3")
    t.add_argument("--out", default="ml/figures/s3_eval.csv")
    t.add_argument("--models-dir", default="ml/models")
    r = sub.add_parser("radius")
    r.add_argument("--radii", default="5,10,25")
    r.add_argument("--horizons", default="1,2,3,4,5,6")
    r.add_argument("--out", default="ml/figures/s3_radius.csv")
    rr = sub.add_parser("refit-reg")
    rr.add_argument("--horizons", default="1,2,3,4,5,6")
    rr.add_argument("--models-dir", default="ml/models")
    rr.add_argument("--out", default="ml/figures/s3_stage_mae.csv")
    rv = sub.add_parser("reg-variants")
    rv.add_argument("--h", type=int, default=3)
    rv.add_argument("--models-dir", default="ml/models")
    rv.add_argument("--out", default="ml/figures/s3_reg_variants_validation.csv")
    a = p.parse_args(argv)
    {"build": cmd_build, "train": cmd_train, "radius": cmd_radius, "refit-reg": cmd_refit_reg,
     "reg-variants": cmd_reg_variants}[a.cmd](a)


if __name__ == "__main__":
    main()
