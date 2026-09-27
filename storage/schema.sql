-- Schema for Real-Time Natural Disaster Early Warning System
-- Applied automatically by docker-compose on first Postgres boot.

CREATE TABLE IF NOT EXISTS sensor_readings (
    id              BIGSERIAL PRIMARY KEY,
    station_id      TEXT NOT NULL,
    sensor_type     TEXT NOT NULL,       -- rainfall | temperature | wind_speed | humidity | water_level
    value           DOUBLE PRECISION NOT NULL,
    event_time      TIMESTAMPTZ NOT NULL,   -- time the sensor recorded the value
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_station_time ON sensor_readings (station_id, event_time);

-- One row per (station, sensor_type, window) after Kafka Streams/Faust aggregation
CREATE TABLE IF NOT EXISTS windowed_aggregates (
    id              BIGSERIAL PRIMARY KEY,
    station_id      TEXT NOT NULL,
    sensor_type     TEXT NOT NULL,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    min_value       DOUBLE PRECISION,
    max_value       DOUBLE PRECISION,
    avg_value       DOUBLE PRECISION,
    sample_count    INTEGER,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_windowed_station_window ON windowed_aggregates (station_id, window_start);

-- Output of the ML anomaly-detection stage
CREATE TABLE IF NOT EXISTS risk_scores (
    id              BIGSERIAL PRIMARY KEY,
    station_id      TEXT NOT NULL,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    risk_score      DOUBLE PRECISION NOT NULL,   -- e.g. isolation-forest anomaly score, normalized 0-1
    is_anomaly      BOOLEAN NOT NULL DEFAULT false,
    model_version   TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_scores_station_time ON risk_scores (station_id, window_start);

-- Alerts raised for the dashboard / notification layer / agentic AI consumption
CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    station_id      TEXT NOT NULL,
    alert_type      TEXT NOT NULL,        -- e.g. FLOOD_RISK, HEAT_ANOMALY, STORM_RISK
    severity        TEXT NOT NULL,        -- LOW | MEDIUM | HIGH | CRITICAL
    message         TEXT NOT NULL,
    risk_score_id   BIGINT REFERENCES risk_scores(id),
    raised_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged    BOOLEAN NOT NULL DEFAULT false,
    acknowledged_by TEXT,                 -- 'human' or an agent id, filled in Phase 2
    acknowledged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alerts_station_time ON alerts (station_id, raised_at);

-- Evaluation run log (Step 8 of the methodology): precision/recall/latency/etc.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_label           TEXT NOT NULL,          -- e.g. 'phase1-baseline', 'phase2-tuned'
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    precision_score     DOUBLE PRECISION,
    recall_score        DOUBLE PRECISION,
    f1_score            DOUBLE PRECISION,
    false_alarm_rate    DOUBLE PRECISION,
    avg_detection_latency_ms   DOUBLE PRECISION,
    throughput_msgs_per_sec    DOUBLE PRECISION,
    avg_alert_delivery_ms      DOUBLE PRECISION,
    recovery_time_s     DOUBLE PRECISION,       -- time to resume after a simulated failure
    notes               TEXT
);
