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
