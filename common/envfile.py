"""Minimal .env loader for core (no extra dependency). Never prints values; does not override variables already set."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None) -> None:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
