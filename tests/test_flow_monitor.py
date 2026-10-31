import time

from common import config
from dashboard import flow_monitor as fm


def raw(**kw):
    now = time.time()
    r = {"ts": now, "kafka_ok": True, "brokers_up": 3, "brokers_expected": 3, "under_replicated": 0,
         "offline_partitions": 0, "pg": {"ok": True, "counts": {}},
         "topics": {config.TOPIC_GAUGE: {"total": 100, "rate": 1.0, "last_ts": now - 2}},
         "groups": {config.GROUP_WINDOWING: {"state": "Stable", "members": 3, "lag": 0}}}
    r.update(kw)
    return r


def status(view, nid):
    return view["nodes"][nid]["status"]


def edge(view, eid):
    return next(e for e in view["edges"] if e["id"] == eid)["state"]


def test_healthy_flow_and_unbuilt_parts_are_absent_not_red():
    v = fm.build_view(raw())
    assert status(v, "t_gauge") == "ok" and status(v, "faust") == "ok"
    assert status(v, "forecaster") == "absent" and status(v, "p_mrms") == "absent"
    assert status(v, "t_rain") == "idle"            # exists, empty: waiting, not broken
    assert edge(v, "e_gauge_faust") == "flow" and edge(v, "e_rain_faust") == "planned"
    assert edge(v, "e_agg_fore") == "inactive"


def test_live_producer_silent_after_threshold_but_alive_on_heartbeat():
    now = time.time()
    quiet = {config.TOPIC_GAUGE: {"total": 100, "rate": 0.0, "last_ts": now - 400}}
    assert status(fm.build_view(raw(topics=quiet), live_stale_s=900), "p_usgs") == "ok"      # heartbeat window
    gone = {config.TOPIC_GAUGE: {"total": 100, "rate": 0.0, "last_ts": now - 2000}}
    assert status(fm.build_view(raw(topics=gone), live_stale_s=900), "p_usgs") == "down"


def test_replay_producer_idle_after_it_finishes():
    now = time.time()
    t = {config.TOPIC_REPLAY_GAUGE: {"total": 500, "rate": 40.0, "last_ts": now}}
    assert status(fm.build_view(raw(topics=t)), "p_replay") == "ok"
    t[config.TOPIC_REPLAY_GAUGE]["rate"] = 0.0
    assert status(fm.build_view(raw(topics=t)), "p_replay") == "idle"


def test_faust_instance_loss_warns_and_stopped_group_blocks_the_edge():
    g = {config.GROUP_WINDOWING: {"state": "Stable", "members": 2, "lag": 10}}
    assert status(fm.build_view(raw(groups=g)), "faust") == "warn"
    g = {config.GROUP_WINDOWING: {"state": "Empty", "members": 0, "lag": 900}}
    v = fm.build_view(raw(groups=g))
    assert status(v, "faust") == "down" and edge(v, "e_gauge_faust") == "blocked"
    g = {config.GROUP_WINDOWING: {"state": "Stable", "members": 3, "lag": 5000}}
    assert status(fm.build_view(raw(groups=g), lag_warn=500), "faust") == "warn"


def test_event_log_records_status_changes_and_broker_loss():
    log = fm.EventLog()
    log.update(fm.build_view(raw()))
    g = {config.GROUP_WINDOWING: {"state": "Empty", "members": 0, "lag": 0}}
    ev = log.update(fm.build_view(raw(groups=g, brokers_up=2)))
    assert any(e["node"] == "Faust windowing" and e["to"] == "down" for e in ev)
    assert any(e["node"] == "Kafka brokers" and e["to"] == "2 up" for e in ev)


def test_demo_data_builds():
    v = fm.build_view(fm.demo_raw(time.time(), time.time() - 20))
    assert status(v, "faust") == "warn" and set(v["nodes"]) == set(fm.NODE_SPECS)


def test_gauge_status_uses_official_levels_and_marks_stale_live_data():
    cats = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}
    assert fm.gauge_status(2.0, 60, cats, True) == "normal"
    assert fm.gauge_status(6.0, 60, cats, True) == "minor"
    assert fm.gauge_status(14.0, 60, cats, True) == "major"
    assert fm.gauge_status(6.0, 99999, cats, True) == "stale"       # old live reading: do not show a level
    assert fm.gauge_status(6.0, None, cats, False) == "minor"        # replay has no staleness
    assert fm.gauge_status(None, None, cats, True) == "nodata"


def test_latest_readings_live_newest_wins_replay_last_played_wins():
    lr = fm.LatestReadings()
    mk = lambda ts, v, f=0: {"type": "reading", "parameter": "00065", "site_no": "1", "value": v,
                             "reading_ts": ts, "fetched_ts": f, "qualifiers": []}
    lr.ingest(config.TOPIC_GAUGE, mk(200, 2.0))
    lr.ingest(config.TOPIC_GAUGE, mk(100, 9.0))               # older reading arriving late is ignored
    lr.ingest(config.TOPIC_GAUGE, {"type": "heartbeat", "site_no": "1"})
    assert lr.live["1"]["value"] == 2.0
    lr.ingest(config.TOPIC_REPLAY_GAUGE, mk(500, 3.0, f=1))
    lr.ingest(config.TOPIC_REPLAY_GAUGE, mk(100, 4.0, f=2))   # replay of an older flood plays on
    assert lr.replay["1"]["value"] == 4.0 and "1" not in {k for k in lr.live if lr.live[k]["value"] == 4.0}


def test_build_gauges_flags_provisional_and_age():
    locs = [{"site_no": "1", "lid": "A", "name": "a", "lat": 30, "lon": -98, "county": "",
             "cats": {"action": 5, "minor": 5, "moderate": 8, "major": 12}}]
    latest = {"1": {"value": 6.0, "reading_ts": 1000.0, "qualifiers": ["P"]}}
    g = fm.build_gauges(locs, latest, True, now=1100.0)[0]
    assert g["status"] == "minor" and g["provisional"] and g["age_s"] == 100.0
    assert fm.build_gauges(locs, {}, True, now=1100.0)[0]["status"] == "nodata"


def test_regions_load_and_summarise(tmp_path):
    import json

    from dashboard import flow_monitor as fm
    p = tmp_path / "regions.json"
    p.write_text(json.dumps([{"id": "a", "name": "A", "lat": 1, "lon": 2, "gauges_csv": "config/gauge_locations.csv"},
                             {"id": "off", "name": "Off", "lat": 1, "lon": 2, "gauges_csv": "x.csv", "enabled": False},
                             {"name": "no id", "lat": 1, "lon": 2}]))
    regs = fm.load_regions(str(p))
    assert [r["id"] for r in regs] == ["a"]
    latest = {"08158700": {"value": 6.0, "reading_ts": 1.0, "qualifiers": []}}
    (s,) = fm.regions_summary(regs, latest, False, 2.0)
    assert s["n_gauges"] >= 30 and s["n_reporting"] == 1 and s["worst"] in ("minor", "action", "moderate", "major", "normal")
    assert s["n_flooding"] == (1 if s["worst"] in ("minor", "moderate", "major") else 0)


def test_missing_regions_file_falls_back_to_the_default_network():
    from dashboard import flow_monitor as fm
    assert fm.load_regions("no/such/file.json")[0]["id"] == "default"


def test_build_view_carries_throughput_and_lag_for_the_metrics_tab():
    raw = {"ts": 100.0, "topics": {"gauge.usgs": {"total": 5, "rate": 1.5, "last_ts": 90.0}},
           "groups": {"postgres-sink": {"members": 1, "state": "Stable", "lag": 7}}}
    m = fm.build_view(raw)["metrics"]
    assert m["topics"] == [{"name": "gauge.usgs", "rate": 1.5, "total": 5, "age_s": 10.0}]
    assert m["groups"] == [{"id": "postgres-sink", "members": 1, "state": "Stable", "lag": 7}]


def test_agent_run_view_sums_time_tools_tokens_and_keeps_the_last_verdict():
    ev = [{"agent": "writer", "kind": "start", "detail": {}}, {"agent": "writer", "kind": "end", "detail": {"ms": 100, "tokens": 50}},
          {"agent": "reviewer", "kind": "verdict", "detail": {"verdict": "fail"}}, {"agent": "writer", "kind": "start", "detail": None},
          {"agent": "writer", "kind": "end", "detail": {"ms": 200, "tokens": 25}}, {"agent": "writer", "kind": "revision", "detail": {}},
          {"agent": "reviewer", "kind": "verdict", "detail": {"verdict": "pass"}}, {"agent": "context", "kind": "tool_call", "detail": {}}]
    v = fm.agent_run_view(ev)
    a = {x["agent"]: x for x in v["agents"]}
    assert a["writer"]["starts"] == 2 and a["writer"]["ms"] == 300 and a["writer"]["tokens"] == 75
    assert a["reviewer"]["verdict"] == "pass" and a["context"]["tool_calls"] == 1 and v["revisions"] == 1 and v["disagreements"] == 0


def test_planned_region_has_no_gauges_and_is_marked(tmp_path):
    regs = [{"id": "x", "name": "X", "lat": 1, "lon": 2, "gauges_csv": str(tmp_path / "missing.csv"), "planned": True}]
    out = fm.regions_summary(regs, {}, True, 0.0)
    assert out[0]["planned"] is True and out[0]["n_gauges"] == 0 and out[0]["worst"] == "nodata"
