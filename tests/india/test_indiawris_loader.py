"""India-WRIS loader on the hand-made fixture (assumed layout; not a real download)."""
from datetime import time

from india import indiawris_loader as L

FIX = "tests/india/fixtures/indiawris_sample.xlsx"


def test_fixture_parses_into_reading_records():
    rs = L.load_file(FIX)
    assert len(rs) == 11                                      # 13 data rows - "-" - duplicate date
    r0 = rs[0]
    assert r0["site_no"] == "FIXTURE01" and r0["site_name"] == "Fixture Ghat"
    assert r0["unit"] == "m" and r0["series"] == "INDIA-WRIS" and r0["source"] == "INDIA-WRIS"
    assert r0["reading_time"] == "2019-08-01T02:30:00Z"       # 08:00 IST = 02:30 UTC
    assert [r["reading_ts"] for r in rs] == sorted(r["reading_ts"] for r in rs)
    assert rs[-2]["reading_time"] == "2019-08-11T09:00:00Z"   # 14:30 IST text
    assert rs[-1]["reading_time"] == "2019-08-12T00:45:00Z"   # 06:15 IST Excel datetime
    assert max(r["value"] for r in rs) == 104.60


def test_assume_time_is_configurable():
    rs = L.load_file(FIX, assume_time=time(0, 0))
    assert rs[0]["reading_time"] == "2019-07-31T18:30:00Z"


def test_envelopes_are_simulation():
    env = L.to_envelopes(L.load_file(FIX))
    assert all(e["mode"] == "SIMULATION" and e["source"] == "INDIA-WRIS" and e["type"] == "reading" for e in env)
    assert "meta" not in env[0]


def test_audit_runs_on_india_readings():
    from india.audit import audit_station
    a = audit_station(L.load_file(FIX), threshold=104.0)      # test-only threshold, not a CWC level
    assert a["site_no"] == "FIXTURE01" and a["events"] == 1
    assert 1400 <= a["interval_min"] <= 1440


def test_thresholds_template_and_category_mapping(tmp_path):
    hdr = open("india/thresholds.csv", encoding="utf-8").readline().strip().split(",")
    assert hdr == L.THRESHOLD_COLUMNS
    p = tmp_path / "t.csv"
    p.write_text(",".join(hdr) + "\nX1,Name,R,B,S,17.0,81.0,10.5,11.5,,,CWC bulletin,2025-08-01,\n")
    th = L.load_thresholds(str(p))
    assert L.categories(th["X1"]) == {"action": 10.5, "minor": 11.5, "moderate": None, "major": None}


def test_missing_header_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n")
    try:
        L.load_file(str(p))
    except ValueError as e:
        assert "header row" in str(e)
    else:
        raise AssertionError("expected ValueError")
