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


def test_horizon_restriction_in_active_json(isolated):
    from ml.s3_model import V0_LONG
    for h in (3, 6):
        _tiny_model(isolated / f"s3_v0_h{h}.joblib", V0_LONG)
    (isolated / "active.json").write_text(json.dumps({"name": "s3_v0", "horizons": [3]}))
    rs = synth.hydrograph("S1", days=5, seed=1)
    w = [a for a in windows_from_readings(rs, 3600) if a["stage_last"] is not None]
    f = {**w[-1], "history": w[-25:-1], "official_levels": LEVELS}
    assert P.predict(f)["model"] == "s3_v0" and P.predict(f)["p_minor"] is not None        # default h = 3
    out6 = P.predict({**f, "horizon_h": 6})
    assert out6["model"] == "rise_rate" and out6["p_minor"] is None                        # file exists, not allowed


def test_repository_activation_is_s3_v0_at_3h_only():
    """The committed ml/models/active.json (team decision 31 Oct 2026)."""
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(P.__file__), "models", "active.json")))
    assert cfg["name"] == "s3_v0" and cfg["horizons"] == [3]


def test_real_model_at_3h_and_fallback_elsewhere(monkeypatch):
    import os
    d = os.path.join(os.path.dirname(P.__file__), "models")
    if not os.path.exists(os.path.join(d, "s3_v0_h3.joblib")):
        pytest.skip("model files not present")
    monkeypatch.setattr(P, "MODELS_DIR", d)
    monkeypatch.setattr(P, "_CACHE", {})
    rs = synth.hydrograph("S1", days=5, seed=1)
    w = [a for a in windows_from_readings(rs, 3600) if a["stage_last"] is not None]
    f = {**w[-1], "history": w[-25:-1], "official_levels": LEVELS}
    out = P.predict(f)
    assert out["model"] == "s3_v0" and 0 <= out["p_minor"] <= 1 and out["horizon_h"] == 3.0
    assert P.predict({**f, "horizon_h": 1})["model"] == "rise_rate"
    assert P.predict({**f, "stage_last": None})["model"] == "rise_rate"


def _windows(stages, t0=1_700_006_400.0):
    """Hourly aggregates.window-like records (as streams/windowing.py emits) for a list of hourly stages."""
    out, prev = [], None
    for k, s in enumerate(stages):
        rr = 0.0 if prev is None else s - prev
        out.append({"window_start_ts": t0 + k * 3600, "stage_last": s, "stage_max": max(s, prev or s),
                    "stage_min": min(s, prev or s), "rise_rate_ft_per_h": rr, "n_readings": 4})
        prev = s
    return out


def _real(monkeypatch):
    import os
    d = os.path.join(os.path.dirname(P.__file__), "models")
    if not os.path.exists(os.path.join(d, "s3_v0_h3.joblib")):
        pytest.skip("model files not present")
    monkeypatch.setattr(P, "MODELS_DIR", d)
    monkeypatch.setattr(P, "_CACHE", {})


DRW = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}       # DRWT2 official levels


def test_forecast_stage_is_feet_near_current_when_steady(monkeypatch):
    _real(monkeypatch)
    w = _windows([3.0] * 26)
    out = P.predict({**w[-1], "history": w[-25:-1], "official_levels": DRW})
    assert out["model"] == "s3_v0" and abs(out["forecast_stage"] - 3.0) < 0.5


def test_forecast_stage_rises_for_a_rising_river(monkeypatch):
    _real(monkeypatch)
    w = _windows([2.0] * 20 + [2.0 + 0.4 * k for k in range(1, 7)])
    out = P.predict({**w[-1], "history": w[-25:-1], "official_levels": DRW})
    assert out["forecast_stage"] > w[-1]["stage_last"]


def test_forecast_stage_stays_near_flood_level_far_above_minor(monkeypatch):
    """Regression test for the 1 Nov 2026 bug: 25.67 ft now gave a 4.21 ft forecast."""
    _real(monkeypatch)
    w = _windows([3.0] * 20 + [8.0, 14.0, 20.0, 24.0, 25.5, 25.67])
    out = P.predict({**w[-1], "history": w[-25:-1], "official_levels": DRW})
    assert abs(out["forecast_stage"] - 25.67) < 5.0
    assert out["alert_by_model"] is False                              # already above minor: no new alert


def test_alert_by_model_matches_tau_below_minor(monkeypatch):
    _real(monkeypatch)
    w = _windows([2.0] * 20 + [2.5, 3.2, 4.0, 4.6, 5.0, 5.3])
    out = P.predict({**w[-1], "history": w[-25:-1], "official_levels": DRW})
    assert out["tau"] is not None and out["alert_by_model"] == (out["p_minor"] >= out["tau"])
    rr = P.predict({**w[-1], "history": w[-25:-1], "official_levels": DRW, "horizon_h": 1})
    assert rr["model"] == "rise_rate" and rr["tau"] is None and rr["alert_by_model"] is None
