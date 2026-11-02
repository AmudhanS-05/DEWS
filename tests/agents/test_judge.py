"""Independent judge (1 Nov, user decision "make Gemini the judge"): the reviewer's LLM judgement runs on
JUDGE_PROVIDER / JUDGE_MODEL, not on the writer's model. Mocked providers (llm._build), no network."""
from types import SimpleNamespace

from agentic_ai import graph, llm
from tests.agents.fake_tools import FakeTools

ALERT = "08158700|minor|2019-05-04T03:00:00Z"


def _good():
    return graph.advisory.template_text(FakeTools().get_alert(ALERT), {"rise_rate_ft_per_h": 0.7, "stage_last": 2.6},
                                        {"rain": {"available": True, "rain_3h": 27.0, "rain_6h": 71.0}, "impacts": []},
                                        "normal", 1, None)


class _Chat:
    def __init__(self, prov, model, behaviour, calls):
        self.prov, self.model, self.behaviour, self.calls = prov, model, behaviour, calls

    def invoke(self, messages):
        role = "judge" if messages[0][1].startswith("You review") else "writer"
        self.calls.append((role, self.prov, self.model))
        out = self.behaviour[(role, self.prov)]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(content=out, usage_metadata={"total_tokens": 7, "input_tokens": 5, "output_tokens": 2})


def _env(monkeypatch, behaviour, writer="groq", judge="gemini", **extra):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", writer)
    monkeypatch.setenv("LLM_FALLBACK", extra.pop("LLM_FALLBACK", "none"))
    monkeypatch.setenv("JUDGE_PROVIDER", judge)
    for k in ("JUDGE_MODEL", "JUDGE_FALLBACK", "GROQ_MODEL", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    calls = []
    monkeypatch.setattr(llm, "_build", lambda prov, timeout_s, model=None: _Chat(prov, model or llm.model_name(prov),
                                                                                 behaviour, calls))
    return calls


def _verdicts(res):
    return [e["detail"] for e in res["events"] if e["agent"] == "reviewer" and e["kind"] == "verdict"]


def _warnings(res):
    return [e for e in res["events"] if e["agent"] == "reviewer" and e["detail"].get("warning") == "same-model judge"]


# ------------------------------------------------------------------------------------------------ llm.py

def test_judge_defaults_to_gemini_and_follows_judge_model(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert llm.judge_provider() == "gemini" and llm.judge_model() == llm.GEMINI_MODEL
    monkeypatch.setenv("JUDGE_MODEL", "gemini-x")
    assert llm.judge_model() == "gemini-x"
    monkeypatch.setenv("JUDGE_PROVIDER", "nonsense")
    assert llm.judge_provider() == "none"


def test_judge_call_uses_judge_provider_not_the_writer(monkeypatch):
    calls = _env(monkeypatch, {("judge", "gemini"): "PASS"}, JUDGE_MODEL="gemini-judge")
    r = llm.judge("You review flood advisories.", "x")
    assert r.text == "PASS" and r.provider == "gemini" and r.model == "gemini-judge"
    assert calls == [("judge", "gemini", "gemini-judge")] and not llm.same_model_judge()


def test_judge_never_falls_back_on_llm_fallback_only_on_judge_fallback(monkeypatch):
    beh = {("judge", "gemini"): RuntimeError("503"), ("judge", "groq"): "PASS"}
    calls = _env(monkeypatch, beh, LLM_FALLBACK="groq")
    r = llm.judge("You review flood advisories.", "x")
    assert r.text is None and "503" in r.error and [c[1] for c in calls] == ["gemini"]
    calls.clear()
    monkeypatch.setenv("JUDGE_FALLBACK", "groq")
    r = llm.judge("You review flood advisories.", "x")
    assert r.text == "PASS" and r.provider == "groq" and r.fallback_from == "gemini"
    assert [c[1] for c in calls] == ["gemini", "groq"]


def test_same_model_judge_detection(monkeypatch):
    _env(monkeypatch, {}, judge="groq")
    assert llm.same_model_judge()
    monkeypatch.setenv("JUDGE_MODEL", "llama-3.3-70b-versatile")
    assert not llm.same_model_judge()        # same provider, other model: independent enough to not warn


# ------------------------------------------------------------------------------------------------ graph

def test_graph_judge_differs_from_writer_and_is_recorded(monkeypatch):
    calls = _env(monkeypatch, {("writer", "groq"): _good(), ("judge", "gemini"): "PASS"})
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["attempts"] == 1
    assert [c[:2] for c in calls] == [("writer", "groq"), ("judge", "gemini")]
    v = _verdicts(res)[-1]
    assert v["judge_provider"] == "gemini" and v["judge_model"] == llm.GEMINI_MODEL and v["judge_status"] == "pass"
    assert v["llm_judgement"] == "PASS" and not _warnings(res)
    row = next(r for r in t.runs if r["agent"] == "reviewer")
    assert row["output"]["judge"]["provider"] == "gemini" and row["output"]["judge"]["model"] == llm.GEMINI_MODEL
    assert res["judges"] == [{"attempt": 1, **row["output"]["judge"]}] and not res["judge_unavailable"]


def test_graph_judge_fail_sends_the_draft_back_with_its_reason(monkeypatch):
    seq = iter(["FAIL: sounds certain", "PASS"])
    beh = {("writer", "groq"): _good()}
    calls = _env(monkeypatch, beh)

    class _J(dict):
        def __getitem__(self, k):
            return next(seq) if k == ("judge", "gemini") else dict.__getitem__(self, k)
    monkeypatch.setattr(llm, "_build", lambda prov, timeout_s, model=None: _Chat(prov, model or llm.model_name(prov),
                                                                                 _J(beh), calls))
    res = graph.run_alert(FakeTools(), ALERT)
    assert res["outcome"] == "saved" and res["attempts"] == 2
    v = _verdicts(res)
    assert v[0]["verdict"] == "fail" and v[0]["judge_status"] == "fail" and "LLM review: sounds certain" in v[0]["reasons"]
    assert [j["status"] for j in res["judges"]] == ["fail", "pass"]
    assert all(j["provider"] == "gemini" for j in res["judges"])


def test_graph_judge_down_deterministic_checks_decide_and_run_says_judge_unavailable(monkeypatch):
    calls = _env(monkeypatch, {("writer", "groq"): _good(), ("judge", "gemini"): RuntimeError("GEMINI_API_KEY not set")})
    t = FakeTools()
    res = graph.run_alert(t, ALERT)
    assert res["outcome"] == "saved" and res["attempts"] == 1      # never blocks
    v = _verdicts(res)[-1]
    assert v["verdict"] == "pass" and v["judge_status"] == "unavailable" and v["judge_provider"] == "gemini"
    assert v["llm_judgement"].startswith("judge unavailable") and not v["llm_judgement"].upper().startswith("PASS")
    assert res["judge_unavailable"] and "GEMINI_API_KEY" in res["judges"][0]["error"]
    assert [c[:2] for c in calls] == [("writer", "groq"), ("judge", "gemini")]   # no fallback to the writer's model
    coord = [r for r in t.runs if r["agent"] == "coordinator"][-1]["output"]
    assert coord["judge_status"] == "unavailable" and "judge unavailable" in coord["note"]


def test_graph_judge_down_with_rate_limit_still_saves(monkeypatch):
    class RateLimitError(Exception):
        pass
    _env(monkeypatch, {("writer", "groq"): _good(), ("judge", "gemini"): RateLimitError("429 quota")})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    res = graph.run_alert(FakeTools(), ALERT)
    assert res["outcome"] == "saved" and res["judge_unavailable"]


def test_graph_same_model_judge_logs_a_warning(monkeypatch):
    calls = _env(monkeypatch, {("writer", "groq"): _good(), ("judge", "groq"): "PASS"}, judge="groq")
    res = graph.run_alert(FakeTools(), ALERT)
    w = _warnings(res)
    assert res["outcome"] == "saved" and len(w) == 1 and w[0]["kind"] == "revision"
    assert w[0]["detail"]["provider"] == "groq" and w[0]["detail"]["model"] == llm.GROQ_MODEL
    assert _verdicts(res)[-1]["judge"]["same_model"] is True
    assert [c[:2] for c in calls] == [("writer", "groq"), ("judge", "groq")]


def test_judge_provider_none_is_recorded_as_off(monkeypatch):
    calls = _env(monkeypatch, {("writer", "groq"): _good()}, judge="none")
    res = graph.run_alert(FakeTools(), ALERT)
    assert res["outcome"] == "saved" and [c[0] for c in calls] == ["writer"]
    assert _verdicts(res)[-1]["judge_status"] == "off"


# ------------------------------------------------------------------------------------------------ PostgreSQL

def test_pg_agent_runs_and_events_record_the_judge_per_attempt(monkeypatch, pg_conn, seeded):
    """The writer's text is the template text (recorded as the graph builds it); the judge is a mocked Gemini."""
    from agentic_ai.tools import Tools
    last = {}
    real = graph.advisory.template_text

    def tmpl(*a, **k):
        last["text"] = real(*a, **k)
        return last["text"]
    monkeypatch.setattr(graph.advisory, "template_text", tmpl)
    monkeypatch.setattr(llm, "provider", lambda: "groq")
    monkeypatch.setattr(llm, "complete", lambda s, u, timeout_s=20, **k: llm.LlmReply(
        text=last["text"], tokens=9, provider="groq", model=llm.GROQ_MODEL))
    _env(monkeypatch, {("judge", "gemini"): RuntimeError("ResourceExhausted: 429")}, AGENT_TOOLS="direct")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    res = graph.run_alert(Tools(pg_conn), seeded, force=True)
    assert res["outcome"] == "saved" and res["judge_unavailable"]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT output->'judge'->>'provider', output->'judge'->>'model', output->'judge'->>'status' "
                    "FROM agent_runs WHERE run_id = %s AND agent = 'reviewer'", (res["run_id"],))
        assert cur.fetchall() == [("gemini", llm.GEMINI_MODEL, "unavailable")]
        cur.execute("SELECT detail->>'judge_provider', detail->>'judge_status' FROM agent_events WHERE run_id = %s "
                    "AND agent = 'reviewer' AND kind = 'verdict'", (res["run_id"],))
        assert cur.fetchall() == [("gemini", "unavailable")]
