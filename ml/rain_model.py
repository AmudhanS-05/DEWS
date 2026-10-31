"""M2.2: model v1 (stage + MRMS rain) vs v0 (stage only), same rows, split by time, event metrics.

Rows: the pooled hourly windows of ml/dataset.py (streams/windowing.py via ml.backtest, unchanged) for every gauge,
kept only where the M2.1 rain file has the full 24 h of rain before the window end (event hours + matched dry hours,
from 2020-10-14 on). v0 and v1 are trained and tested on exactly the same rows, so the difference is the rain.
  v0 features  rel_minor, rel_action, frac_to_minor, rise_rate, stage_range, d1h, d3h, d6h
  v1 features  v0 + rain_1h, rain_3h, rain_6h, rain_24h (mm, mean depth over the circle, default 10 km), each the
               sum of the hourly MRMS Pass 2 accumulations ending in (t - lag - k h, t - lag] (default lag 0 h:
               Pass 2 is published ~1 h after its hour, the same order as the ~1 h stage feed delay).
Per horizon h: a HistGradientBoosting classifier for "crosses MINOR within h" and a regressor for the stage at
t + h (relative to minor). Sample weights: every gauge carries equal total weight, every event equal weight within its
gauge (ml.dataset.event_weights), so fast urban creeks do not dominate. The alert threshold tau is chosen on the last
25 % (by time) of the TRAIN period after fitting on the first 75 %, then the model is refit on all of train;
the test period is never used for any choice.
Alert rule (as ml/harness.py): p >= tau while the stage is below minor, 6 h cooldown. A crossing is scored only if the
row set holds at least one window in [crossing - h, crossing) (the model had a chance to warn). The rise-rate baseline
is scored on the same rows for reference. Rows are evaluation_runs-shaped (ml/harness.py COLUMNS), data_source
"replay" (SIMULATION data), per gauge plus POOLED and POOLED-SLOW.

Caveat to state with the result: the row set is a case-control sample (flood windows plus matched dry windows), not
the continuous stream, so precision and false alarms are not those of a year of live operation.

    python -m ml.rain_model --split 2024-01-01 --horizons 1,2,3,4,5,6 --label m2.2-rain-v1 \
        --out ml/figures/model_v1_vs_v0.csv
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np

from ml import harness

V0 = ["rel_minor", "rel_action", "frac_to_minor", "rise_rate", "stage_range", "d1h", "d3h", "d6h"]
RAIN_SUMS_H = (1, 3, 6, 24)
RAIN = [f"rain_{k}h" for k in RAIN_SUMS_H]
V1 = V0 + RAIN
TAU_GRID = tuple(round(x, 2) for x in np.arange(0.05, 0.96, 0.05))


def rain_features(t: np.ndarray, hour_ts: np.ndarray, rain_mm: np.ndarray, sums=RAIN_SUMS_H,
                  lag_h: float = 0.0) -> dict[str, np.ndarray]:
    """rain_kh at each t = sum of hourly accumulations whose valid time (end of hour) lies in (t-lag-k h, t-lag].
    NaN unless all k hours are present and finite (a missing hour is never read as zero rain)."""
    out = {f"rain_{k}h": np.full(len(t), np.nan) for k in sums}
    if not len(hour_ts) or not len(t):
        return out
    hi = np.round(np.asarray(hour_ts, dtype=float) / 3600).astype(np.int64)
    h0, h1 = hi.min(), hi.max()
    full = np.full(h1 - h0 + 1, np.nan)
    full[hi - h0] = rain_mm
    ok = np.isfinite(full)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, full, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    end = np.floor((np.asarray(t, dtype=float) - lag_h * 3600) / 3600).astype(np.int64) - h0   # index of last hour
    for k in sums:
        a, b = end - k + 1, end + 1                       # hours [a, b) in index space
        valid = (a >= 0) & (b <= len(full))
        ac, bc = np.clip(a, 0, len(full)), np.clip(b, 0, len(full))
        n = cn[bc] - cn[ac]
        s = cs[bc] - cs[ac]
        out[f"rain_{k}h"] = np.where(valid & (n == k), s, np.nan)
    return out


def alerts_from_flags(t: np.ndarray, cur: np.ndarray, thr: float, fire: np.ndarray, category: str = "minor",
                      cooldown_h: float = 6.0) -> list[tuple[float, str]]:
    """Same rule as ml.harness.collect: fire while the current stage is below the category, 6 h cooldown."""
    order = np.argsort(t, kind="stable")
    alerts, last = [], -1e18
    for i in order:
        if fire[i] and np.isfinite(cur[i]) and cur[i] < thr and t[i] - last >= cooldown_h * 3600:
            alerts.append((float(t[i]), category))
            last = t[i]
    return alerts


def crossings_in_rows(crossings: list[tuple], t_rows: np.ndarray, horizon_h: float) -> list[tuple]:
    """Keep crossings (ct, cat, slow) with at least one row in [ct - h, ct)."""
    ts = np.sort(np.asarray(t_rows, dtype=float))
    keep = []
    for c in crossings:
        k = np.searchsorted(ts, c[0] - horizon_h * 3600, side="left")
        if k < len(ts) and ts[k] < c[0]:
            keep.append(c)
    return keep


def equal_gauge_event_weights(site: np.ndarray, event_id: np.ndarray) -> np.ndarray:
    """Vectorised ml.dataset.event_weights (same definition)."""
    import pandas as pd
    d = pd.DataFrame({"s": site, "e": event_id})
    n_rows = d.groupby("s")["s"].transform("size").to_numpy(float)
    ev = d[d.e != ""]
    per_event = d.groupby("e")["e"].transform("size").to_numpy(float)
    n_ev = ev.groupby("s")["e"].nunique()
    n_ev_row = d["s"].map(n_ev).fillna(1).to_numpy(float)
    return np.where(d.e.to_numpy() != "", 1.0 / (per_event * np.maximum(n_ev_row, 1)), 1.0 / n_rows)


def score(label: str, model: str, h: float, per_site: dict[str, tuple], feed_delay_h: float = 1.0,
          extra_notes: str = "") -> list[dict]:
    """per_site: site -> (alerts, crossings, f, obs). Rows shaped like ml.harness (per gauge, POOLED, POOLED-SLOW)."""
    rows = [harness._scored_row(label, s, model, h, [pt], feed_delay_h) for s, pt in per_site.items()]
    rows.append(harness._scored_row(label, "POOLED", model, h, list(per_site.values()), feed_delay_h))
    slow = [(*harness._slow_only(a, c, h), f, o) for a, c, f, o in per_site.values()]
    rows.append(harness._scored_row(label, "POOLED-SLOW", model, h, slow, feed_delay_h))
    if extra_notes:
        for r in rows:
            r["notes"] += " " + extra_notes
    return rows


def _event_f1(df, p, tau, h, site_info) -> float:
    tp = fa = det = nc = na = 0
    for s, g in df.assign(_p=p).groupby("site_no"):
        thr = site_info[s]["thr"]
        al = alerts_from_flags(g.t.to_numpy(), g.stage.to_numpy(), thr, g._p.to_numpy() >= tau)
        cr = crossings_in_rows(site_info[s]["crossings"], g.t.to_numpy(), h)
        cr = [c for c in cr if g.t.min() <= c[0] <= g.t.max() + h * 3600]
        from ml import evaluate
        r = evaluate.score_alerts(al, [(c[0], c[1]) for c in cr], h)
        tp += r["true_positive_alerts"]; fa += r["false_alarms"]; det += r["detected"]
        nc += r["crossings"]; na += r["alerts"]
    if not nc or not na:
        return 0.0
    pr, rc = tp / na, det / nc
    return 2 * pr * rc / (pr + rc) if pr + rc else 0.0


def fit_one(train, feats: list[str], h: int, site_info: dict, seed: int = 0, choose_tau: bool = True):
    """Classifier + regressor for horizon h; tau from an inner time split of train (never the test period)."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    y, w = train[f"y_cross_{h}"].to_numpy(), train["w"].to_numpy()

    def clf():
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                              l2_regularization=1.0, random_state=seed)
    tau = 0.5
    if choose_tau:
        cut = train.t.quantile(0.75)
        a, b = train[train.t < cut], train[train.t >= cut]
        if a[f"y_cross_{h}"].nunique() == 2 and b[f"y_cross_{h}"].sum() > 0:
            m = clf().fit(a[feats], a[f"y_cross_{h}"], sample_weight=a["w"])
            pb = m.predict_proba(b[feats])[:, 1]
            scores = [(_event_f1(b, pb, tt, h, site_info), -tt) for tt in TAU_GRID]
            best = max(scores)
            tau = -best[1] if best[0] > 0 else 0.5
    c = clf().fit(train[feats], y, sample_weight=w)
    ok = train[f"y_stage_{h}"].notna()
    r = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=seed)
    r.fit(train.loc[ok, feats], train.loc[ok, f"y_stage_{h}"] - train.loc[ok, "minor"], sample_weight=w[ok.to_numpy()])
    return {"clf": c, "reg": r, "tau": float(tau), "features": feats, "horizon_h": h}


def evaluate_models(test, models: dict[str, dict], h: int, site_info: dict, label: str,
                    feed_delay_h: float = 1.0) -> tuple[list[dict], dict]:
    from sklearn.metrics import average_precision_score
    rows, pr = [], {}
    y = test[f"y_cross_{h}"].to_numpy()
    for name, m in models.items():
        p = m["clf"].predict_proba(test[m["features"]])[:, 1]
        stage_hat = m["reg"].predict(test[m["features"]]) + test["minor"].to_numpy()
        ap = average_precision_score(y, p) if y.sum() else float("nan")
        pr[name] = (y, p)
        rows += score(label, name, h, _per_site(test, p >= m["tau"], stage_hat, h, site_info), feed_delay_h,
                      f"tau={m['tau']:.2f} row_AP={ap:.3f}")
    rr = test.rise_rate.clip(lower=0).fillna(0).to_numpy()
    f_rr = test.stage.to_numpy() + rr * h
    thr = test.site_no.map({s: v["thr"] for s, v in site_info.items()}).to_numpy()
    rows += score(label, "rise_rate", h, _per_site(test, f_rr >= thr, f_rr, h, site_info), feed_delay_h,
                  "same rows as v0/v1")
    return rows, pr


def _per_site(test, fire: np.ndarray, stage_hat: np.ndarray, h: int, site_info: dict) -> dict:
    out = {}
    d = test.assign(_fire=fire, _f=stage_hat)
    for s, g in d.groupby("site_no", sort=True):
        t = g.t.to_numpy()
        al = alerts_from_flags(t, g.stage.to_numpy(), site_info[s]["thr"], g._fire.to_numpy())
        cr = [c for c in crossings_in_rows(site_info[s]["crossings"], t, h) if t.min() <= c[0]]
        ok = g[f"y_stage_{h}"].notna().to_numpy()
        out[s] = (al, cr, g._f.to_numpy()[ok], g[f"y_stage_{h}"].to_numpy()[ok])
    return out


def build_rows(gauges: list[dict], rain, radius_km: float, lag_h: float, progress=None):
    """Dataset rows + rain features for every gauge; returns (DataFrame of rows with complete 24 h rain, site_info)."""
    import pandas as pd
    from ml.dataset import build_site_columns
    prog = progress or (lambda it, desc, total=None: it)
    frames, info = [], {}
    rain = rain[rain.radius_km == radius_km]
    by_site = {s: g.sort_values("hour_ts") for s, g in rain.groupby("site_no")}
    for g in prog(gauges, "gauges"):            # any iterable: ml.gauges.iter_gauges loads one gauge at a time
        prep = harness.prepare(g["readings"], g["cats"])
        info[g["site_no"]] = {"thr": prep["thr"], "crossings": prep["crossings"], "lid": g["lid"]}
        col = build_site_columns(g["readings"], g["cats"], wins=prep["aggs"])
        if not col or g["site_no"] not in by_site:
            continue
        df = pd.DataFrame(col)
        r = by_site[g["site_no"]]
        for k, v in rain_features(df.t.to_numpy(), r.hour_ts.to_numpy(), r.rain_mm.to_numpy(), lag_h=lag_h).items():
            df[k] = v
        df = df[df["rain_24h"].notna()].copy()
        df["minor"] = g["cats"]["minor"]
        frames.append(df)
        del prep, col
    if not frames:
        return pd.DataFrame(), info
    df = pd.concat(frames, ignore_index=True).sort_values(["t", "site_no"], kind="stable").reset_index(drop=True)
    df["w"] = equal_gauge_event_weights(df.site_no.to_numpy(), df.event_id.to_numpy())
    return df, info


def _ts(d: str) -> float:
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()


def main(argv=None):
    import argparse
    import joblib
    import pandas as pd
    from common import config
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="M2.2: v1 (stage + rain) vs v0 (stage only) on the MRMS hours.")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only")
    p.add_argument("--start", default="2020-10-01")
    p.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    p.add_argument("--split", default="2024-01-01", help="train t < split, test t >= split (UTC date)")
    p.add_argument("--horizons", default="1,2,3,4,5,6")
    p.add_argument("--radius-km", type=float, default=10.0)
    p.add_argument("--rain-lag-h", type=float, default=0.0)
    p.add_argument("--label", default="m2.2-rain-v1")
    p.add_argument("--out", default="ml/figures/model_v1_vs_v0.csv")
    p.add_argument("--models-dir", default="ml/models")
    p.add_argument("--data-dir")
    p.add_argument("--insert", action="store_true", help="also INSERT into evaluation_runs (needs the DB up)")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer = Timer()
    horizons = [int(x) for x in a.horizons.split(",")]
    rain_path = os.path.join(config.DATA_DIR, "rain", "mrms_circle.parquet")
    print(f"ml.rain_model: v0 (stage) vs v1 (stage + rain {a.radius_km:g} km, lag {a.rain_lag_h:g} h), split "
          f"{a.split}, h={horizons}; rain from {rain_path}")
    if not os.path.exists(rain_path):
        raise SystemExit(f"ERROR: {rain_path} missing; run python -m ml.mrms_history first")
    rain = pd.read_parquet(rain_path)
    gauges = iter_gauges(a.gauges, a.gauges_only, a.start, a.end, config.DATA_DIR)
    df, info = build_rows(gauges, rain, a.radius_km, a.rain_lag_h,
                          progress=lambda it, desc, total=None: bar(it, desc, count_gauges(a.gauges, a.gauges_only)))
    if df.empty:
        raise SystemExit("ERROR: no window has 24 h of rain; check the M2.1 output")
    split = _ts(a.split)
    train, test = df[df.t < split].copy(), df[df.t >= split].copy()
    for name, part in (("train", train), ("test", test)):
        n_c = sum(len(crossings_in_rows([c for c in v["crossings"] if (c[0] < split) == (name == "train")],
                                        part[part.site_no == s].t.to_numpy(), 3)) for s, v in info.items())
        yrs = sorted({datetime.fromtimestamp(c[0], timezone.utc).year for v in info.values() for c in v["crossings"]
                      if (c[0] < split) == (name == "train") and c[0] >= df.t.min()})
        print(f"  {name}: {len(part)} rows, {part.site_no.nunique()} gauges, {n_c} minor crossings with a row in "
              f"the 3 h before (flood years {yrs})")
        if n_c == 0:
            raise SystemExit(f"ERROR: no crossing on the {name} side of --split {a.split}; move the split")
    os.makedirs(a.models_dir, exist_ok=True)
    rows, pr_rows = [], []
    for h in bar(horizons, "horizons"):
        models = {"gbm_v0_stage": fit_one(train, V0, h, info), "gbm_v1_stage_rain": fit_one(train, V1, h, info)}
        for name, m in models.items():
            joblib.dump({**m, "trained_on": f"t < {a.split}", "radius_km": a.radius_km, "rain_lag_h": a.rain_lag_h},
                        os.path.join(a.models_dir, f"{name}_h{h}.joblib"))
        r, pr = evaluate_models(test, models, h, info, a.label)
        rows += r
        for name, (y, pp) in pr.items():
            pr_rows.append(pd.DataFrame({"model": name, "horizon_h": h, "y": y, "p": pp}))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    harness.write_csv(rows, a.out)
    pd.concat(pr_rows).to_parquet(a.out.replace(".csv", "_scores.parquet"), index=False)
    if a.insert:
        harness.insert_rows(rows)
    print("\n  model              view         h  P     R     F1    FA   lead gross/net")
    for r in rows:
        if r["site_no"].startswith("POOLED"):
            print(f"  {r['model']:18s} {r['site_no']:12s} {r['horizon_h']:<2} {harness._fmt(r['precision'])}  "
                  f"{harness._fmt(r['recall'])}  {harness._fmt(r['f1'])}  {r['false_alarms']:<4} {r['notes']}")
    print(f"\ndone: {len(rows)} rows ({len(train)} train / {len(test)} test windows) -> {a.out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
