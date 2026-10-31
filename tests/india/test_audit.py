"""Audit mechanics on synthetic series (not findings)."""
from india import audit
from tests.ml.synth import CATS, hydrograph


def test_interval_archive_events_and_resolvable_rise():
    a = audit.audit_station(hydrograph("S", days=60, seed=1, n_storms=3, rise_h=8.0), CATS["minor"])
    assert a["site_no"] == "S" and a["interval_min"] == 15.0
    assert abs(a["archive_days"] - 60) < 0.1 and a["missing_pct"] < 1 and a["events"] == 3
    assert a["rise_resolvable"] is True


def test_gap_and_daily_data_not_resolvable():
    rs = hydrograph("S", days=60, seed=1, n_storms=3, rise_h=8.0)
    gappy = [r for i, r in enumerate(rs) if not 1000 <= i < 1400]
    a = audit.audit_station(gappy, CATS["minor"])
    assert a["longest_gap_h"] >= 100 and a["missing_pct"] > 5
    daily = [r for i, r in enumerate(rs) if i % 96 == 0]
    d = audit.audit_station(daily, CATS["minor"])
    assert d["interval_min"] == 1440.0 and d["rise_resolvable"] is False


def test_empty_and_no_threshold():
    assert audit.audit_station([], None)["n_readings"] == 0
    assert audit.audit_station(hydrograph("S", days=5), None)["events"] is None
