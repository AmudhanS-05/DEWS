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


# ---------------------------------------------------------------- GROQ_REASONING_EFFORT and the 429 wait (round 4)

def test_reasoning_effort_default_low_and_only_for_gpt_oss(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    for v in ("GROQ_MODEL", "GROQ_REASONING_EFFORT"):
        monkeypatch.delenv(v, raising=False)
    assert llm.reasoning_effort("groq") == "low" and llm.reasoning_effort("gemini") is None
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "Medium")
    assert llm.reasoning_effort("groq") == "medium"
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "extreme")            # unknown value -> default
    assert llm.reasoning_effort("groq") == "low"
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")      # model without the parameter: not sent
    assert llm.reasoning_effort("groq") is None


def test_groq_client_gets_reasoning_effort_without_network(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "high")
    assert llm._build("groq", 5).reasoning_effort == "high"
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    assert llm._build("groq", 5).reasoning_effort is None


class _Resp:
    def __init__(self, headers):
        self.status_code, self.headers = 429, headers


class Groq429(Exception):
    """Shaped like groq.RateLimitError: status_code 429 and the HTTP response with Groq's headers."""
    def __init__(self, headers):
        super().__init__("Error code: 429 - rate limit reached on tokens per minute (TPM)")
        self.status_code, self.response = 429, _Resp(headers)


def test_rate_limit_hint_from_headers():
    assert llm.rate_limit_hint(Groq429({"retry-after": "4"})) == 4.0
    assert llm.rate_limit_hint(Groq429({"x-ratelimit-reset-tokens": "7.66s", "x-ratelimit-reset-requests": "2.1s"})) == 7.66
    assert llm.rate_limit_hint(Groq429({"x-ratelimit-reset-tokens": "1m2.5s"})) == 62.5
    assert llm.rate_limit_hint(Groq429({})) is None and llm.rate_limit_hint(RuntimeError("down")) is None


class _Seq:
    def __init__(self, results):
        self.results = list(results)

    def invoke(self, _m):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _seq_providers(monkeypatch, fb, groq_results, gemini_result=None):
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_FALLBACK", fb)
    calls, slept, waits = [], [], []
    seq = {"groq": _Seq(groq_results), "gemini": _Seq([gemini_result])}

    def build(prov, timeout_s):
        calls.append(prov)
        return seq[prov]
    monkeypatch.setattr(llm, "_build", build)
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    return calls, slept, waits


def test_groq_429_waits_once_for_the_hint_then_retries_groq(monkeypatch):
    calls, slept, waits = _seq_providers(monkeypatch, "gemini", [Groq429({"retry-after": "3"}), _Msg("after wait")])
    r = llm.complete("s", "u", on_wait=lambda p, w, h: waits.append((p, w, h)))
    assert r.text == "after wait" and r.provider == "groq" and r.waited_s == 3.0
    assert calls == ["groq", "groq"] and slept == [3.0] and waits == [("groq", 3.0, 3.0)]


def test_groq_429_twice_waits_once_then_falls_back(monkeypatch):
    calls, slept, _ = _seq_providers(monkeypatch, "gemini", [Groq429({"retry-after": "2"}), Groq429({"retry-after": "2"})],
                                     _Msg("from gemini"))
    r = llm.complete("s", "u")
    assert calls == ["groq", "groq", "gemini"] and slept == [2.0]
    assert r.text == "from gemini" and r.fallback_from == "groq" and "after waiting 2 s" in r.primary_error


def test_groq_429_hint_above_cap_goes_straight_to_fallback(monkeypatch):
    calls, slept, waits = _seq_providers(monkeypatch, "gemini", [Groq429({"x-ratelimit-reset-tokens": "45s"})],
                                         _Msg("from gemini"))
    r = llm.complete("s", "u", on_wait=lambda p, w, h: waits.append((p, w, h)))
    assert calls == ["groq", "gemini"] and slept == [] and waits == [("groq", 0.0, 45.0)] and r.text == "from gemini"


def test_wait_line_in_the_cli():
    from agentic_ai import run
    e = {"agent": "writer", "kind": "revision", "t_s": 2.2,
         "detail": {"rate_limit": "groq", "wait_s": 7.66, "hint_s": 7.66, "cap_s": 20.0}}
    assert "groq rate limit (429): waiting 7.66 s (hint 7.66 s, cap 20 s), then retrying groq" in run.progress_line(e)
    e["detail"].update(wait_s=0.0, hint_s=45.0)
    assert "reset in 45 s > 20 s cap, not waiting" in run.progress_line(e)


def test_graph_logs_the_wait_before_the_retry(monkeypatch):
    from agentic_ai import graph
    from tests.agents.fake_tools import FakeTools
    calls, slept, _ = _seq_providers(monkeypatch, "none", [Groq429({"retry-after": "1"}), RuntimeError("down")])
    res = graph.run_alert(FakeTools(), "08158700|minor|2019-05-04T03:00:00Z")
    w = [e for e in res["events"] if e["kind"] == "revision" and "rate_limit" in e["detail"]]
    assert len(w) == 1 and w[0]["agent"] == "writer" and w[0]["detail"]["wait_s"] == 1.0 and slept == [1.0]
    assert res["outcome"] in ("saved", "v1_kept")
