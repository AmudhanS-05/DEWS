"""Baselines and metrics on made-up numbers (mechanics only)."""
from ml import baselines, evaluate

CATS = {"action": 8.0, "minor": 10.0, "moderate": 12.0, "major": 14.0}
H = 3600


def test_baselines():
    agg = {"stage_last": 9.0, "rise_rate_ft_per_h": 0.8}
    assert baselines.persistence(agg, 3) == 9.0
    assert abs(baselines.rise_rate(agg, 3) - 11.4) < 1e-9
    assert baselines.rise_rate({"stage_last": 9.0, "rise_rate_ft_per_h": -1.0}, 3) == 9.0
    assert baselines.rise_rate({"stage_last": None}, 3) is None


def test_alert_category_uses_official_thresholds():
    assert baselines.alert_category(11.4, CATS) == "minor"
    assert baselines.alert_category(7.9, CATS) is None
    assert baselines.alert_category(8.5, CATS, min_category="minor") is None


def test_scoring_tp_fa_recall_lead():
    crossings = [(10 * H, "minor"), (100 * H, "major")]
    alerts = [(8 * H, "minor"),        # TP, lead 2 h for first crossing
              (9 * H, "minor"),        # TP
              (50 * H, "minor"),       # false alarm
              (97 * H, "moderate")]    # TP for the major crossing (lower category alert), lead 3 h
    s = evaluate.score_alerts(alerts, crossings, horizon_h=3)
    assert s["true_positive_alerts"] == 3 and s["false_alarms"] == 1
    assert s["detected"] == 2 and s["recall"] == 1.0 and s["precision"] == 0.75
    assert s["lead_times_h"] == [2.0, 3.0]
    assert abs(s["f1"] - (2 * 0.75 / 1.75)) < 1e-9


def test_higher_alert_does_not_count_for_lower_crossing():
    s = evaluate.score_alerts([(9 * H, "major")], [(10 * H, "minor")], horizon_h=3)
    assert s["true_positive_alerts"] == 0 and s["detected"] == 0


def test_errors_and_net_lead():
    e = evaluate.stage_errors([(10.0, 9.0), (8.0, 9.0)])
    assert e["mae"] == 1.0 and e["rmse"] == 1.0 and e["bias"] == 0.0
    r = evaluate.report_lead_times([2.0, 3.0], [1.0, 1.5])
    assert r["net_lead_times_h"] == [1.0, 1.5] and r["gross_median_h"] == 2.5


def test_backtest_end_to_end_on_synthetic_floods():
    """Two slow synthetic floods: rise-rate should warn ahead; persistence never can."""
    from ml import backtest
    T0 = 1_790_000_000 - 1_790_000_000 % H
    rs = []
    for e in range(2):
        base = T0 + e * 200 * H
        for k in range(0, 4 * 24):                        # 24 h at 15-min steps
            hrs = k / 4
            v = 2.0 + max(0.0, hrs - 6) * 0.8               # flat 6 h, then +0.8 ft/h
            rs.append({"site_no": "S", "series": "1", "reading_ts": base + k * 900, "value": min(v, 15.0),
                       "qualifiers": ["A"]})
        rs.append({"site_no": "S", "series": "1", "reading_ts": base + 100 * H, "value": 2.0, "qualifiers": ["A"]})
    rr = backtest.run(rs, CATS, "minor", horizon_h=3, model="rise_rate")
    pe = backtest.run(rs, CATS, "minor", horizon_h=3, model="persistence")
    assert rr["crossings"] == 2 and rr["recall"] == 1.0 and rr["median_lead_time_h"] > 0
    assert pe["alerts"] == 0 and pe["recall"] == 0.0
