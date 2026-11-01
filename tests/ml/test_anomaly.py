"""ml.anomaly: causal features that separate fault shapes from a sustained rise, the gap rule, never-raise contract,
fault injection. Synthetic data only (no findings)."""
import numpy as np
import pytest

from ml import anomaly as A
from ml.backtest import windows_from_readings
from tests.ml import synth

F = {k: i for i, k in enumerate(A.FEATURES)}


def _aggs(rs):
    return windows_from_readings(rs, 3600)


def test_features_are_causal():
    rs = synth.hydrograph("S1", days=6, seed=2)
    aggs = _aggs(rs)
    full = A.features_from_arrays(A.window_arrays(aggs), 0.2, 4)
    part = A.features_from_arrays(A.window_arrays(aggs[:60]), 0.2, 4)
    assert np.allclose(full[:60], part)


def _ramp(rise_ft=5.0, hours=6, days=3):
    """Flat 2 ft, then a sustained rise of rise_ft over `hours`, then flat: a fast genuine rise, 15-min readings."""
    out, t0 = [], synth.T0
    for i in range(days * 96):
        t = t0 + i * 900
        x = min(max((i - 96) / (hours * 4), 0.0), 1.0)
        out.append({"site_no": "S1", "series": "1", "reading_ts": t, "value": 2.0 + rise_ft * x,
                    "qualifiers": ["A"], "parameter": "00065"})
    return out


def test_spike_has_excursion_but_a_fast_sustained_rise_does_not():
    rs = _ramp()
    clean = A.features_from_arrays(A.window_arrays(_aggs(rs)), 0.2, 4)
    assert clean[:, F["excursion"]].max() < 1e-9 and clean[:, F["jump_then_flat"]].max() < 1e-9
    bad, _ = A.inject(rs, "spike", rs[29]["reading_ts"], 0.2, np.random.default_rng(0))     # mid-hour reading
    spiked = A.features_from_arrays(A.window_arrays(_aggs(bad)), 0.2, 4)
    assert spiked[:, F["excursion"]].max() > 2.5
    bad, _ = A.inject(rs, "spike", rs[31]["reading_ts"], 0.2, np.random.default_rng(0))     # last reading of an hour
    spiked = A.features_from_arrays(A.window_arrays(_aggs(bad)), 0.2, 4)
    assert spiked[:, F["reversal"]].max() > 2.5                                             # shows as up-then-down


def test_step_dropout_and_flat_signatures():
    rs = synth.hydrograph("S1", days=10, seed=4, n_storms=1)
    rng = np.random.default_rng(0)
    t0 = rs[300]["reading_ts"]
    step, _ = A.inject(rs, "step", t0, 0.2, rng)
    assert A.features_from_arrays(A.window_arrays(_aggs(step)), 0.2, 4)[:, F["jump_then_flat"]].max() > 1
    drop, _ = A.inject(rs, "dropout", t0, 0.2, rng)
    assert A.features_from_arrays(A.window_arrays(_aggs(drop)), 0.2, 4)[:, F["gap_h"]].max() * 6 >= 5
    flat, _ = A.inject(rs, "flatline", t0, 0.2, rng)
    assert A.features_from_arrays(A.window_arrays(_aggs(flat)), 0.2, 4)[:, F["flat_h"]].max() * 24 >= 20


def _model(rs):
    from sklearn.ensemble import IsolationForest
    aggs = _aggs(rs)
    sc = A.gauge_scale(aggs)
    X = A.features_from_arrays(A.window_arrays(aggs), *sc)
    iso = IsolationForest(n_estimators=50, random_state=0).fit(X)
    s = -iso.score_samples(X)
    return {"iforest": iso, "threshold": float(np.quantile(s, 0.999)), "scales": {"S1": sc}, "default_scale": sc[0],
            "default_n": sc[1], "q999": np.quantile(np.abs(X), 0.999, axis=0)}


def test_score_window_contract_and_gap_rule():
    rs = synth.hydrograph("S1", days=30, seed=5)
    m = _model(rs)
    aggs = _aggs(rs)
    out = A.score_window({**aggs[200], "site_no": "S1", "history": aggs[176:200]}, m)
    assert set(out) == {"anomaly_score", "suspect", "reason"} and isinstance(out["suspect"], bool)
    drop, _ = A.inject(rs, "dropout", rs[1500]["reading_ts"], 0.2, np.random.default_rng(0))
    w = _aggs(drop)
    k = next(i for i, a in enumerate(w) if a["window_start_ts"] >= rs[1500]["reading_ts"] + 4 * 3600)
    o = A.score_window({**w[k], "site_no": "S1", "history": w[k - 24:k]}, m)
    assert o["suspect"] and o["reason"] == "no_readings"
    sc, fl = A.score_series(w, m, "S1")
    assert fl[k]


@pytest.mark.parametrize("bad", [None, {}, {"history": "x"}, {"stage_last": "abc", "window_start_ts": 1}])
def test_never_raises(bad):
    out = A.score_window(bad, {"broken": True})
    assert out == {"anomaly_score": None, "suspect": False, "reason": "unavailable"}
