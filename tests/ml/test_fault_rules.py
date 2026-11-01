"""ml.fault_rules on hand-built series: each rule fires on its fault shape and not on a sustained rise."""
import numpy as np

from ml import fault_rules as R

H = 3600.0


def series(n=200, step=900.0, base=2.0):
    t = np.arange(n) * step + 1_700_006_400.0
    return t, np.full(n, base)


def ctx(moving_neighbor=False, rain=None):
    t = np.arange(0, 400) * H + 1_700_006_400.0 - 50 * H
    v = np.where(np.arange(400) % 2 == 0, 1.0, 1.3) if moving_neighbor else np.full(400, 1.0)
    r = {} if rain is None else {"S": {float(h): rain for h in t}}
    return R.Context({"N": (t, v)}, r, {"S": ["N"]})


def test_gap_rule():
    t, v = series()
    t2 = np.concatenate([t[:50], t[70:]])            # 20 readings x 15 min = 5 h without readings
    assert len(R.rule_gap(t2, v[:len(t2)])) == 1 and R.rule_gap(t, v) == []


def test_spike_returns_but_rise_does_not():
    t, v = series()
    v[100] += 1.0
    assert R.rule_spike(t, v, 0.5) == [t[101]]
    v2 = series()[1].copy()
    v2[100:] += np.linspace(0.6, 6, 100)             # big first jump (0.6 ft), then a sustained rise: no return
    assert R.rule_spike(t, v2, 0.5) == []


def test_stuck_needs_independent_movement():
    t, v = series()                                  # 50 h identical values
    assert R.rule_stuck(t, v, "S", ctx(moving_neighbor=False), 6) == []          # everything quiet: not decidable
    assert len(R.rule_stuck(t, v, "S", ctx(moving_neighbor=True), 6)) >= 1      # neighbour moves: stuck
    assert len(R.rule_stuck(t, v, "S", ctx(rain=3.0), 6)) >= 1                  # rain fell: stuck


def test_step_needs_quiet_evidence_and_holds():
    t, v = series()
    v[100:] += 0.8
    assert R.rule_step(t, v, "S", ctx(moving_neighbor=False), 0.4) == [t[100 + R.STEP_HOLD]]
    assert R.rule_step(t, v, "S", ctx(moving_neighbor=True), 0.4) == []         # neighbour moved: maybe real
    assert R.rule_step(t, v, "S", R.Context({}, {}, {}), 0.4) == []            # no evidence: no flag
    v2 = series()[1].copy()
    v2[100:] += np.linspace(0.8, 5, 100)             # jump, then keeps rising (flood): not a step
    assert R.rule_step(t, v2, "S", ctx(), 0.4) == []


def test_check_readings_contract():
    t, v = series()
    v[50] += 2.0
    rs = [{"reading_ts": a, "value": b} for a, b in zip(t, v)]
    out = R.check_readings(rs, "S", None, 0.3, {"spike": 2.0, "stuck": 24.0, "step": 2.0})
    assert out["suspect"] and out["reasons"] == ["spike"]


def test_check_gauge_ships_only_accepted_rules_and_never_raises():
    t, v = series()
    v[50] += 2.0                                       # spike: detected by the rule but not shipped
    v[120:] += 1.0                                     # step with a quiet neighbour
    rs = [{"reading_ts": a, "value": b} for a, b in zip(t, v)]
    cfg = {"shipped": ["no_readings", "stuck", "step"], "settings": {"spike": 2.0, "stuck": 24.0, "step": 1.0},
           "scales": {"S": 0.3}, "default_scale": 0.3}
    out = R.check_gauge(rs, "S", ctx(), cfg)
    assert out["suspect"] and out["reasons"] == ["step"] and "spike" not in out["flags"]
    assert R.check_gauge(None, "S", None, cfg) == {"suspect": False, "reasons": ["unavailable"], "flags": {}}


def test_context_from_windows():
    ws = [{"window_start_ts": 3600.0 * k, "stage_last": 1.0 + (k % 2) * 0.5} for k in range(10)]
    c = R.Context.from_windows({"N": ws}, {"S": {7200: 2.0}}, {"S": ["N"]})
    assert c.nb_moves("S", 0, 10 * 3600) == (1, 1) and c.rain_sum("S", 3600, 7200) == 2.0


def test_committed_config_loads():
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(R.__file__), "models", "fault_rules.json")))
    assert cfg["shipped"] == ["no_readings", "stuck", "step"] and len(cfg["scales"]) == 38
