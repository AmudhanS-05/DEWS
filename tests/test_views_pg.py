"""storage/schema.sql and storage/views.sql apply twice (idempotent) and the views behave. Needs AGENTS_TEST_DSN
(any scratch database); works in a throwaway schema it drops afterwards."""
import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def conn():
    dsn = os.getenv("AGENTS_TEST_DSN")
    if not dsn:
        pytest.skip("AGENTS_TEST_DSN not set: PostgreSQL tests skipped")
    import psycopg2
    schema = "views_t_" + uuid.uuid4().hex[:8]
    c = psycopg2.connect(dsn, connect_timeout=5)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
    yield c
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


def apply(conn):
    with conn.cursor() as cur:
        for f in ("schema.sql", "views.sql"):
            cur.execute((ROOT / "storage" / f).read_text(encoding="utf-8"))


def test_apply_twice_and_tables_present(conn):
    apply(conn)
    apply(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
        names = {r[0] for r in cur.fetchall()}
    assert {"advisories", "agent_runs", "agent_events", "gauge_impacts", "past_floods", "alerts"} <= names


def test_current_advisory_prefers_newest_unsuperseded(conn):
    apply(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO alerts (alert_id, site_no, mode, category, raised_at) "
                    "VALUES ('a1','08158700','SIMULATION','minor', now())")
        cur.execute("INSERT INTO advisories (advisory_id, alert_id, version, source, text, status) VALUES "
                    "('a1|1','a1',1,'template','v1','superseded'),('a1|2','a1',2,'agents','v2','draft')")
        cur.execute("SELECT advisory_version, advisory_source FROM alert_overview WHERE alert_id = 'a1'")
        assert cur.fetchone() == (2, "agents")
