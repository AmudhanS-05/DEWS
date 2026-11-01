"""The graph on the seeded alert, LLM_PROVIDER=none, no network and no database."""
import pytest

from agentic_ai import graph, llm
from tests.agents.fake_tools import FakeTools

ALERT = "08158700|minor|2019-05-04T03:00:00Z"
UPGRADE = "08158700|moderate|2019-05-04T03:00:00Z"


def kinds(res, agent=None, kind=None):
    return [e for e in res["events"] if (agent is None or e["agent"] == agent) and (kind is None or e["kind"] == kind)]


def test_seeded_alert_saves_v2_draft_with_events():
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["verdict"] == "pass" and res["confidence"] == "normal"
    assert res["advisory_id"] == ALERT + "|2" and t.advisories[0]["status"] == "draft"
    assert "SIMULATION" in t.advisories[0]["text"] and "MINOR" in t.advisories[0]["text"]
    agents = {e["agent"] for e in res["events"]}
    assert agents == {"coordinator", "evidence", "context", "writer", "reviewer"}
    assert len(t.events) == len(res["events"]) and {r["agent"] for r in t.runs} >= {"coordinator", "evidence", "context", "writer", "reviewer"}
    assert all(e["kind"] in {"start", "end", "tool_call", "token_chunk", "verdict", "disagreement", "revision"} for e in res["events"])


def test_workers_use_only_their_own_tools():
    res = graph.run_alert(FakeTools(), ALERT)
    names = {a: {e["detail"]["tool"] for e in kinds(res, a, "tool_call")} for a in ("evidence", "context")}
    assert names["evidence"] == {"get_recent_windows", "get_forecast", "get_official_levels", "get_past_floods",
                                 "get_data_quality"}
    assert names["context"] == {"get_rain", "get_neighbours", "get_gauge_info", "get_impacts"}


def test_evidence_and_context_both_run_before_writer():
    res = graph.run_alert(FakeTools(), ALERT)
    order = [(e["agent"], e["kind"]) for e in res["events"]]
    w = order.index(("writer", "start"))
    assert order.index(("evidence", "end")) < w and order.index(("context", "end")) < w


def test_disagreement_marks_uncertain_and_states_both_signals():
    t = FakeTools(flat=True)   # heavy radar rain but a flat gauge
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["confidence"] == "uncertain"
    d = kinds(res, "coordinator", "disagreement")
    assert len(d) == 1 and d[0]["detail"]["evidence"].startswith("not rising") and d[0]["detail"]["context"].startswith("rising")
    assert "UNCERTAIN" in t.advisories[0]["text"] and t.advisories[0]["confidence"] == "uncertain"


def test_no_rain_data_is_unknown_not_disagreement():
    res = graph.run_alert(FakeTools(rain=False), ALERT)
    assert res["confidence"] == "normal" and not kinds(res, "coordinator", "disagreement")


def test_duplicate_alert_is_dropped_and_force_overrides():
    t = FakeTools()
    graph.run_alert(t, ALERT)
    again = graph.run_alert(t, ALERT)
    assert again["outcome"] == "dropped" and "duplicate" in again["drop_reason"] and len(t.advisories) == 1
    assert graph.run_alert(t, ALERT, force=True)["outcome"] == "saved"


def test_unknown_alert_is_dropped():
    assert graph.run_alert(FakeTools(), "nope")["outcome"] == "dropped"


def test_upgrade_in_same_episode_is_written_as_update():
    t = FakeTools()
    t.alerts[UPGRADE]["probability"] = 0.99
    res = graph.run_alert(t, UPGRADE)
    assert res["outcome"] == "saved" and t.advisories[0]["text"].count("Update 2:") == 1


def _scripted(monkeypatch, outputs):
    calls = iter(outputs)
    monkeypatch.setattr(llm, "provider", lambda: "groq")
    monkeypatch.setattr(llm, "complete", lambda s, u, timeout_s=20, **k: llm.LlmReply(text=next(calls), tokens=10, provider="groq"))


def test_reviewer_sends_bad_draft_back_then_passes(monkeypatch):
    good = graph.advisory.template_text(FakeTools().get_alert(ALERT), {"rise_rate_ft_per_h": 0.7, "stage_last": 2.6},
                                        {"rain": {"available": True, "rain_3h": 27.0, "rain_6h": 71.0}, "impacts": []},
                                        "normal", 1, None)
    _scripted(monkeypatch, ["The creek will flood at 9.9 ft.", good, "PASS"])
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["attempts"] == 2
    v = kinds(res, "reviewer", "verdict")
    assert [x["detail"]["verdict"] for x in v] == ["fail", "pass"]
    assert any("will flood" in r for r in v[0]["detail"]["reasons"]) and kinds(res, "writer", "revision")
    assert res["tokens"] > 0


def test_still_failing_after_two_revisions_keeps_v1(monkeypatch):
    _scripted(monkeypatch, ["It will flood."] * 3)
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "v1_kept" and res["attempts"] == 3 and t.advisories == []


def test_llm_failure_keeps_template_v1_and_blocks_nothing(monkeypatch):
    """C4.3 (1 Nov): an LLM failure keeps template v1 (no duplicate v2 draft) and stops at once (no judge call)."""
    calls = []
    monkeypatch.setattr(llm, "provider", lambda: "groq")

    def fail(s, u, timeout_s=20, **k):
        calls.append(s)
        return llm.LlmReply(text=None, provider="groq", error="Timeout")
    monkeypatch.setattr(llm, "complete", fail)
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "v1_kept" and res["attempts"] == 1 and t.advisories == [] and len(calls) == 1
    assert res["llm_down"] == "Timeout" and res["reasons"] == ["LLM unavailable: Timeout"]


def test_tool_crash_leaves_v1(monkeypatch):
    t = FakeTools()
    monkeypatch.setattr(t, "get_rain", lambda *a, **k: 1 / 0)
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "v1_kept" and "division" in res["error"] and t.advisories == []


def test_unicode_draft_is_saved_on_first_attempt_as_ascii(monkeypatch):
    """Regression for the live v1_kept run: a correct draft with U+2013/U+2011/U+202F (probability as 79 %)."""
    a = FakeTools().get_alert(ALERT)
    draft = (f"SIMULATION – replayed historical data. Gauge now {a['current_stage']} ft. Forecast "
             f"{a['forecast_stage']} ft within {a['horizon_h']:g} h; {round(a['probability'] * 100)}\u202f% probability of "
             f"reaching MINOR flood stage ({a['official_levels']['minor']} ft). Lead‑time to MINOR about 4.1 h (an estimate). "
             "Data are provisional.")
    _scripted(monkeypatch, [draft, "PASS"])
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["attempts"] == 1, res.get("reasons")
    assert t.advisories[-1]["text"].isascii() and " " not in res["draft"]
