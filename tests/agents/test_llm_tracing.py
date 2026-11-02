import os

from agentic_ai import llm, tracing


def test_provider_none_returns_no_text_and_no_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    r = llm.complete("s", "u")
    assert r.text is None and r.error is None and r.provider == "none"


def test_unknown_provider_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm.provider() == "none"


def test_groq_without_key_fails_soft(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    r = llm.complete("s", "u")
    assert r.text is None and "GROQ_API_KEY" in r.error


def test_ollama_down_fails_soft_within_timeout(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")   # nothing listens
    r = llm.complete("s", "u", timeout_s=2)
    assert r.text is None and r.error and r.latency_s < 10


def test_tracing_off_without_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    s = tracing.configure()
    assert s["enabled"] is False and os.environ["LANGSMITH_TRACING"] == "false" and "KEY" in s["reason"]


def test_tracing_on_with_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    s = tracing.configure()
    assert s["enabled"] and s["project"] and "lsv2_fake" not in str(s)
    assert tracing.run_config("r", "a", "SIMULATION")["metadata"]["alert_id"] == "a"
