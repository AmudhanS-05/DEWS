-- Tables the agents read and write (docs/CONTRACTS.md). Idempotent: agentic_ai.db.ensure_schema() runs it.
-- Core: when you add these to storage/schema.sql, keep the columns identical and delete this file's duplicates.

CREATE TABLE IF NOT EXISTS advisories (
    advisory_id     TEXT PRIMARY KEY,               -- alert_id|version
    alert_id        TEXT NOT NULL,
    version         INTEGER NOT NULL,               -- 1 = template, 2+ = agents
    source          TEXT NOT NULL,                  -- template | agents
    text            TEXT NOT NULL,
    confidence      TEXT NOT NULL DEFAULT 'normal', -- normal | uncertain
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected | superseded | sent
    reviewer_verdict TEXT,
    edited_text     TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,
    delivered_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id        BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    alert_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,   -- coordinator | evidence | context | writer | reviewer | human
    kind            TEXT NOT NULL,   -- start | end | tool_call | token_chunk | verdict | disagreement | revision
    detail          JSONB,
    ts              TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    alert_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,
    input_summary   TEXT,
    output          JSONB,
    tool_calls      JSONB,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    tokens          INTEGER,
    verdict         TEXT
);
-- 1 Nov: LangSmith link per run (NULL when tracing is off). Idempotent for tables created before.
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS trace_run_id TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS trace_url TEXT;

-- NWS impact statements per stage (NWPS). Empty until loaded: the agents then say none is available.
CREATE TABLE IF NOT EXISTS gauge_impacts (
    site_no         TEXT NOT NULL,
    stage_ft        DOUBLE PRECISION NOT NULL,
    category        TEXT,
    statement       TEXT NOT NULL,
    PRIMARY KEY (site_no, stage_ft, statement)
);

-- Past flood events per gauge (ML track's 213-event table when it lands). Empty until loaded.
CREATE TABLE IF NOT EXISTS past_floods (
    site_no         TEXT NOT NULL,
    crossing_time   TIMESTAMPTZ NOT NULL,
    category        TEXT NOT NULL,
    peak_stage_ft   DOUBLE PRECISION,
    rise_time_h     DOUBLE PRECISION,
    PRIMARY KEY (site_no, crossing_time, category)
);

CREATE INDEX IF NOT EXISTS agent_events_run ON agent_events (run_id, event_id);
