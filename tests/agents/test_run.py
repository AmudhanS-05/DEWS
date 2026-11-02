import json

from agentic_ai import run
from tests.agents.fake_tools import FakeTools

ALERT = "08158700|minor|2019-05-04T03:00:00Z"


def test_process_record_runs_graph_on_alert_record():
    t = FakeTools()
    res = run.process_record(t, json.dumps({"alert_id": ALERT, "kind": "new"}).encode())
    assert res["outcome"] == "saved" and "saved" in run.summarize(res)


def test_retraction_and_garbage_are_skipped():
    t = FakeTools()
    assert run.process_record(t, json.dumps({"alert_id": ALERT, "kind": "retract"})) is None
    assert run.process_record(t, b"not json") is None and t.advisories == []


def test_waits_for_the_sink_then_gives_up_to_drop():
    sleeps = []
    res = run.process_record(FakeTools(), json.dumps({"alert_id": "x|minor|y"}), sleep=sleeps.append)
    assert len(sleeps) == run.SINK_WAIT_TRIES and res["outcome"] == "dropped"


def test_consume_reads_group_agent_commits_after_processing_and_never_produces(monkeypatch):
    import sys
    import types

    log = []

    class Msg:
        def error(self): return None
        def value(self): return json.dumps({"alert_id": ALERT}).encode()

    class FakeConsumer:
        def __init__(self, conf): log.append(("conf", conf["group.id"], conf["enable.auto.commit"]))
        def subscribe(self, topics): log.append(("sub", topics))
        def poll(self, t):
            if any(x[0] == "commit" for x in log):
                raise KeyboardInterrupt
            return Msg()
        def commit(self, message, asynchronous): log.append(("commit", len(t.advisories)))
        def close(self): log.append(("close",))

    t = FakeTools()
    monkeypatch.setitem(sys.modules, "confluent_kafka", types.SimpleNamespace(Consumer=FakeConsumer))
    run.consume(t)
    assert log[0] == ("conf", "agent", False) and log[1] == ("sub", ["alerts.flood"])
    assert ("commit", 1) in log and log[-1] == ("close",)
