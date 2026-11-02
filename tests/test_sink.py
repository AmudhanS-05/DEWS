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


def test_alert_writes_its_own_trace_row_but_a_retract_does_not():
    tr = {"reading_ts": 1556938800.0, "fetched_ts": 1556939100.0, "ingest_ts": 1556939101.0,
          "window_close_time": 1556942400.0, "forecast_time": "2026-10-31T10:00:00Z"}
    a = dict(ALERT, trace=tr)
    row = dict(sink.route(config.TOPIC_ALERTS, a))["latency_trace_alert"]
    assert row[0] == "08158700|SIMULATION|1556938800" and row[7] == "2026-10-31T10:00:00Z" and len(row) == 9
    assert sink.alert_trace_row(a, now=1793500000.0)[8] == "2026-11-01T02:26:40Z"
    assert "latency_trace_alert" not in dict(sink.route(config.TOPIC_ALERTS, dict(a, kind="retract")))


def test_forecast_trace_row_carries_the_ingest_time():
    fc = dict(FC, trace=dict(FC["trace"], ingest_ts=1556939101.0))
    row = sink.trace_row(fc)
    assert row[5] is not None and row[7] == FC["made_at"] and sink.trace_row(FC)[5] is None


def test_missing_field_raises_for_the_caller_to_skip():
    with pytest.raises(KeyError):
        sink.route(config.TOPIC_AGG, {"site_no": "x"})


def test_last_wins_keeps_newest_row_per_key():
    out = sink.last_wins("windowed_aggregates", [sink.window_row(AGG), sink.window_row(dict(AGG, stage_last=9.9))])
    assert len(out) == 1 and out[0][5] == 9.9


def test_compare_reports_gaps_duplicates_extra():
    assert compare({1, 2, 3}, [1, 2, 2, 4]) == {"topic": 3, "table": 3, "gaps": 1, "duplicates": 1, "extra": 1}


RAIN = {"type": "rain", "rain_id": "08158700|MultiSensor_QPE_01H_Pass2_00.00|1717221600|SIMULATION",
        "site_no": "08158700", "product": "MultiSensor_QPE_01H_Pass2_00.00", "mode": "SIMULATION", "accum_hours": 1,
        "hour_start": "2024-06-01T05:00:00Z", "hour_end": "2024-06-01T06:00:00Z", "hour_end_ts": 1717221600.0,
        "rain_mm": 7.561, "rain_mm_5km": 9.132, "rain_mm_10km": 7.561, "rain_mm_25km": 2.852, "coverage_10km": 1.0,
        "n_cells_10km": 294, "file_time": "2024-06-01T06:58:39Z", "fetched_at": "2026-11-01T01:00:00Z"}


def test_rain_routes_to_catchment_rain_and_heartbeat_is_skipped():
    (t, row), = sink.route(config.TOPIC_REPLAY_RAIN, RAIN)
    assert t == "catchment_rain"
    assert row[:6] == ("08158700", "MultiSensor_QPE_01H_Pass2_00.00", "2024-06-01T06:00:00Z", 1, 7.561, "SIMULATION")
    assert row[6:10] == (9.132, 2.852, 1.0, 294)
    assert sink.route(config.TOPIC_RAIN, {"type": "heartbeat", "site_no": "x", "mode": "LIVE", "product": "p"}) == []
    assert config.TOPIC_RAIN in sink.TOPICS and config.TOPIC_REPLAY_RAIN in sink.TOPICS


def test_rain_keys_dedupe_per_mode_and_window_row_carries_rain_24h():
    a = sink.rain_row(RAIN)
    b = sink.rain_row(dict(RAIN, rain_mm_10km=8.0))
    c = sink.rain_row(dict(RAIN, mode="LIVE"))
    kept = sink.last_wins("catchment_rain", [a, b, c])
    assert len(kept) == 2 and {r[5] for r in kept} == {"SIMULATION", "LIVE"}
    assert [r for r in kept if r[5] == "SIMULATION"][0][4] == 8.0
    row = sink.window_row(dict(AGG, rain_1h=1.0, rain_3h=2.0, rain_6h=3.0, rain_24h=4.0))
    assert row[9:13] == (1.0, 2.0, 3.0, 4.0)


def test_check_sink_covers_the_rain_topics():
    from tools.check_sink import CHECKS
    topics, keyf, sql, _ = CHECKS["catchment_rain"]
    assert topics == [config.TOPIC_RAIN, config.TOPIC_REPLAY_RAIN]
    assert keyf(RAIN) == ("08158700", "MultiSensor_QPE_01H_Pass2_00.00", 1717221600, 1, "SIMULATION")
    assert keyf({"type": "heartbeat"}) is None
