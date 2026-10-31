"""Table 1: measured cells come only from audit rows; India cells say pending when nothing was downloaded."""
from india import table1 as T

US = [{"site_no": "1", "n_readings": 100, "interval_min": 15.0, "archive_days": 3652.5, "missing_pct": 1.0,
       "longest_gap_h": 30.0, "events": 4, "median_rise_h": 6.0, "rise_resolvable": "True"},
      {"site_no": "2", "n_readings": 100, "interval_min": 5.0, "archive_days": 1826.25, "missing_pct": 3.0,
       "longest_gap_h": 10.0, "events": 0, "median_rise_h": "", "rise_resolvable": ""}]


def test_india_pending_without_downloads():
    t = T.build_table(US, [], 38)
    by = {r["property"]: r for r in t}
    assert by["Reporting interval (median per station)"]["us_usgs"].startswith("10 min")
    assert all(r["india_measured"] in (T.PENDING, "", "not measurable from archive files") for r in t)
    assert "4 total" in by["Events above the threshold (US: NWPS minor; India: CWC danger level)"]["us_usgs"]
    assert by["Rise time resolvable (interval < median rise)"]["us_usgs"] == "1 of 1 stations with events"
    md = T.to_markdown(t)
    assert md.count("\n") == len(t) + 2


def test_india_measured_when_audited():
    ind = [dict(US[0], site_no="X", interval_min=1440.0, rise_resolvable="False")]
    t = T.build_table(US, ind, 38, 1)
    by = {r["property"]: r for r in t}
    assert by["Reporting interval (median per station)"]["india_measured"].startswith("1440 min")
    assert "1 stations with levels" in by["Official thresholds machine-readable"]["india_measured"]
