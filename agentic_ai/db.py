"""PostgreSQL access for the agents. DSN comes from common.config (env PG_DSN)."""
from __future__ import annotations

from pathlib import Path

import psycopg2

from common.config import PG_DSN

SCHEMA_FILES = [Path(__file__).resolve().parents[1] / "storage" / "schema.sql",
                Path(__file__).with_name("schema_agents.sql")]


def connect(dsn: str | None = None):
    conn = psycopg2.connect(dsn or PG_DSN, connect_timeout=5)
    conn.autocommit = True
    return conn


def ensure_schema(conn, include_core: bool = False) -> None:
    """Create the agent tables (and, for a throwaway test database, the core tables too). Idempotent."""
    files = SCHEMA_FILES if include_core else SCHEMA_FILES[1:]
    with conn.cursor() as cur:
        for f in files:
            cur.execute(f.read_text(encoding="utf-8"))
