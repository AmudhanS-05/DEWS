"""A4.1: the stdio MCP server wraps the tools; the graph calls them through the MCP client by default and falls back
to a direct call when MCP fails. PostgreSQL tests need AGENTS_TEST_DSN (throwaway schema)."""
import pytest

from agentic_ai import graph, mcp_client, mcp_server
from agentic_ai.tools import Tools
from tests.agents.fake_tools import FakeTools

MINOR = "08158700|minor|2019-05-04T03:00:00Z"


def test_server_offers_every_contract_read_tool_and_save_advisory():
    contract = {"get_alert", "get_recent_windows", "get_forecast", "get_official_levels", "get_past_floods", "get_rain",
                "get_neighbours", "get_gauge_info", "get_impacts", "get_episode_history"}
    assert contract <= set(mcp_server.TOOL_NAMES) and "save_advisory" in mcp_server.TOOL_NAMES
    assert not {"log_agent_event", "log_agent_run"} & set(mcp_server.TOOL_NAMES)
    for n in mcp_server.TOOL_NAMES:
        assert hasattr(Tools, n)


def test_fakes_and_direct_mode_are_not_wrapped(monkeypatch):
    f = FakeTools()
    assert mcp_client.wrap(f) is f
    monkeypatch.setenv("AGENT_TOOLS", "direct")
    t = Tools.__new__(Tools)
    assert mcp_client.wrap(t) is t


class _Broken:
    ok, error, tool_names = True, None, set(mcp_server.TOOL_NAMES)

    def call(self, name, arguments, timeout=None):
        raise TimeoutError("server hung")


class _Down:
    ok, error, tool_names = False, "server did not start within 20 s", set()


@pytest.mark.parametrize("session", [_Broken(), _Down()], ids=["call_fails", "server_down"])
def test_mcp_failure_falls_back_to_direct_and_still_saves(session):
    t = FakeTools()
    proxy = mcp_client.McpTools(t, session=session)
    res = graph.run_alert(proxy, MINOR)
    assert res["outcome"] == "saved" and t.advisories
    vias = {e["detail"]["via"] for e in res["events"] if e["kind"] == "tool_call"}
    assert vias and all(v.startswith("direct (mcp") for v in vias)
    assert proxy.calls["mcp"] == 0 and proxy.calls["fallback"] > 0


def test_run_log_never_goes_over_mcp():
    t = FakeTools()
    proxy = mcp_client.McpTools(t, session=_Broken())
    proxy.log_agent_event("r", MINOR, "coordinator", "start", {})
    assert t.events and proxy.calls == {"mcp": 0, "direct": 0, "fallback": 0}


# ------------------------------------------------------------------ real stdio server on PostgreSQL

@pytest.fixture()
def mcp_session(pg_conn, seeded):
    s = mcp_client.McpSession(mcp_client._conn_env(pg_conn)).start()
    yield s
    s.close()


def test_stdio_server_answers_like_the_direct_tools(mcp_session, tools):
    assert mcp_session.ok, mcp_session.error
    assert mcp_session.tool_names == set(mcp_server.TOOL_NAMES)
    assert mcp_session.call("get_official_levels", {"site": "08158700"}) == tools.get_official_levels("08158700")
    a = mcp_session.call("get_alert", {"alert_id": MINOR})
    assert a == tools.get_alert(MINOR)
    w = mcp_session.call("get_recent_windows", {"site": "08158700", "hours": 3, "until": a["as_of"], "mode": "SIMULATION"})
    assert w == tools.get_recent_windows("08158700", 3, a["as_of"], "SIMULATION")
    assert mcp_session.call("get_impacts", {"site": "08158700", "level": "minor"}) == []


def test_graph_uses_mcp_by_default_on_postgres(tools, pg_conn, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLS", raising=False)
    res = graph.run_alert(tools, MINOR, force=True)
    assert res["outcome"] == "saved", res.get("error")
    vias = [e["detail"]["via"] for e in res["events"] if e["kind"] == "tool_call"]
    assert vias and set(vias) == {"mcp"} and res["tools_via"]["mcp"] == len(vias) + 1   # + get_alert in run_alert
    with pg_conn.cursor() as cur:
        cur.execute("SELECT text FROM advisories WHERE advisory_id = %s", (res["advisory_id"],))
        assert "SIMULATION" in cur.fetchone()[0]
        for t in ("advisories", "agent_events", "agent_runs"):     # leave the session schema as the other tests expect
            cur.execute(f"DELETE FROM {t} WHERE alert_id = %s", (MINOR,))
