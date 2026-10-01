"""Gate check end-to-end on SYNTHETIC data (network calls replaced), to test the
counting and kill-rule logic only."""
from datetime import datetime, timedelta, timezone

from tools import gate_check

NWPS_DOC = {"lid": "TEST1", "name": "Synthetic gauge",
            "flood": {"stageUnits": "ft", "categories": {"action": {"stage": 8}, "minor": {"stage": 10},
                                                        "moderate": {"stage": 12}, "major": {"stage": 14}}}}


def year_doc(year, n_events, rise_h):
    pts = []
    t = datetime(year, 1, 1, tzinfo=timezone(timedelta(hours=-6)))
    for e in range(n_events):
        start = t + timedelta(days=20 * e + 1)
        for k in range(0, int(rise_h * 4) + 1):          # 15-min steps rising 2 -> 11 ft
            v = 2 + 9 * k / (rise_h * 4)
            pts.append((start + timedelta(minutes=15 * k), v))
        pts.append((start + timedelta(hours=rise_h + 6), 2.0))   # back to base
    vals = [{"value": f"{v:.2f}", "qualifiers": ["A"], "dateTime": ts.isoformat()} for ts, v in pts]
    return {"value": {"timeSeries": [{"sourceInfo": {"siteName": "S", "siteCode": [{"value": "0000001"}]},
                                      "variable": {"variableCode": [{"value": "00065"}], "unit": {"unitCode": "ft"},
                                                   "noDataValue": -999999.0},
                                      "values": [{"value": vals, "method": [{"methodID": 1}]}]}]}}


def run(monkeypatch, n_events, rise_h):
    monkeypatch.setattr(gate_check, "get_json", lambda url: NWPS_DOC)
    monkeypatch.setattr(gate_check, "fetch_year", lambda site, y, refresh: year_doc(y, n_events, rise_h))
    return gate_check.check_gauge({"usgs_site": "0000001", "nwps_lid": "TEST1"}, years=2,
                                  category="minor", min_events=10, min_rise_h=3.0, refresh=False)


def test_pass_when_enough_slow_events(monkeypatch):
    s, ev = run(monkeypatch, n_events=6, rise_h=5)       # 6 per year x 2 years = 12 events
    assert s["n_events"] == 12 and s["verdict"] == "PASS", s
    assert 4.0 <= s["median_rise_h"] <= 5.0


def test_fail_when_rise_too_fast(monkeypatch):
    s, _ = run(monkeypatch, n_events=6, rise_h=1)
    assert s["verdict"] == "FAIL" and "median rise time" in s["reasons"]


def test_fail_when_too_few_events(monkeypatch):
    s, _ = run(monkeypatch, n_events=2, rise_h=5)
    assert s["verdict"] == "FAIL" and "only 4 events" in s["reasons"]


def test_error_is_not_a_verdict(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("proxy 403")
    monkeypatch.setattr(gate_check, "get_json", boom)
    monkeypatch.setattr(gate_check, "fetch_year", boom)
    s, _ = gate_check.check_gauge({"usgs_site": "1", "nwps_lid": "X"}, 1, "minor", 10, 3.0, False)
    assert s["verdict"] == "ERROR"
