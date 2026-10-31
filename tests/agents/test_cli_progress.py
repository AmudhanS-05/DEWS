"""CLI output: UTF-8 console, one progress line per agent step, final line, duplicate message."""
import io
import sys

from agentic_ai import graph, llm, run
from tests.agents.fake_tools import FakeTools

ALERT = "08158700|minor|2019-05-04T03:00:00Z"


def test_utf8_stdio_survives_a_cp1252_console(monkeypatch):
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    run.utf8_stdio()
    print("6.8\u202fft \u2011 79\xa0%")
    sys.stdout.flush()
    # TextIOWrapper writes os.linesep for "\n" (Windows: "\r\n"): compare the text, not the platform line ending
    assert raw.getvalue().decode("utf-8").replace("\r\n", "\n") == "6.8\u202fft \u2011 79\xa0%\n"


def _scripted(monkeypatch, outputs):
    calls = iter(outputs)
    monkeypatch.setattr(llm, "provider", lambda: "groq")
    monkeypatch.setattr(llm, "complete", lambda s, u, timeout_s=20: llm.LlmReply(
        text=next(calls), tokens=10, provider="groq", model="openai/gpt-oss-120b"))


def test_progress_lines_as_the_run_happens(monkeypatch):
    good = graph.advisory.template_text(FakeTools().get_alert(ALERT), {"rise_rate_ft_per_h": 0.7, "stage_last": 2.6},
                                        {"rain": {"available": True, "rain_3h": 27.0, "rain_6h": 71.0}, "impacts": []},
                                        "normal", 1, None)
    _scripted(monkeypatch, ["The creek will flood at 9.9 ft.", good, "PASS"])
    lines = []
    res = graph.run_alert(FakeTools(), ALERT, on_event=lambda e: lines.append(run.progress_line(e)))
    lines = [x for x in lines if x]
    agents = [x.split("]")[1].split()[0] for x in lines]
    assert agents[0] == "coordinator" and {"evidence", "context"} <= set(agents[1:3])
    assert any("writer" in x and "attempt 1/3: groq openai/gpt-oss-120b" in x and " ms)" in x for x in lines)
    fail = next(x for x in lines if "reviewer" in x and "attempt 1/3" in x)
    assert "FAIL" in fail and "will flood" in fail and "9.9" in fail
    assert any("reviewer" in x and "attempt 2/3: PASS" in x for x in lines)
    assert lines[-1].endswith("(normal)") and "saved" in lines[-1]
    final = run.final_line(res)
    assert final.startswith(f"advisory {ALERT}|2  version 2  outcome saved") and "tokens 30" in final
    assert "LangSmith" not in final   # tracing off in tests


def test_final_line_has_trace_url_when_tracing_on():
    res = {"outcome": "saved", "advisory_id": "a|2", "version": 2, "confidence": "normal", "attempts": 1,
           "tokens": 5, "events": [], "elapsed_s": 1.5, "trace_run_id": "abc", "trace_project": "dews-agents",
           "alert_id": "a"}
    assert run.final_line(res, "https://smith.langchain.com/o/t/projects/p/x/r/abc?poll=true").endswith("r/abc?poll=true")
    assert "project dews-agents, run abc" in run.final_line(res, None)


def test_duplicate_message_names_the_version_and_force():
    t = FakeTools()
    graph.run_alert(t, ALERT)
    res = graph.run_alert(t, ALERT)
    assert "v2 exists" in res["drop_reason"] and "use --force to re-run" in res["drop_reason"]
    assert "v2 exists" in run.final_line(res)


def test_fallback_and_llm_failure_get_a_line():
    e = {"agent": "writer", "kind": "revision", "t_s": 1.0,
         "detail": {"llm_fallback": "gemini", "primary": "groq", "primary_error": "RateLimitError: 429", "ok": True}}
    assert "groq failed (RateLimitError: 429); retried on gemini: ok" in run.progress_line(e)
    assert run.progress_line({"agent": "writer", "kind": "token_chunk", "detail": {"text": "x"}}) is None


def test_progress_callback_errors_do_not_change_the_outcome():
    res = graph.run_alert(FakeTools(), ALERT, on_event=lambda e: 1 / 0)
    assert res["outcome"] == "saved"


def test_reviewer_line_says_when_the_llm_judge_gave_no_reply():
    """Laptop/cloud run 4 (31 Oct): Groq rate-limited the judge, the deterministic checks passed and PASS hid that."""
    e = {"agent": "reviewer", "kind": "verdict", "t_s": 4.3,
         "detail": {"verdict": "pass", "reasons": [], "attempt": 1, "max_attempts": 3,
                    "llm_judgement": "no reply (groq: RateLimitError: 429)"}}
    line = run.progress_line(e)
    assert "PASS (deterministic checks only; LLM judgement no reply (groq: RateLimitError: 429))" in line
    ok = dict(e, detail=dict(e["detail"], llm_judgement="PASS"))
    assert run.progress_line(ok).endswith("attempt 1/3: PASS")
