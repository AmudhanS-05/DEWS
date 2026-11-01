"""Forecaster rules. The rule tests pin the rise-rate baseline (no ml/models/active.json): since ml commits
active.json naming s3_v0 at 3 h, predict() would otherwise pick the trained model and the expected stages change.
A separate set runs with s3_v0 active and checks the model name, p_minor and that records/state stay JSON-safe."""
import json

import pytest

from ml import predict as predict_mod
from ml.predict import predict
from streams import forecaster

GAUGE = {"name": "Onion Creek near Driftwood", "levels": {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}}
T0 = 1556938800.0  # 2019-05-04T03:00:00Z


def window(i, stage, rate, n=12, mode="SIMULATION"):
    from common.timeutil import epoch_to_iso
    ts = T0 + 3600 * i
    return {"site_no": "08158700", "series": "s", "mode": mode, "window_start": epoch_to_iso(ts),
            "window_end": epoch_to_iso(ts + 3600), "window_start_ts": ts, "n_readings": n, "revision": False,
            "stage_last": stage, "stage_max": stage, "stage_min": stage, "rise_rate_ft_per_h": rate,
            "provisional": True, "last_reading_ts": ts + 3500, "window_close_time": epoch_to_iso(ts + 3700)}


@pytest.fixture(autouse=True)
def baseline_model(request, monkeypatch):
    """Rule tests: no active model -> rise_rate. Tests named test_s3_* use whatever active.json names (s3_v0)."""
    if request.node.name.startswith("test_s3_"):
        predict_mod._CACHE.pop("active", None)
        yield
        predict_mod._CACHE.pop("active", None)
        return
    monkeypatch.setitem(predict_mod._CACHE, "active", (None, None))
    yield


def run(windows):
    st, fcs, alerts = forecaster.new_state(), [], []
    for w in windows:
        fc, al = forecaster.step(st, w, GAUGE, predict, horizon_h=3, now=T0)
        fcs.append(fc)
        alerts += al
    return fcs, alerts, st


def test_one_forecast_per_window_with_trace_and_model():
    fcs, _, _ = run([window(i, 2.0, 0.0) for i in range(4)])
    assert len(fcs) == 4 and {f["model"] for f in fcs} == {"rise_rate"}
    assert fcs[0]["trace"]["reading_ts"] == T0 + 3500 and fcs[0]["trace"]["window_close_time"] == T0 + 3700
    assert fcs[0]["p_minor"] is None            # the baseline gives no probability: never invented


def test_rising_gauge_raises_one_new_alert_before_the_crossing():
    # 2.6 ft rising 0.7 ft/h: 3 h forecasts are 4.7, 5.4, 6.1 (first MINOR, at the third window), 6.8 (still minor)
    ws = [window(0, 2.6, 0.7), window(1, 3.3, 0.7), window(2, 4.0, 0.7), window(3, 4.7, 0.7)]
    _, alerts, _ = run(ws)
    assert [a["kind"] for a in alerts] == ["new"]
    a = alerts[0]
    assert a["category"] == "minor" and a["current_stage"] < 5.5 <= a["forecast_stage"]
    assert a["alert_id"] == "08158700|minor|" + ws[2]["window_start"] and a["episode_start"] == ws[2]["window_start"]
    assert a["mode"] == "SIMULATION" and a["official_levels"]["minor"] == 5.5


def test_alert_carries_the_window_time_not_only_the_wall_clock():
    ws = [window(0, 2.6, 0.7), window(1, 3.3, 0.7), window(2, 4.0, 0.7)]
    _, alerts, _ = run(ws)
    a = alerts[0]
    assert a["window_start"] == ws[2]["window_start"] and a["window_start_ts"] == ws[2]["window_start_ts"]
    assert a["raised_at"] != a["window_start"]      # raised_at stays the wall clock (made_at)


def test_stage_already_in_category_does_not_alert_again():
    _, alerts, _ = run([window(0, 6.0, 0.0), window(1, 6.0, 0.0)])
    assert alerts == []


def test_higher_category_forecast_is_an_upgrade():
    ws = [window(0, 4.0, 0.7), window(1, 4.7, 0.7), window(2, 5.6, 1.0), window(3, 6.6, 1.0)]
    _, alerts, _ = run(ws)
    assert [(a["kind"], a["category"]) for a in alerts] == [("new", "minor"), ("upgrade", "moderate")]
    assert alerts[0]["alert_id"].split("|")[2] == alerts[1]["alert_id"].split("|")[2]   # same episode


def test_unconfirmed_alert_is_retracted_after_calm_windows():
    ws = [window(0, 4.0, 0.7), window(1, 4.2, 0.0), window(2, 4.0, 0.0), window(3, 3.9, 0.0)]
    _, alerts, st = run(ws)
    assert [a["kind"] for a in alerts] == ["new", "retract"]
    assert alerts[0]["alert_id"] == alerts[1]["alert_id"] and st["episode"] is None


def test_reached_alert_is_not_retracted():
    ws = [window(0, 4.0, 0.7), window(1, 5.6, 0.0)] + [window(i, 6.0, -0.1) for i in range(2, 4)]
    _, alerts, _ = run(ws)
    assert [a["kind"] for a in alerts] == ["new"]


def test_zero_filled_window_forecasts_but_never_raises():
    fcs, alerts, _ = run([window(0, 4.0, 0.7, n=0)])
    assert len(fcs) == 1 and alerts == []


def test_state_roundtrip_through_json(tmp_path):
    _, _, st = run([window(0, 4.0, 0.7)])
    p = tmp_path / "s.json"
    forecaster.save_state({"k": st}, p)
    assert forecaster.load_state(p)["k"]["episode"]["raised"]["minor"]["alert_id"].startswith("08158700|minor|")


def test_load_gauges_has_official_levels():
    g = forecaster.load_gauges()
    assert g["08158700"]["levels"]["minor"] == 5.5 and len(g) >= 30


def _s3_active():
    predict_mod._CACHE.pop("active", None)
    name, hs = predict_mod._active()
    return name == "s3_v0" and predict_mod._load_model(3) is not None


def test_s3_v0_at_3h_names_itself_gives_p_minor_and_stays_json_safe(tmp_path):
    pytest.importorskip("joblib")
    if not _s3_active():
        pytest.skip("ml/models/active.json does not name s3_v0 with a 3 h model file")
    ws = [window(i, 4.0 + 0.7 * i, 0.7) for i in range(4)]
    fcs, alerts, st = run(ws)
    assert {f["model"] for f in fcs} == {"s3_v0"}
    assert all(isinstance(f["p_minor"], float) and 0.0 <= f["p_minor"] <= 1.0 for f in fcs)
    assert all(f["p_action"] is None and f["p_major"] is None for f in fcs)    # trained for minor only
    assert all(type(f["forecast_stage"]) is float for f in fcs)                # plain float, not numpy
    json.dumps(fcs, allow_nan=False), json.dumps(alerts, allow_nan=False)
    forecaster.save_state({"k": st}, tmp_path / "s.json")
    assert forecaster.load_state(tmp_path / "s.json")["k"]["history"][-1]["stage_last"] == ws[-1]["stage_last"]


def test_s3_v0_is_3h_only_other_horizons_use_rise_rate():
    if not _s3_active():
        pytest.skip("ml/models/active.json does not name s3_v0 with a 3 h model file")
    st = forecaster.new_state()
    fc, _ = forecaster.step(st, window(0, 4.0, 0.7), GAUGE, predict, horizon_h=1, now=T0)
    assert fc["model"] == "rise_rate" and fc["p_minor"] is None


def test_window_rain_fields_reach_predict():
    """C2.2: the window handed to ml.predict carries rain_1h/3h/6h/24h (s3_v0 is stage-only; kept for display and
    for v1 models that read rain)."""
    seen = []

    def spy(features):
        seen.append(features)
        return predict(features)

    w = dict(window(0, 2.0, 0.0), rain_1h=1.5, rain_3h=4.0, rain_6h=6.5, rain_24h=None, rain_product="P2")
    forecaster.step(forecaster.new_state(), w, GAUGE, spy, horizon_h=3, now=T0)
    assert {k: seen[0][k] for k in ("rain_1h", "rain_3h", "rain_6h", "rain_24h")} == \
        {"rain_1h": 1.5, "rain_3h": 4.0, "rain_6h": 6.5, "rain_24h": None}
    from ml.predict import model_features
    X = model_features(seen[0], GAUGE["levels"])
    assert X["rain_1h"] == 1.5 and X["rain_6h"] == 6.5


def _by_model(flags):
    """A fake model: forecast stage stays low, alert_by_model follows `flags`, one per window."""
    it = iter(flags)
    def p(features):
        f = next(it)
        return {"forecast_stage": features["stage_last"], "p_action": None, "p_minor": 0.9 if f else 0.1, "p_moderate": None, "p_major": None,
                "horizon_h": 3.0, "model": "m", "tau": 0.8, "alert_by_model": f}
    return p


def _run_model(flags, stages=None):
    st, al = forecaster.new_state(), []
    p = _by_model(flags)
    for i, f in enumerate(flags):
        al += forecaster.step(st, window(i, (stages or [4.0] * len(flags))[i], 0.0), GAUGE, p, horizon_h=3, now=T0)[1]
    return al


def test_model_rule_raises_one_minor_alert_and_records_the_rule():
    al = _run_model([False, True, True, False])
    assert [(a["kind"], a["category"], a["alert_rule"], a["tau"]) for a in al] == [("new", "minor", "alert_by_model", 0.8)]


def test_forecast_stage_rule_is_recorded_when_the_model_gives_no_flag():
    fcs, alerts, _ = run([window(0, 4.0, 1.0), window(1, 5.0, 1.0)])
    assert alerts and all(a["alert_rule"] == "forecast_stage" and a["tau"] is None for a in alerts)


def test_model_saying_no_raises_nothing_even_when_the_stage_is_above_minor():
    assert _run_model([False, False], [4.0, 20.0]) == []                     # too fast to warn: no alert after the fact


def test_model_alert_stands_for_its_horizon_then_is_withdrawn_and_cools_down():
    al = _run_model([True, False, False, False, True, False, False, False, False])
    assert [a["kind"] for a in al] == ["new", "retract"]                     # retract after 3 quiet windows; True in the cooldown raises nothing


def test_model_alert_reached_is_not_withdrawn():
    al = _run_model([True, False, False, False, False], [4.0, 6.0, 6.0, 4.0, 4.0])
    assert [a["kind"] for a in al] == ["new"]
