"""LangSmith tracing by environment variables only (A1.6). Optional: the system works without it.

Turn on in .env:  LANGSMITH_TRACING=true  and  LANGSMITH_API_KEY=...   (LANGSMITH_PROJECT optional)
LangGraph and the LangChain chat models then trace themselves; nothing else in the code changes. Without a key we
force tracing off so no background upload errors appear on an offline laptop.
"""
from __future__ import annotations

import os

from agentic_ai.env import load_env

DEFAULT_PROJECT = "dews-agents"


def configure() -> dict:
    """Normalise the tracing env vars and report the state (never returns the key)."""
    load_env()
    want = os.getenv("LANGSMITH_TRACING", "").strip().lower() in ("1", "true", "yes", "on")
    has_key = bool(os.getenv("LANGSMITH_API_KEY"))
    enabled = want and has_key
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    os.environ["LANGCHAIN_TRACING_V2"] = os.environ["LANGSMITH_TRACING"]
    if enabled:
        os.environ.setdefault("LANGSMITH_PROJECT", DEFAULT_PROJECT)
    reason = "on" if enabled else ("LANGSMITH_API_KEY missing" if want else "off")
    return {"enabled": enabled, "project": os.getenv("LANGSMITH_PROJECT") if enabled else None, "reason": reason}


def run_config(run_id: str, alert_id: str, mode: str | None = None) -> dict:
    """LangGraph config: a readable run name plus tags and metadata that make traces searchable by alert and mode."""
    return {"run_name": f"advisory {alert_id}", "tags": ["dews", "agents"] + ([mode] if mode else []),
            "metadata": {"run_id": run_id, "alert_id": alert_id, "mode": mode}}
