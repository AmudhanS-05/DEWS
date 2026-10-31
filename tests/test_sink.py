import pytest

from common import config
from storage import sink
from tools.check_sink import compare

READING = {"type": "reading", "site_no": "08158700", "parameter": "00065", "series": "s1", "value": 2.6, "unit": "ft",
           "qualifiers": ["P"], "reading_time": "2019-05-04T03:00:00Z", "reading_ts": 1556938800.0,
           "fetched_at": "2019-05-04T03:05:00Z", "mode": "SIMULATION"}
AGG = {"site_no": "08158700", "mode": "SIMULATION", "window_start": "2019-05-04T03:00:00Z",
       "window_end": "2019-05-04T04:00:00Z", "n_readings": 12, "stage_last": 2.6, "stage_max": 2.7, "stage_min": 2.5,
       "rise_rate_ft_per_h": 0.7, "provisional": True}
FC = {"site_no": "08158700", "mode": "SIMULATION", "window_start": "2019-05-04T03:00:00Z", "model": "rise_rate",
      "horizon_h": 6, "forecast_stage": 6.8, "p_minor": 0.79, "made_at": "2026-10-31T10:00:00Z",
      "trace": {"reading_ts": 1556938800.0, "fetched_ts": 1556939100.0, "window_close_time": 1556942400.0}}
ALERT = {"alert_id": "08158700|minor|2019-05-04T03:00:00Z", "kind": "new", "site_no": "08158700", "mode": "SIMULATION",
         "category": "minor", "probability": 0.79, "horizon_h": 6, "provisional_data": True,
         "raised_at": "2019-05-04T04:00:00Z"}


def test_heartbeat_is_skipped():
    assert sink.route(config.TOPIC_GAUGE, {"type": "heartbeat", "site_no": "x", "mode": "LIVE"}) == []


def test_reading_routes_to_gauge_readings():
    (table, row), = sink.route(config.TOPIC_REPLAY_GAUGE, READING)
    assert table == "gauge_readings" and row[0] == "08158700" and row[6] == ["P"] and row[7] == "SIMULATION"


def test_window_forecast_and_alert_routes():
    assert sink.route(config.TOPIC_AGG, AGG)[0][0] == "windowed_aggregates"
    assert [t for t, _ in sink.route(config.TOPIC_FORECASTS, FC)] == ["risk_scores", "latency_trace"]
    (t, row), (t2, _) = sink.route(config.TOPIC_ALERTS, ALERT)
    assert (t, t2) == ("alerts", "advisories") and row[-1] == "draft"
    (t, row), (t2, _) = sink.route(config.TOPIC_ALERTS, dict(ALERT, kind="retract"))
    assert (t, t2) == ("alerts", "advisory_retract") and row[-1] == "retracted"


def test_missing_field_raises_for_the_caller_to_skip():
    with pytest.raises(KeyError):
        sink.route(config.TOPIC_AGG, {"site_no": "x"})


def test_last_wins_keeps_newest_row_per_key():
    out = sink.last_wins("windowed_aggregates", [sink.window_row(AGG), sink.window_row(dict(AGG, stage_last=9.9))])
    assert len(out) == 1 and out[0][5] == 9.9


def test_compare_reports_gaps_duplicates_extra():
    assert compare({1, 2, 3}, [1, 2, 2, 4]) == {"topic": 3, "table": 3, "gaps": 1, "duplicates": 1, "extra": 1}
