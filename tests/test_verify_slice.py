from tools import verify_slice as vs


def test_declared_objects_reads_tables_and_views():
    sql = "CREATE TABLE IF NOT EXISTS a (x int);\nCREATE OR REPLACE VIEW v AS SELECT 1;\nCREATE TABLE IF NOT EXISTS b (y int);"
    assert vs.declared_objects(sql) == ({"a", "b"}, {"v"})


def test_real_schema_declares_13_tables_and_5_views():
    tables, views = vs.declared_objects((vs.ROOT / "storage" / "schema.sql").read_text(encoding="utf-8"))
    _, more = vs.declared_objects((vs.ROOT / "storage" / "views.sql").read_text(encoding="utf-8"))
    assert len(tables) == 13 and len(views | more) == 5


def test_missing_lists_what_is_gone():
    assert vs.missing(["a", "b"], ["a", "b", "c"]) == ""
    assert vs.missing(["b", "a", "z"], ["a"]) == "missing: b, z"


def test_newest_drill_per_scenario_wins():
    rows = [("fault_drill:sink", None, "FAILED: x"), ("fault_drill:sink", 5.0, "ok")]
    assert vs.latest_drills(rows) == {"sink": (5.0, "ok")}


def test_drills_verdict_needs_every_scenario_passed():
    good = {s: (1.0, "ok") for s in vs.SCENARIOS}
    assert vs.drills_verdict(good)[0]
    assert not vs.drills_verdict({k: v for k, v in good.items() if k != "broker"})[0]      # one never ran
    assert not vs.drills_verdict({**good, "sink": (None, "x")})[0]                         # did not recover
    ok, text = vs.drills_verdict({**good, "sink": (5.0, "...; FAILED: check_sink")})       # newest run failed
    assert not ok and "not passed: sink" in text


def test_alert_must_come_before_the_crossing():
    w = [("2019-05-04T01:00:00+00:00", 3.0), ("2019-05-04T02:00:00+00:00", 8.2), ("2019-05-04T03:00:00+00:00", 9.0)]
    assert vs.alert_before_crossing(w, "2019-05-04T01:00:00+00:00", 5.5)[0]
    assert not vs.alert_before_crossing(w, "2019-05-04T02:00:00+00:00", 5.5)[0]      # same window: not before it
    assert not vs.alert_before_crossing(w, None, 5.5)[0]
    assert vs.alert_before_crossing(w[:1], "2019-05-04T01:00:00+00:00", 5.5)[0]      # never crossed


def test_episode_lead_time_is_reported_negative_allowed():
    w = [("2019-05-04T01:00:00+00:00", 4.0), ("2019-05-04T02:00:00+00:00", 6.0)]
    ok, text = vs.episode_lead_times([("s|minor|2019-05-04T01:00:00Z", "new"), ("s|major|2019-05-04T01:00:00Z", "upgrade")], w, 5.5)
    assert ok and "+1.0 h gross, +0.0 h net" in text
    ok, text = vs.episode_lead_times([("s|minor|2019-05-04T02:00:00Z", "new")], w, 5.5)     # same window: passes, reported as it is
    assert ok and "+0.0 h gross, -1.0 h net" in text
    assert not vs.episode_lead_times([], w, 5.5)[0]
    assert not vs.episode_lead_times([("s|minor|2019-05-04T01:00:00Z", "new"), ("s|major|2019-05-04T01:00:00Z", "new")], w, 5.5)[0]
    assert "never reached" in vs.episode_lead_times([("s|minor|2019-05-04T01:00:00Z", "new")], w[:1], 5.5)[1]


def test_verdict_never_passes_with_a_failure_and_counts_manual_steps():
    good = ("core", "a", True, "")
    bad = ("ml", "b", False, "")
    ok, text = vs.verdict([good, bad], 2)
    assert not ok and text.startswith("FAIL: 1 of 2") and "failed: b" in text
    ok, text = vs.verdict([good], 3)
    assert ok and "3 manual step(s) still need a person" in text


def test_a_check_that_raises_is_a_failure():
    def boom():
        raise OSError("connection refused")
    ok, detail = vs.run_check_safely(boom)
    assert not ok and "connection refused" in detail


def test_s2_added_and_s1_checks_unchanged():
    assert set(vs.CHECKS) == {"S1", "S2"}
    assert len(vs.CHECKS["S1"]) == 19 and vs.CHECKS["S1"][0][1] == "tests green"
    assert vs.CHECKS["S1"][-1][2] is vs.check_agent_v2
    assert all(part == "core" for part, _, _ in vs.CHECKS["S2"]) and len(vs.MANUAL["S2"]) == 2


W = ["2023-10-26T%02d:00:00+00:00" % h for h in range(24)]


def test_rain_peak_before_crossing_passes_and_reports_the_lead():
    ws = [(W[h], 2.0 if h < 15 else 8.0, {12: 11.1, 14: 7.7, 18: 8.7}.get(h, 0.2)) for h in range(20)]
    ok, txt = vs.rain_before_stage(ws, 7.0)
    assert ok and "+3 h before" in txt and W[12] in txt and W[15] in txt


def test_rain_peak_after_crossing_or_no_rain_fails():
    ws = [(W[h], 2.0 if h < 10 else 8.0, {12: 11.1}.get(h, 0.2)) for h in range(20)]
    assert not vs.rain_before_stage(ws, 7.0)[0]                       # wettest hour came after the rise
    ws = [(W[h], 2.0 if h < 10 else 8.0, None) for h in range(20)]
    ok, txt = vs.rain_before_stage(ws, 7.0)
    assert not ok and "no window with rain_1h" in txt
    assert vs.rain_before_stage([(W[0], 2.0, 5.0)], 7.0) == (False, "stage never reached 7 ft")
    ws = [(W[h], 2.0 if h < 15 else 8.0, 0.0) for h in range(20)]
    assert not vs.rain_before_stage(ws, 7.0)[0]                       # a dry event never passes
