"""LLM provider switch (A1.4): LLM_PROVIDER = groq | gemini | ollama | none, plus LLM_FALLBACK = gemini | groq | none.

  groq    Groq free tier, GROQ_MODEL (default openai/gpt-oss-120b), key GROQ_API_KEY
  gemini  Google AI Studio free tier, GEMINI_MODEL (default gemini-3.5-flash-lite), key GEMINI_API_KEY
  ollama  local OLLAMA_MODEL (default qwen2.5:7b) at OLLAMA_HOST; optional, not needed on the laptop
  none    no model: complete() returns text=None and the agents use their deterministic path (tests always use this)

GROQ_REASONING_EFFORT = low (default) | medium | high: reasoning budget for Groq reasoning models (openai/gpt-oss-*);
ignored for models without it. Round 4: most completion tokens were reasoning (883-2890 for a ~200-token advisory).

Rate limit: on a 429 the call waits once for the provider's hint (retry-after, else x-ratelimit-reset-tokens /
-requests) when it is at most RATE_LIMIT_WAIT_CAP_S, then retries the same provider; a longer or missing hint goes
straight to LLM_FALLBACK (a capped wait shorter than the reset would only fail again). `on_wait(provider, seconds,
hint)` is called before sleeping so the CLI can show it.

LLM_FALLBACK: when the primary call fails (error, timeout, rate limit, empty reply) the same prompt is tried once on
the fallback provider; if that fails too the caller keeps its deterministic text (and template v1 stays).
Every call has a timeout. A failure never raises: complete() returns text=None with `error` set.

Judge (1 Nov, user decision "make Gemini the judge"): the reviewer's LLM judgement uses JUDGE_PROVIDER = gemini
(default) | groq | ollama | none, model JUDGE_MODEL (default: that provider's model variable / default), so the
writer's model does not grade its own draft. JUDGE_FALLBACK = none (default) | groq | gemini | ollama: only when set
is a failed judge call retried there (it may be the writer's model). LLM_FALLBACK is never used for the judge.
`same_model_judge()` says when judge and writer are the same provider and model (the reviewer logs a warning).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from agentic_ai.env import load_env

PROVIDERS = ("groq", "gemini", "ollama", "none")
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.5-flash-lite"   # 31 Oct: 2.5-flash 404 for new keys; 3.5-flash 504 after 30 s
OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_TIMEOUT_S = 30.0
REASONING_EFFORTS = ("low", "medium", "high")
DEFAULT_REASONING_EFFORT = "low"
RATE_LIMIT_WAIT_CAP_S = 20.0


@dataclass
class LlmReply:
    text: str | None
    tokens: int = 0
    provider: str = "none"
    model: str | None = None
    latency_s: float = 0.0
    error: str | None = None
    fallback_from: str | None = None    # set when the text came from LLM_FALLBACK after the primary failed
    primary_error: str | None = None
    waited_s: float = 0.0               # time slept on a 429 hint before the retry
    reasoning_effort: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    error_obj: Exception | None = None  # internal: the exception, for the rate-limit hint


def forced_down() -> bool:
    """LLM_FORCE_DOWN=1: every call fails immediately (the "LLM down" fault drill; also `with forced_down_ctx():`)."""
    return os.getenv("LLM_FORCE_DOWN", "").strip().lower() in ("1", "true", "yes", "on")


class forced_down_ctx:
    """Context manager for the fault drill: LLM down inside the block, previous setting restored after."""

    def __enter__(self):
        self._old = os.environ.get("LLM_FORCE_DOWN")
        os.environ["LLM_FORCE_DOWN"] = "1"
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("LLM_FORCE_DOWN", None)
        else:
            os.environ["LLM_FORCE_DOWN"] = self._old
        return False


def _pick(var: str, default: str) -> str:
    load_env()
    p = os.getenv(var, default).strip().lower()
    return p if p in PROVIDERS else "none"


def provider() -> str:
    return _pick("LLM_PROVIDER", "none")


def fallback() -> str:
    """The fallback provider, or 'none' (also when it equals the primary)."""
    f = _pick("LLM_FALLBACK", "none")
    return "none" if f == provider() else f


def judge_provider() -> str:
    """JUDGE_PROVIDER: who runs the reviewer's LLM judgement (default gemini, independent of the groq writer)."""
    return _pick("JUDGE_PROVIDER", "gemini")


def judge_model() -> str | None:
    """JUDGE_MODEL, else the judge provider's usual model (GEMINI_MODEL / GROQ_MODEL / OLLAMA_MODEL or default)."""
    load_env()
    return (os.getenv("JUDGE_MODEL") or "").strip() or model_name(judge_provider())


def judge_fallback() -> str:
    """JUDGE_FALLBACK (default none): retried there once when the judge call fails; 'none' when equal to the judge."""
    f = _pick("JUDGE_FALLBACK", "none")
    return "none" if f == judge_provider() else f


def same_model_judge() -> bool:
    """True when the judge is the writer's provider and model (the model would grade its own draft)."""
    p = provider()
    return p != "none" and judge_provider() == p and judge_model() == model_name(p)


def model_name(prov: str) -> str | None:
    load_env()
    return {"groq": os.getenv("GROQ_MODEL") or GROQ_MODEL, "gemini": os.getenv("GEMINI_MODEL") or GEMINI_MODEL,
            "ollama": os.getenv("OLLAMA_MODEL") or OLLAMA_MODEL}.get(prov)


def reasoning_effort(prov: str) -> str | None:
    """GROQ_REASONING_EFFORT for a Groq reasoning model (openai/gpt-oss-*), else None (parameter not sent)."""
    if prov != "groq" or not str(model_name("groq")).startswith("openai/gpt-oss"):
        return None
    e = (os.getenv("GROQ_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT).strip().lower()
    return e if e in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def _seconds(v) -> float | None:
    """'7.66s', '1m2.5s', '120ms', '3' -> seconds; None if unreadable."""
    if v is None:
        return None
    v = str(v).strip()
    try:
        return float(v)
    except ValueError:
        pass
    m = _DUR.match(v)
    if not m or not any(m.groups()):
        return None
    h, mi, se, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + se + ms / 1000


def rate_limit_hint(err: Exception | None) -> float | None:
    """Seconds to wait from a 429's headers (retry-after, else the larger x-ratelimit-reset-*); None if not a 429."""
    if err is None:
        return None
    resp = getattr(err, "response", None)
    status = getattr(err, "status_code", None) or getattr(resp, "status_code", None)
    if status != 429 and "429" not in str(err)[:40] and type(err).__name__ != "RateLimitError":
        return None
    h = getattr(resp, "headers", None) or {}
    ra = _seconds(h.get("retry-after"))
    if ra is not None:
        return ra
    resets = [x for x in (_seconds(h.get("x-ratelimit-reset-tokens")), _seconds(h.get("x-ratelimit-reset-requests")))
              if x is not None]
    return max(resets) if resets else None


def _build(prov: str, timeout_s: float, model: str | None = None):
    model = model or model_name(prov)
    if prov == "groq":
        from langchain_groq import ChatGroq
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        extra = {"reasoning_effort": reasoning_effort("groq")} if reasoning_effort("groq") else {}
        return ChatGroq(model=model, temperature=0.2, timeout=timeout_s, max_retries=0, api_key=key, **extra)
    if prov == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        return ChatGoogleGenerativeAI(model=model, temperature=0.2, timeout=timeout_s, max_retries=0, google_api_key=key)
    from langchain_ollama import ChatOllama
    return ChatOllama(model=model, temperature=0.2, base_url=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
                      client_kwargs={"timeout": timeout_s})


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):   # content blocks (Gemini): keep the text parts only
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _call(prov: str, system: str, user: str, timeout_s: float, model: str | None = None) -> LlmReply:
    t0 = time.monotonic()
    model = model or model_name(prov)
    try:
        msg = _build(prov, timeout_s, model).invoke([("system", system), ("human", user)])
        usage = getattr(msg, "usage_metadata", None) or {}
        text = _text(msg.content).strip()
        if not text:
            raise RuntimeError("empty reply")
        return LlmReply(text=text, tokens=int(usage.get("total_tokens", 0)), provider=prov, model=model,
                        latency_s=round(time.monotonic() - t0, 2), reasoning_effort=reasoning_effort(prov),
                        prompt_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)))
    except Exception as e:  # noqa: BLE001 - any failure leaves template v1 / deterministic text in place
        return LlmReply(text=None, provider=prov, model=model, latency_s=round(time.monotonic() - t0, 2),
                        error=f"{type(e).__name__}: {str(e)[:200]}", reasoning_effort=reasoning_effort(prov),
                        error_obj=e)


def _call_waiting(prov: str, system: str, user: str, timeout_s: float, on_wait=None,
                  model: str | None = None) -> LlmReply:
    """_call, and on a 429 with a hint <= RATE_LIMIT_WAIT_CAP_S: wait once, then call the same provider again."""
    reply = _call(prov, system, user, timeout_s, model)
    hint = rate_limit_hint(reply.error_obj) if reply.text is None else None
    if hint is None or hint > RATE_LIMIT_WAIT_CAP_S:
        if hint is not None and on_wait:
            _safe(on_wait, prov, 0.0, hint)          # hint too long: say so, go to the fallback
        return reply
    wait = round(max(hint, 0.5), 2)
    if on_wait:
        _safe(on_wait, prov, wait, hint)
    time.sleep(wait)
    second = _call(prov, system, user, timeout_s, model)
    second.waited_s = wait
    second.latency_s = round(reply.latency_s + wait + second.latency_s, 2)
    if second.text is None:
        second.error = f"{second.error} (after waiting {wait:g} s for the rate limit)"
    return second


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - progress output must not change the outcome
        pass


def complete(system: str, user: str, timeout_s: float = DEFAULT_TIMEOUT_S, on_wait=None) -> LlmReply:
    """One chat completion under the configured provider (one wait on a short 429 hint), retried once on
    LLM_FALLBACK on failure. Never raises."""
    return _complete(provider(), None, fallback(), system, user, timeout_s, on_wait)


def judge(system: str, user: str, timeout_s: float = DEFAULT_TIMEOUT_S, on_wait=None) -> LlmReply:
    """The reviewer's LLM judgement on JUDGE_PROVIDER / JUDGE_MODEL, retried once on JUDGE_FALLBACK only when that
    is set (never on LLM_FALLBACK). text=None with `error` when the judge is unavailable. Never raises."""
    return _complete(judge_provider(), judge_model(), judge_fallback(), system, user, timeout_s, on_wait)


def _complete(prov: str, model: str | None, fb: str, system: str, user: str, timeout_s: float, on_wait) -> LlmReply:
    model = model or model_name(prov)
    if forced_down():      # fault drill "LLM down" (C4.3): fail at once, as a provider outage would
        return LlmReply(text=None, provider=prov if prov != "none" else "forced_down", model=model,
                        error="LLM forced down (LLM_FORCE_DOWN=1, fault drill)")
    if prov == "none":
        return LlmReply(text=None, provider="none")
    reply = _call_waiting(prov, system, user, timeout_s, on_wait, model)
    if reply.text is None and fb != "none":
        second = _call(fb, system, user, timeout_s)
        second.fallback_from, second.primary_error = prov, reply.error
        second.latency_s = round(reply.latency_s + second.latency_s, 2)
        second.waited_s = reply.waited_s
        if second.text is None:
            second.error = f"{prov}: {reply.error}; {fb}: {second.error}"
        return second
    return reply
