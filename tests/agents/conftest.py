"""Agent tests run with LLM_PROVIDER=none and no network. Tests marked `pg` need a PostgreSQL: set AGENTS_TEST_DSN
(any empty/scratch database, e.g. the docker one); each session works in its own throwaway schema and drops it."""
import os
import uuid

import pytest

os.environ["LLM_PROVIDER"] = "none"
os.environ["LLM_FALLBACK"] = "none"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)


def _throwaway_schema():
    dsn = os.getenv("AGENTS_TEST_DSN")
    if not dsn:
        pytest.skip("AGENTS_TEST_DSN not set: PostgreSQL tests skipped")
    import psycopg2
    from agentic_ai.db import ensure_schema
    schema = "agents_t_" + uuid.uuid4().hex[:8]
    conn = psycopg2.connect(dsn, connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
    ensure_schema(conn, include_core=True)
    yield conn
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.close()


@pytest.fixture(scope="session")
def pg_conn():
    """Throwaway schema with the ILLUSTRATIVE seed (made-up rain/probabilities; exercises every branch)."""
    yield from _throwaway_schema()


@pytest.fixture()
def pg_empty():
    """A fresh throwaway schema per test (for the measured-replay seed and mixed-data regressions)."""
    yield from _throwaway_schema()


@pytest.fixture(scope="session")
def seeded(pg_conn):
    from agentic_ai import seed
    return seed.load(pg_conn, illustrative=True)


@pytest.fixture()
def tools(pg_conn, seeded):
    from agentic_ai.tools import Tools
    return Tools(pg_conn)
