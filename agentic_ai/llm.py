"""LLM provider switch (A1.4): LLM_PROVIDER = groq | gemini | ollama | none, plus LLM_FALLBACK = gemini | groq | none.

  groq    Groq free tier, GROQ_MODEL (default openai/gpt-oss-120b), key GROQ_API_KEY
  gemini  Google AI Studio free tier, GEMINI_MODEL (default gemini-3.5-flash-lite), key GEMINI_API_KEY
  ollama  local OLLAMA_MODEL (default qwen2.5:7b) at OLLAMA_HOST; optional, not needed on the laptop
  none    no model: complete() returns text=None and the agents use their deterministic path (tests always use this)

LLM_FALLBACK: when the primary call fails (error, timeout, rate limit, empty reply) the same prompt is tried once on
the fallback provider; if that fails too the caller keeps its deterministic text (and template v1 stays).
Every call has a timeout. A failure never raises: complete() returns text=None with `error` set.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from agentic_ai.env import load_env

PROVIDERS = ("groq", "gemini", "ollama", "none")
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.5-flash-lite"   # 31 Oct: 2.5-flash 404 for new keys; 3.5-flash 504 after 30 s
OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_TIMEOUT_S = 30.0


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


def model_name(prov: str) -> str | None:
    load_env()
    return {"groq": os.getenv("GROQ_MODEL") or GROQ_MODEL, "gemini": os.getenv("GEMINI_MODEL") or GEMINI_MODEL,
            "ollama": os.getenv("OLLAMA_MODEL") or OLLAMA_MODEL}.get(prov)


def _build(prov: str, timeout_s: float):
    model = model_name(prov)
    if prov == "groq":
        from langchain_groq import ChatGroq
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        return ChatGroq(model=model, temperature=0.2, timeout=timeout_s, max_retries=0, api_key=key)
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


def _call(prov: str, system: str, user: str, timeout_s: float) -> LlmReply:
    t0 = time.monotonic()
    try:
        msg = _build(prov, timeout_s).invoke([("system", system), ("human", user)])
        usage = getattr(msg, "usage_metadata", None) or {}
        text = _text(msg.content).strip()
        if not text:
            raise RuntimeError("empty reply")
        return LlmReply(text=text, tokens=int(usage.get("total_tokens", 0)), provider=prov, model=model_name(prov),
                        latency_s=round(time.monotonic() - t0, 2))
    except Exception as e:  # noqa: BLE001 - any failure leaves template v1 / deterministic text in place
        return LlmReply(text=None, provider=prov, model=model_name(prov), latency_s=round(time.monotonic() - t0, 2),
                        error=f"{type(e).__name__}: {str(e)[:200]}")


def complete(system: str, user: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> LlmReply:
    """One chat completion under the configured provider, retried once on LLM_FALLBACK on failure. Never raises."""
    prov = provider()
    if prov == "none":
        return LlmReply(text=None, provider="none")
    reply = _call(prov, system, user, timeout_s)
    fb = fallback()
    if reply.text is None and fb != "none":
        second = _call(fb, system, user, timeout_s)
        second.fallback_from, second.primary_error = prov, reply.error
        second.latency_s = round(reply.latency_s + second.latency_s, 2)
        if second.text is None:
            second.error = f"{prov}: {reply.error}; {fb}: {second.error}"
        return second
    return reply
