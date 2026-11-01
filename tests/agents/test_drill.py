"""C4.3 "LLM down" fault drill: v1 stays, no agents draft, nothing blocked (PostgreSQL, throwaway schema)."""
import pytest

from agentic_ai import drill, llm

MINOR = "08158700|minor|2019-05-04T03:00:00Z"


def test_forced_down_fails_every_call_at_once(monkeypatch):
    monkeypatch.setenv("LLM_FORCE_DOWN", "1")
    r = llm.complete("s", "u")
    assert r.text is None and "LLM_FORCE_DOWN" in r.error and r.provider != "none"
    monkeypatch.delenv("LLM_FORCE_DOWN")
    with llm.forced_down_ctx():
        assert llm.forced_down()
    assert not llm.forced_down()


@pytest.mark.parametrize("no_key", [False, True], ids=["forced_down", "no_key"])
def test_drill_passes_and_keeps_v1(tools, pg_conn, no_key):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO advisories (advisory_id, alert_id, version, source, text, status) "
                    "VALUES (%s,%s,1,'template','v1 text','draft') ON CONFLICT DO NOTHING", (MINOR + "|1", MINOR))
    try:
        res = drill.llm_down(tools, MINOR, no_key=no_key)
        assert res["ok"], res["notes"]
        assert res["run"]["outcome"] == "v1_kept" and res["recovery_time_s"] < drill.LIMIT_S
        assert {c["check"].split(" (")[0] for c in res["checks"]} >= {"no agents draft written",
                                                                     "template v1 unchanged and approvable"}
        assert [a["version"] for a in drill._adv(tools, MINOR)] == [1]
    finally:
        with pg_conn.cursor() as cur:
            for t in ("advisories", "agent_events", "agent_runs"):
                cur.execute(f"DELETE FROM {t} WHERE alert_id = %s", (MINOR,))


def test_drill_never_raises_without_alerts():
    class NoAlerts:
        def _q(self, *a):
            return []
    res = drill.llm_down(NoAlerts())
    assert res["ok"] is False and "no alert" in res["notes"]
