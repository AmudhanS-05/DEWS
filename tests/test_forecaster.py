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
