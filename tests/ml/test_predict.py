"""ml.predict contract (docs/CONTRACTS.md): keys, baseline values, never raises, under 50 ms."""
import time

import pytest

from ml import predict as P


@pytest.fixture(autouse=True)
def baseline_only(monkeypatch, tmp_path):
    """These tests pin the rise-rate path; the committed ml/models/active.json is tested in test_predict_model.py."""
    monkeypatch.setattr(P, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(P, "_CACHE", {})

KEYS = {"forecast_stage", "p_action", "p_minor", "p_moderate", "p_major", "horizon_h", "model"}
LEVELS = {"action": 8.0, "minor": 10.0, "moderate": 12.0, "major": 14.0}


def feats(**kw):
    f = {"site_no": "S1", "stage_last": 9.0, "rise_rate_ft_per_h": 0.8, "history": [], "official_levels": LEVELS}
    f.update(kw)
    return f


def test_contract_keys_and_baseline_value():
    out = P.predict(feats())
    assert set(out) == KEYS
    assert out["model"] == "rise_rate" and out["horizon_h"] == 3.0
    assert abs(out["forecast_stage"] - 11.4) < 1e-9
    assert all(out[k] is None for k in ("p_action", "p_minor", "p_moderate", "p_major"))


def test_horizon_override_and_falling_stage():
    assert abs(P.predict(feats(horizon_h=6))["forecast_stage"] - 13.8) < 1e-9
    assert P.predict(feats(rise_rate_ft_per_h=-1.0))["forecast_stage"] == 9.0


def test_missing_or_garbage_input_never_raises():
    for bad in ({}, {"stage_last": None}, {"stage_last": "x", "rise_rate_ft_per_h": 1}, {"horizon_h": "abc"}, None):
        out = P.predict(bad)
        assert set(out) == KEYS and out["model"] == "rise_rate"
    assert P.predict({})["forecast_stage"] is None


def test_model_error_falls_back_to_baseline(monkeypatch):
    def boom(f, h):
        raise RuntimeError("bad model")
    monkeypatch.setattr(P, "_model_predict", boom)
    out = P.predict(feats())
    assert out["model"] == "rise_rate" and abs(out["forecast_stage"] - 11.4) < 1e-9


def test_fast():
    t = time.perf_counter()
    for _ in range(200):
        P.predict(feats())
    assert (time.perf_counter() - t) / 200 < 0.05
