"""ml.predict with an S3 model: off unless ml/models/active.json names one; same features as the training frame;
p_minor + forecast_stage; falls back to rise-rate on any problem; fast."""
import json
import time

import numpy as np
import pytest

from ml import predict as P
from ml.backtest import windows_from_readings
from ml.dataset import build_site_columns
from ml.s3_model import lag_change
from tests.ml import synth

LEVELS = {"action": synth.CATS["action"], "minor": synth.CATS["minor"], "moderate": synth.CATS["moderate"],
          "major": synth.CATS["major"]}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(P, "_CACHE", {})
    return tmp_path


def test_live_features_equal_training_frame():
    rs = synth.hydrograph("S1", days=20, seed=3)
    aggs = windows_from_readings(rs, 3600)
    col = build_site_columns(rs, synth.CATS, wins=aggs)
    t, cur = col["t"], col["stage"]
    d12, d24 = lag_change(t, cur, 12), lag_change(t, cur, 24)
    wins = [a for a in aggs if a["stage_last"] is not None]
    for i in (30, 100, 250, len(wins) - 1):
        X = P.model_features({**wins[i], "history": wins[max(0, i - 24):i]}, LEVELS)
        for k in ("rel_minor", "rel_action", "frac_to_minor", "rise_rate", "stage_range", "d1h", "d3h", "d6h"):
            assert X[k] == pytest.approx(col[k][i], nan_ok=True), k
        assert X["d12h"] == pytest.approx(d12[i], nan_ok=True) and X["d24h"] == pytest.approx(d24[i], nan_ok=True)


def test_no_active_file_means_baseline(isolated):
    out = P.predict({"stage_last": 9.0, "rise_rate_ft_per_h": 0.8, "official_levels": LEVELS})
    assert out["model"] == "rise_rate" and out["p_minor"] is None


def _tiny_model(path, feats):
    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(feats)))
    y = (X[:, 0] > 0.5).astype(int)
    joblib.dump({"kind": "v0", "clf": HistGradientBoostingClassifier(max_iter=10).fit(X, y),
                 "reg": HistGradientBoostingRegressor(max_iter=10).fit(X, X[:, 0]), "tau": 0.5,
                 "features": feats, "horizon_h": 3, "category": "minor"}, path)


def test_active_model_returns_probability_and_falls_back(isolated):
    from ml.s3_model import V0_LONG
    _tiny_model(isolated / "s3_v0_h3.joblib", V0_LONG)
    (isolated / "active.json").write_text(json.dumps({"name": "s3_v0"}))
    rs = synth.hydrograph("S1", days=5, seed=1)
    w = [a for a in windows_from_readings(rs, 3600) if a["stage_last"] is not None]
    f = {**w[-1], "history": w[-25:-1], "official_levels": LEVELS}
    out = P.predict(f)
    assert out["model"] == "s3_v0" and 0.0 <= out["p_minor"] <= 1.0 and out["horizon_h"] == 3.0
    assert out["forecast_stage"] is not None and out["p_action"] is None and out["p_major"] is None
    assert P.predict({**f, "horizon_h": 2})["model"] == "rise_rate"          # no file for h=2
    assert P.predict({**f, "stage_last": None})["model"] == "rise_rate"
    assert P.predict({**f, "official_levels": {}})["model"] == "rise_rate"
    t0 = time.perf_counter()
    for _ in range(100):
        P.predict(f)
    assert (time.perf_counter() - t0) / 100 < 0.05


def test_committed_models_load_if_present():
    import os
    path = os.path.join(os.path.dirname(P.__file__), "models", "s3_v0_h3.joblib")
    if not os.path.exists(path):
        pytest.skip("model files not present")
    import joblib
    m = joblib.load(path)
    assert m["kind"] == "v0" and m["horizon_h"] == 3 and 0 < m["tau"] < 1
