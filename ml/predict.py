"""Forecaster entry point called by streams/forecaster.py (contract: docs/CONTRACTS.md, "ML model").

    predict(features) -> {forecast_stage, p_action, p_minor, p_moderate, p_major, horizon_h, model}

`features` is the latest `aggregates.window` record for the gauge, plus `history` (earlier windows, oldest first)
and `official_levels` ({action, minor, moderate, major}). Optional `horizon_h` (default 3).

This is the baseline stub: the 3 h rise-rate extrapolation, no probabilities (None = the model gives none).
It never raises; on any error it still returns a rise-rate record. The trained model replaces `_model_predict`
in S3; `predict()` keeps falling back to the baseline.
"""
from __future__ import annotations

from ml import baselines

DEFAULT_HORIZON_H = 3.0
BASELINE_NAME = "rise_rate"
_EMPTY = {"forecast_stage": None, "p_action": None, "p_minor": None, "p_moderate": None, "p_major": None}


def _baseline(features: dict, horizon_h: float) -> dict:
    return {**_EMPTY, "forecast_stage": baselines.rise_rate(features, horizon_h),
            "horizon_h": horizon_h, "model": BASELINE_NAME}


def _model_predict(features: dict, horizon_h: float) -> dict | None:
    """Trained model hook (S3). None means 'no model loaded, use the baseline'."""
    return None


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
