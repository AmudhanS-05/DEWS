"""Harness mechanics on synthetic hydrographs (tests the code; the numbers are not findings)."""
from ml import baselines, harness
from tests.ml.synth import CATS, hydrograph

SLOW = hydrograph("SLOW", days=90, seed=1, n_storms=4, rise_h=8.0)
FAST = hydrograph("FAST", days=90, seed=2, n_storms=3, rise_h=1.0)
RBS, CBS = {"SLOW": SLOW, "FAST": FAST}, {"SLOW": CATS, "FAST": CATS}


def rows(fn=baselines.rise_rate, h=3.0):
    return harness.evaluate_model(RBS, CBS, "rise_rate", fn, h, "t")


def test_rows_per_gauge_pooled_and_slow_and_columns():
    r = rows()
    assert [x["site_no"] for x in r] == ["SLOW", "FAST", "POOLED", "POOLED-SLOW"]
    assert all(set(x) == set(harness.COLUMNS) and x["data_source"] == "replay" for x in r)


def test_pooled_counts_are_sums_and_slow_excludes_fast():
    r = {x["site_no"]: x for x in rows()}
    n = lambda k, key: int(r[k]["notes"].split(f"{key}=")[1].split()[0])
    assert n("POOLED", "crossings") == n("SLOW", "crossings") + n("FAST", "crossings") == 7
    assert n("POOLED-SLOW", "crossings") == 4
    assert r["POOLED"]["false_alarms"] == r["SLOW"]["false_alarms"] + r["FAST"]["false_alarms"]


def test_persistence_never_alerts_early_rise_rate_does():
    pers = {x["site_no"]: x for x in rows(baselines.persistence)}["SLOW"]
    rr = {x["site_no"]: x for x in rows()}["SLOW"]
    assert pers["recall"] == 0.0 or pers["recall"] is not None and pers["recall"] <= rr["recall"]
    assert rr["recall"] > 0


def test_net_lead_is_gross_minus_feed_delay():
    note = {x["site_no"]: x for x in rows()}["SLOW"]["notes"]
    g = float(note.split("lead_gross_h=")[1].split()[0]); n = float(note.split("lead_net_h=")[1].split()[0])
    assert abs((g - n) - 1.0) < 0.011


def test_distinct_storms_clusters_nearby_crossings():
    H = 3600
    assert harness.distinct_storms([0, 5 * H, 100 * H, 110 * H, 300 * H]) == 3
    assert harness.distinct_storms([]) == 0
