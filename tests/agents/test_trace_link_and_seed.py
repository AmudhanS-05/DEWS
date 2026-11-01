"""1 Nov coordinator items: (1) agent_runs carries the LangSmith trace run id and URL (tracing mocked, no network);
(2) the seed writes no alerts for the neighbour gauges 08159000 / 08158840 and can delete the ones it wrote before."""
from agentic_ai import graph, seed, tracing
from tests.agents.fake_tools import FakeTools

MINOR = "08158700|minor|2019-05-04T03:00:00Z"


def _tracing_on(monkeypatch, url="https://smith.langchain.com/o/x/projects/p/y/r/RUN"):
    monkeypatch.setattr(tracing, "configure", lambda: {"enabled": True, "project": "dews-agents", "reason": "on"})
    monkeypatch.setattr(tracing, "trace_url", lambda rid, project: url.replace("RUN", rid) if rid else None)


def test_trace_run_id_and_url_on_every_run_row(monkeypatch):
    _tracing_on(monkeypatch)
    t = FakeTools()
    res = graph.run_alert(t, MINOR)
    rid = res["trace_run_id"]
    assert rid and res["trace_url"].endswith(rid)
    assert t.runs and all(r["trace_run_id"] == rid and r["trace_url"] == res["trace_url"] for r in t.runs)


def test_tracing_off_leaves_both_empty():
    t = FakeTools()
    res = graph.run_alert(t, MINOR)
    assert res["trace_run_id"] is None and "trace_url" not in res
    assert all(r["trace_run_id"] is None and "trace_url" not in r for r in t.runs)


def test_unreachable_langsmith_keeps_the_id_and_the_outcome(monkeypatch):
    _tracing_on(monkeypatch)
    monkeypatch.setattr(tracing, "trace_url", lambda rid, project: None)
    t = FakeTools()
    res = graph.run_alert(t, MINOR)
    assert res["outcome"] == "saved" and all(r["trace_run_id"] == res["trace_run_id"] for r in t.runs)


def test_pg_agent_runs_columns(pg_empty, monkeypatch):
    from agentic_ai.tools import Tools
    _tracing_on(monkeypatch)
    alert = seed.load(pg_empty)
    res = graph.run_alert(Tools(pg_empty), alert)
    with pg_empty.cursor() as cur:
        cur.execute("SELECT DISTINCT trace_run_id, trace_url FROM agent_runs WHERE run_id = %s", (res["run_id"],))
        assert cur.fetchall() == [(res["trace_run_id"], res["trace_url"])]


def test_seed_writes_no_neighbour_alerts_but_keeps_their_windows(pg_empty):
    seed.load(pg_empty)
    with pg_empty.cursor() as cur:
        cur.execute("SELECT DISTINCT site_no FROM alerts")
        assert [r[0] for r in cur.fetchall()] == [seed.SITE]
        cur.execute("SELECT site_no, count(*) FROM windowed_aggregates GROUP BY site_no ORDER BY site_no")
        assert dict(cur.fetchall()) == {"08158700": 96, "08158840": 96, "08159000": 96}


def test_drop_neighbour_alerts_removes_exactly_the_old_rows(pg_empty):
    from agentic_ai.seed import _A_SQL, build_replay
    seed.load(pg_empty)
    with pg_empty.cursor() as cur:                      # what the seed wrote before 1 Nov
        for a in build_replay()["alerts"]:
            cur.execute(_A_SQL, a)
        cur.execute("SELECT count(*) FROM alerts")
        assert cur.fetchone()[0] == 6
    assert seed.drop_neighbour_alerts(pg_empty) == 5
    with pg_empty.cursor() as cur:
        cur.execute("SELECT alert_id FROM alerts")
        assert [r[0] for r in cur.fetchall()] == [seed.REPLAY_ALERT]
        cur.execute("SELECT count(*) FROM windowed_aggregates WHERE site_no <> %s", (seed.SITE,))
        assert cur.fetchone()[0] == 192
