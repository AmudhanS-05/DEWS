"""LLM provider switch (A1.4): LLM_PROVIDER = groq | ollama | none.

  groq    Llama 3.3 70B on Groq's free tier (key GROQ_API_KEY in .env)
  ollama  local qwen2.5:7b (OLLAMA_MODEL, OLLAMA_HOST) as the offline fallback
  none    no model: complete() returns text=None and the agents use their deterministic path (tests always use this)

Every call has a timeout. A failure never raises: complete() returns text=None with `error` set, and the caller
keeps the deterministic text (and, for the whole run, template v1 stays in place).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from agentic_ai.env import load_env

GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TIMEOUT_S = 20.0


@dataclass
class LlmReply:
    text: str | None
    tokens: int = 0
    provider: str = "none"
    model: str | None = None
    latency_s: float = 0.0
    error: str | None = None


def provider() -> str:
    load_env()
    p = os.getenv("LLM_PROVIDER", "none").strip().lower()
    return p if p in ("groq", "ollama", "none") else "none"


def _build(prov: str, timeout_s: float):
    if prov == "groq":
        from langchain_groq import ChatGroq
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        return ChatGroq(model=os.getenv("GROQ_MODEL", GROQ_MODEL), temperature=0.2, timeout=timeout_s,
                        max_retries=0, api_key=key), os.getenv("GROQ_MODEL", GROQ_MODEL)
    from langchain_ollama import ChatOllama
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    return ChatOllama(model=model, temperature=0.2, base_url=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
                      client_kwargs={"timeout": timeout_s}), model


def complete(system: str, user: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> LlmReply:
    """One chat completion under the configured provider. Never raises."""
    prov = provider()
    if prov == "none":
        return LlmReply(text=None, provider="none")
    t0 = time.monotonic()
    try:
        chat, model = _build(prov, timeout_s)
        msg = chat.invoke([("system", system), ("human", user)])
        usage = getattr(msg, "usage_metadata", None) or {}
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        return LlmReply(text=text.strip(), tokens=int(usage.get("total_tokens", 0)), provider=prov, model=model,
                        latency_s=round(time.monotonic() - t0, 2))
    except Exception as e:  # noqa: BLE001 - any failure leaves template v1 / deterministic text in place
        return LlmReply(text=None, provider=prov, latency_s=round(time.monotonic() - t0, 2),
                        error=f"{type(e).__name__}: {str(e)[:200]}")
