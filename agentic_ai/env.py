"""Minimal .env loader (no extra dependency). Never prints values."""
from __future__ import annotations

import os
from pathlib import Path

_DONE = False


def load_env(path: str | Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ without overriding variables already set."""
    global _DONE
    if _DONE and path is None:
        return
    _DONE = True
    p = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
