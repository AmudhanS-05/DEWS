"""Contract tools on a real PostgreSQL (seeded May 2019 DRWT2 alert, SIMULATION)."""
from agentic_ai import seed

ALERT = "08158700|minor|2019-05-04T03:00:00Z"
UPGRADE = "08158700|moderate|2019-05-04T03:00:00Z"


def test_seed_is_idempotent(pg_conn, seeded):
    assert seed.load(pg_conn) == ALERT
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM alerts")
        assert cur.fetchone()[0] == 2


def test_get_alert_rebuilds_contract_fields(tools):
    a = tools.get_alert(ALERT)
    assert a["mode"] == "SIMULATION" and a["category"] == "minor" and a["kind"] == "new"
    assert a["official_levels"]["minor"] == 5.5 and a["official_levels"]["moderate"] == 7.0
    assert a["current_stage"] == 2.6 and a["forecast_stage"] == 6.8 and a["horizon_h"] == 6
    assert tools.get_alert(UPGRADE)["kind"] == "upgrade"
    assert tools.get_alert("nope") is None


def test_windows_end_at_alert_time_and_stay_simulation(tools):
    a = tools.get_alert(ALERT)
    w = tools.get_recent_windows(a["site_no"], 6, a["raised_at"], a["mode"])
    assert len(w) == 6 and w[-1]["window_end"] == a["raised_at"] and w[-1]["stage_last"] == 2.6
    assert all(x["mode"] == "SIMULATION" for x in w)
    assert [x["window_start"] for x in w] == sorted(x["window_start"] for x in w)
    assert tools.get_recent_windows(a["site_no"], 6, a["raised_at"], "LIVE") == []


def test_forecast_and_levels_and_info(tools):
    a = tools.get_alert(ALERT)
    f = tools.get_forecast(a["site_no"], a["raised_at"], "SIMULATION")
    assert f["model"] == "rise_rate" and f["forecast_stage"] == 6.8 and 0.7 < f["p_minor"] < 0.9
    assert tools.get_official_levels("08158700")["major"] == 13.0
    assert tools.get_gauge_info("08158700")["county"] == "Hays"


def test_rain_shows_rain_before_the_rise(tools):
    a = tools.get_alert(ALERT)
    r = tools.get_rain(a["site_no"], 6, a["raised_at"], a["mode"])
    assert r["available"] and r["rain_3h"] == 27.0 and r["rain_6h"] == 71.0
    assert tools.get_rain("08158840", 6, a["raised_at"], a["mode"])["available"] is False


def test_neighbours_name_downstream_first(tools):
    a = tools.get_alert(ALERT)
    n = tools.get_neighbours(a["site_no"], a["raised_at"], a["mode"])
    assert n[0]["site_no"] == "08159000" and n[0]["relation"] == "downstream" and n[0]["rising"] is False


def test_impacts_and_past_floods_are_empty_not_invented(tools):
    assert tools.get_impacts("08158700", "minor") == []
    assert tools.get_past_floods("08158700")["n"] == 0


def test_episode_history_links_both_alerts(tools):
    h = tools.get_episode_history(UPGRADE)
    assert [x["category"] for x in h["alerts"]] == ["minor", "moderate"]


def test_writes_save_advisory_log_run_and_event(tools, pg_conn):
    r = tools.save_advisory(ALERT, "draft one", "normal", "pass")
    assert r["advisory_id"] == ALERT + "|2"
    tools.save_advisory(ALERT, "draft two", "uncertain", "pass")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version, status, confidence FROM advisories WHERE alert_id=%s ORDER BY version", (ALERT,))
        assert cur.fetchall() == [(2, "superseded", "normal"), (3, "draft", "uncertain")]
    tools.log_agent_event("r1", ALERT, "writer", "start", {"x": 1})
    tools.log_agent_run("r1", ALERT, "writer", "in", {"o": 1}, [{"tool": "t"}], tokens=5, verdict="pass")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agent, kind, detail FROM agent_events WHERE run_id='r1'")
        assert cur.fetchone() == ("writer", "start", {"x": 1})
        cur.execute("SELECT agent, tokens, verdict FROM agent_runs WHERE run_id='r1'")
        assert cur.fetchone() == ("writer", 5, "pass")


def test_graph_on_real_postgres_writes_v2_events_and_runs(tools, pg_conn):
    from agentic_ai import graph
    res = graph.run_alert(tools, UPGRADE, force=True)
    assert res["outcome"] == "saved" and res["verdict"] == "pass"
    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, source, reviewer_verdict FROM advisories WHERE advisory_id = %s", (res["advisory_id"],))
        assert cur.fetchone() == ("draft", "agents", "pass")
        cur.execute("SELECT count(*), count(DISTINCT agent) FROM agent_events WHERE run_id = %s", (res["run_id"],))
        assert cur.fetchone() == (len(res["events"]), 5)
        cur.execute("SELECT count(*) FROM agent_runs WHERE run_id = %s", (res["run_id"],))
        assert cur.fetchone()[0] >= 5


def test_agent_events_stream_in_order_for_the_live_agents_tab(tools, pg_conn):
    """A2.3: each event is in PostgreSQL before the next step runs (the console polls agent_events), in event_id
    order with non-decreasing ts, and only the agent/kind values of docs/CONTRACTS.md."""
    from agentic_ai import graph
    seen = []

    def check(e):
        with pg_conn.cursor() as cur:
            cur.execute("SELECT agent, kind FROM agent_events WHERE run_id = (SELECT run_id FROM agent_events "
                        "ORDER BY event_id DESC LIMIT 1) ORDER BY event_id")
            rows = cur.fetchall()
        seen.append(len(rows))
        assert (e["agent"], e["kind"]) in rows
    res = graph.run_alert(tools, ALERT, force=True, on_event=check)
    # evidence and context run in parallel threads, so a count can jump ahead, never lag behind
    assert all(n >= i + 1 for i, n in enumerate(seen)) and seen[-1] == len(res["events"])
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agent, kind, detail, ts FROM agent_events WHERE run_id = %s ORDER BY event_id", (res["run_id"],))
        rows = cur.fetchall()
    assert {r[0] for r in rows} <= {"coordinator", "evidence", "context", "writer", "reviewer", "human"}
    assert {r[1] for r in rows} <= {"start", "end", "tool_call", "token_chunk", "verdict", "disagreement", "revision"}
    assert all(isinstance(r[2], dict) for r in rows) and [r[3] for r in rows] == sorted(r[3] for r in rows)
    v = [r[2] for r in rows if r[1] == "verdict"]
    assert v and {"verdict", "reasons", "attempt", "max_attempts"} <= set(v[-1])
    assert all("ms" in r[2] for r in rows if r[1] == "end" and r[0] != "coordinator")
