-- Flood / flash-flood forecasting and alerting schema (rev 3, proposed).
-- Loaded automatically by docker compose on FIRST start of an empty volume.
-- Changed it later? Run: docker compose down -v   (wipes data) and up again,
-- or apply the change by hand with psql.

CREATE TABLE IF NOT EXISTS gauges (
    site_no         TEXT PRIMARY KEY,
    nwps_lid        TEXT,
    name            TEXT,
    stage_units     TEXT,
    action_stage    DOUBLE PRECISION,   -- official NWPS categories = the labels
    minor_stage     DOUBLE PRECISION,
    moderate_stage  DOUBLE PRECISION,
    major_stage     DOUBLE PRECISION,
    median_rise_h   DOUBLE PRECISION,   -- from the Phase 0 gate check
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gauge_readings (
    site_no         TEXT NOT NULL,
    parameter       TEXT NOT NULL,      -- 00065 gage height, 00060 discharge
    series          TEXT NOT NULL,      -- one site can report several sensors at the same time
    reading_time    TIMESTAMPTZ NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    unit            TEXT,
    qualifiers      TEXT[],             -- 'P' = provisional
    mode            TEXT NOT NULL DEFAULT 'LIVE',   -- LIVE | SIMULATION
    fetched_at      TIMESTAMPTZ,        -- when our producer first saw it
    ingested_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (site_no, parameter, series, reading_time, mode)
);

CREATE TABLE IF NOT EXISTS catchment_rain (
    site_no         TEXT NOT NULL,
    product         TEXT NOT NULL,      -- e.g. MRMS MultiSensor_QPE_01H_Pass2
    window_end      TIMESTAMPTZ NOT NULL,
    accum_hours     INTEGER NOT NULL,
    rain_mm         DOUBLE PRECISION,
    computed_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (site_no, product, window_end, accum_hours)
);

CREATE TABLE IF NOT EXISTS windowed_aggregates (
    site_no             TEXT NOT NULL,
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'LIVE',
    n_readings          INTEGER NOT NULL,       -- 0 = zero-filled window (no report)
    stage_last          DOUBLE PRECISION,
    stage_max           DOUBLE PRECISION,
    stage_min           DOUBLE PRECISION,
    rise_rate_ft_per_h  DOUBLE PRECISION,
    rain_1h             DOUBLE PRECISION,
    rain_3h             DOUBLE PRECISION,
    rain_6h             DOUBLE PRECISION,
    provisional         BOOLEAN,
    emitted_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (site_no, window_start, mode)
);

CREATE TABLE IF NOT EXISTS risk_scores (
    id                  BIGSERIAL PRIMARY KEY,
    site_no             TEXT NOT NULL,
    window_start        TIMESTAMPTZ NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'LIVE',
    model               TEXT NOT NULL,          -- persistence | rise_rate | <ml model name>
    horizon_h           DOUBLE PRECISION NOT NULL,
    forecast_stage      DOUBLE PRECISION,
    p_action            DOUBLE PRECISION,
    p_minor             DOUBLE PRECISION,
    p_moderate          DOUBLE PRECISION,
    p_major             DOUBLE PRECISION,
    computed_at         TIMESTAMPTZ DEFAULT now()
);

-- One forecast per gauge, window, model and horizon: lets the sink upsert (at-least-once delivery, no duplicates)
CREATE UNIQUE INDEX IF NOT EXISTS risk_scores_uniq ON risk_scores (site_no, window_start, mode, model, horizon_h);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id            TEXT PRIMARY KEY,
    site_no             TEXT NOT NULL,
    mode                TEXT NOT NULL,          -- LIVE | SIMULATION
    category            TEXT NOT NULL,          -- action | minor | moderate | major
    probability         DOUBLE PRECISION,
    horizon_h           DOUBLE PRECISION,
    provisional_data    BOOLEAN,
    status              TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected | sent | retracted
    message             TEXT,
    raised_at           TIMESTAMPTZ NOT NULL,
    approved_at         TIMESTAMPTZ,
    approved_by         TEXT,
    delivered_at        TIMESTAMPTZ,
    channel             TEXT,
    details             JSONB               -- the alert record as published (stages, levels, model): facts for the bulletin
);

CREATE TABLE IF NOT EXISTS latency_trace (
    trace_id            TEXT PRIMARY KEY,       -- site|series|reading epoch
    site_no             TEXT NOT NULL,
    mode                TEXT NOT NULL,
    reading_time        TIMESTAMPTZ,
    fetched_at          TIMESTAMPTZ,
    ingest_time         TIMESTAMPTZ,
    window_close_time   TIMESTAMPTZ,
    forecast_time       TIMESTAMPTZ,
    alert_time          TIMESTAMPTZ,
    approved_time       TIMESTAMPTZ,
    delivered_time      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id              BIGSERIAL PRIMARY KEY,
    run_label           TEXT NOT NULL,
    data_source         TEXT NOT NULL,          -- live | replay (never merged)
    site_no             TEXT,
    model               TEXT,
    horizon_h           DOUBLE PRECISION,
    precision           DOUBLE PRECISION,
    recall              DOUBLE PRECISION,
    f1                  DOUBLE PRECISION,
    false_alarms        INTEGER,
    median_lead_time_h  DOUBLE PRECISION,
    mae_ft              DOUBLE PRECISION,
    rmse_ft             DOUBLE PRECISION,
    throughput_msgs_s   DOUBLE PRECISION,
    recovery_time_s     DOUBLE PRECISION,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Feed staleness per reading, for the dashboard
CREATE OR REPLACE VIEW feed_staleness AS
SELECT site_no, series, reading_time, fetched_at,
       EXTRACT(EPOCH FROM (fetched_at - reading_time)) / 60.0 AS staleness_min
FROM gauge_readings
WHERE mode = 'LIVE' AND fetched_at IS NOT NULL;

-- Advisories and agent tables (C1.2; same columns as agentic_ai/schema_agents.sql, which stays idempotent)
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

-- databases created before C1.5 gain the column
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS details JSONB;
