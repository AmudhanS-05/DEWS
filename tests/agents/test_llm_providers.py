"""Provider switch and LLM_FALLBACK, with mocked chat models (no network)."""
from agentic_ai import llm


class _Msg:
    def __init__(self, content, tokens=7):
        self.content, self.usage_metadata = content, {"total_tokens": tokens}


class _Chat:
    def __init__(self, result):
        self.result = result

    def invoke(self, _messages):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _providers(monkeypatch, primary, fb, behaviour):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", primary)
    monkeypatch.setenv("LLM_FALLBACK", fb)
    calls = []

    def build(prov, timeout_s):
        calls.append(prov)
        return _Chat(behaviour[prov])
    monkeypatch.setattr(llm, "_build", build)
    return calls


class RateLimitError(Exception):
    pass


def test_default_models(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    for v in ("GROQ_MODEL", "GEMINI_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(v, raising=False)
    assert llm.model_name("groq") == "openai/gpt-oss-120b"
    assert llm.model_name("gemini") == "gemini-3.5-flash-lite" and llm.model_name("ollama") == "qwen2.5:7b"
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    assert llm.model_name("groq") == "llama-3.3-70b-versatile"


def test_gemini_is_a_provider_and_fails_soft_without_key(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK", "none")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = llm.complete("s", "u")
    assert llm.provider() == "gemini" and r.text is None and "GEMINI_API_KEY" in r.error


def test_gemini_client_builds_with_a_key_without_network(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    chat = llm._build("gemini", 5)
    assert type(chat).__name__ == "ChatGoogleGenerativeAI" and chat.model.endswith("gemini-3.5-flash-lite")


def test_groq_rate_limit_retries_once_on_gemini(monkeypatch):
    calls = _providers(monkeypatch, "groq", "gemini",
                       {"groq": RateLimitError("429 rate limit"), "gemini": _Msg([{"type": "text", "text": "ok text"}])})
    r = llm.complete("s", "u")
    assert calls == ["groq", "gemini"]
    assert r.text == "ok text" and r.provider == "gemini" and r.fallback_from == "groq" and "429" in r.primary_error


def test_both_fail_returns_none_with_both_errors(monkeypatch):
    calls = _providers(monkeypatch, "groq", "gemini", {"groq": TimeoutError("t"), "gemini": RuntimeError("down")})
    r = llm.complete("s", "u")
    assert calls == ["groq", "gemini"] and r.text is None and "groq" in r.error and "gemini" in r.error


def test_no_fallback_call_when_primary_works_or_fallback_none(monkeypatch):
    calls = _providers(monkeypatch, "groq", "gemini", {"groq": _Msg("fine"), "gemini": _Msg("unused")})
    assert llm.complete("s", "u").text == "fine" and calls == ["groq"]
    calls = _providers(monkeypatch, "groq", "none", {"groq": RuntimeError("x")})
    assert llm.complete("s", "u").text is None and calls == ["groq"]


def test_empty_reply_counts_as_failure(monkeypatch):
    calls = _providers(monkeypatch, "groq", "gemini", {"groq": _Msg("  "), "gemini": _Msg("from gemini")})
    assert llm.complete("s", "u").text == "from gemini" and calls == ["groq", "gemini"]


def test_fallback_equal_to_primary_is_ignored(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK", "gemini")
    assert llm.fallback() == "none"
