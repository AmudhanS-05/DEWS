"""Forecaster entry point called by streams/forecaster.py (contract: docs/CONTRACTS.md, "ML model").

    predict(features) -> {forecast_stage, p_action, p_minor, p_moderate, p_major, horizon_h, model}

`features` is the latest `aggregates.window` record for the gauge, plus `history` (earlier windows, oldest first)
and `official_levels` ({action, minor, moderate, major}). Optional `horizon_h` (default 3).

Default: the rise-rate extrapolation, no probabilities (None = the model gives none). A trained S3 model
(ml/s3_model.py) is used ONLY when `ml/models/active.json` names one, e.g. {"name": "s3_v0"}; it then loads
`ml/models/<name>_h<h>.joblib` once per horizon and returns p_minor (the only category it was trained for; p_action,
p_moderate, p_major stay None), forecast_stage from its stage regressor, horizon_h and model = <name>. Live features
are built by `model_features` exactly as the training frame (ml.dataset.build_site_columns + 12/24 h changes;
rain_1h/3h/6h from the window for v1). It never raises: any failure (no file, missing horizon, missing stage,
a stack v1 without rain) returns the rise-rate record.
"""
from __future__ import annotations

import json
import os

from ml import baselines

DEFAULT_HORIZON_H = 3.0
BASELINE_NAME = "rise_rate"
_EMPTY = {"forecast_stage": None, "p_action": None, "p_minor": None, "p_moderate": None, "p_major": None}


def _baseline(features: dict, horizon_h: float) -> dict:
    return {**_EMPTY, "forecast_stage": baselines.rise_rate(features, horizon_h),
            "horizon_h": horizon_h, "model": BASELINE_NAME}


def _model_predict(features: dict, horizon_h: float) -> dict | None:
    """Trained model hook (S3). None means 'no model loaded, use the baseline'."""
    m = _load_model(horizon_h)
    if m is None:
        return None
    from threadpoolctl import threadpool_limits
    with threadpool_limits(1, user_api="openmp"):     # one-row predictions: OpenMP threads only add latency
        return _score(m, features, horizon_h)


def _score(m: dict, features: dict, horizon_h: float) -> dict | None:
    X = model_features(features, features.get("official_levels") or {})
    if X is None:
        return None
    if m["kind"] == "v1":
        base = m["base_model"]
        x0 = [[X[f] for f in base["features"]]]
        p0 = base["clf"].predict_proba(x0)[:, 1]
        rain = [X.get(k) for k in m["rain_features"]]
        if any(v is None for v in rain):
            return None
        import numpy as np
        xr = np.array([rain], dtype=float)
        xj = [[X[f] for f in m["v0_features"]] + rain]
        p = float(m["model"].predict_proba_from(p0, xr, xj)[0])
        reg, feats = base["reg"], base["features"]
    else:
        p = float(m["clf"].predict_proba([[X[f] for f in m["features"]]])[0, 1])
        reg, feats = m["reg"], m["features"]
    stage = float(reg.predict([[X[f] for f in feats]])[0]) + float(features["official_levels"]["minor"])
    return {**_EMPTY, "p_minor": p, "forecast_stage": stage, "horizon_h": horizon_h, "model": m["name"]}


MODELS_DIR = os.getenv("ML_MODELS_DIR", os.path.join(os.path.dirname(__file__), "models"))
_CACHE: dict = {}


def _active() -> str | None:
    if "active" not in _CACHE:
        try:
            with open(os.path.join(MODELS_DIR, "active.json"), encoding="utf-8") as f:
                _CACHE["active"] = json.load(f).get("name")
        except Exception:  # noqa: BLE001 - no active model: baseline
            _CACHE["active"] = None
    return _CACHE["active"]


def _load_model(horizon_h: float):
    name = _active()
    if not name or float(horizon_h) != int(horizon_h):
        return None
    key = (name, int(horizon_h))
    if key not in _CACHE:
        import joblib
        path = os.path.join(MODELS_DIR, f"{name}_h{int(horizon_h)}.joblib")
        if not os.path.exists(path):
            _CACHE[key] = None
        else:
            m = joblib.load(path)
            m["name"] = name
            if m.get("kind") == "v1":
                m["base_model"] = joblib.load(os.path.join(MODELS_DIR, m["base"]))
            _strip_names(m)
            _CACHE[key] = m
    return _CACHE[key]


def _strip_names(obj) -> None:
    """Models were fitted on named DataFrame columns; live calls pass lists in the same fixed order. Dropping the
    stored names avoids a sklearn warning on every call (order is guaranteed by the feature lists in the file)."""
    if isinstance(obj, dict):
        for v in obj.values():
            _strip_names(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strip_names(v)
    elif hasattr(obj, "models"):
        _strip_names(obj.models)
    elif hasattr(obj, "feature_names_in_"):
        try:
            del obj.feature_names_in_
        except AttributeError:
            pass


def model_features(agg: dict, levels: dict) -> dict | None:
    """Training-frame features from one window + its history (see ml.dataset.build_site_columns)."""
    cur = agg.get("stage_last")
    minor, action = levels.get("minor"), levels.get("action")
    if cur is None or minor is None or action is None:
        return None
    cur, minor, action = float(cur), float(minor), float(action)
    nan = float("nan")
    smax, smin = agg.get("stage_max"), agg.get("stage_min")
    X = {"rel_minor": cur - minor, "rel_action": cur - action, "frac_to_minor": cur / minor if minor else nan,
         "rise_rate": float(agg.get("rise_rate_ft_per_h") or 0.0),
         "stage_range": (float(smax) if smax is not None else cur) - (float(smin) if smin is not None else cur)}
    t = agg.get("window_start_ts")
    by_t = {h.get("window_start_ts"): h.get("stage_last") for h in (agg.get("history") or [])
            if h.get("stage_last") is not None}
    for lag in (1, 3, 6, 12, 24):
        past = by_t.get(t - lag * 3600) if t is not None else None
        X[f"d{lag}h"] = cur - float(past) if past is not None else nan
    for k in ("rain_1h", "rain_3h", "rain_6h"):
        X[k] = None if agg.get(k) is None else float(agg[k])
    return X


def predict(features: dict) -> dict:
    horizon_h = DEFAULT_HORIZON_H
    try:
        horizon_h = float(features.get("horizon_h") or DEFAULT_HORIZON_H)
        out = _model_predict(features, horizon_h)
        return out if out is not None else _baseline(features, horizon_h)
    except Exception:
        try:
            return _baseline(features, horizon_h)
        except Exception:
            return {**_EMPTY, "horizon_h": horizon_h, "model": BASELINE_NAME}
