-- Read-only views for the console and Power BI (C1.2). Idempotent: apply with
--   docker exec -i flood-postgres psql -U spa_user -d disaster_warning < storage/views.sql

-- Newest advisory per alert that has not been superseded or rejected (v2 from the agents replaces v1)
CREATE OR REPLACE VIEW current_advisory AS
SELECT DISTINCT ON (alert_id) alert_id, advisory_id, version, source, text, confidence, status,
       reviewer_verdict, edited_text, created_at, approved_at, approved_by, delivered_at
FROM advisories
WHERE status NOT IN ('superseded', 'rejected')
ORDER BY alert_id, version DESC;

-- One row per alert with its current advisory and the time from raise to approval to delivery
CREATE OR REPLACE VIEW alert_overview AS
SELECT a.alert_id, a.site_no, a.mode, a.category, a.probability, a.horizon_h, a.status AS alert_status,
       a.raised_at, a.approved_at, a.delivered_at,
       c.version AS advisory_version, c.source AS advisory_source, c.confidence, c.status AS advisory_status,
       EXTRACT(EPOCH FROM (a.approved_at - a.raised_at)) AS raise_to_approval_s,
       EXTRACT(EPOCH FROM (a.delivered_at - a.approved_at)) AS approval_to_delivery_s
FROM alerts a LEFT JOIN current_advisory c USING (alert_id);

-- Latency waterfall per trace: seconds spent between consecutive pipeline stages
CREATE OR REPLACE VIEW latency_waterfall AS
SELECT trace_id, site_no, mode, reading_time,
       EXTRACT(EPOCH FROM (fetched_at - reading_time))        AS feed_delay_s,
       EXTRACT(EPOCH FROM (ingest_time - fetched_at))         AS ingest_s,
       EXTRACT(EPOCH FROM (window_close_time - ingest_time))  AS windowing_s,
       EXTRACT(EPOCH FROM (forecast_time - window_close_time)) AS forecast_s,
       EXTRACT(EPOCH FROM (alert_time - forecast_time))       AS alert_s,
       EXTRACT(EPOCH FROM (approved_time - alert_time))       AS human_s,
       EXTRACT(EPOCH FROM (delivered_time - approved_time))   AS delivery_s,
       EXTRACT(EPOCH FROM (delivered_time - fetched_at))      AS net_total_s,
       EXTRACT(EPOCH FROM (delivered_time - reading_time))    AS gross_total_s
FROM latency_trace;

-- One row per agent run: when it ran, how many events, whether a reviewer verdict was reached
CREATE OR REPLACE VIEW agent_run_summary AS
SELECT run_id, alert_id, min(ts) AS started_at, max(ts) AS ended_at,
       EXTRACT(EPOCH FROM (max(ts) - min(ts))) AS duration_s,
       count(*) AS events,
       count(*) FILTER (WHERE kind = 'tool_call') AS tool_calls,
       count(*) FILTER (WHERE agent = 'reviewer' AND kind = 'verdict') AS review_rounds,
       (array_agg(detail->>'verdict' ORDER BY event_id DESC) FILTER (WHERE kind = 'verdict'))[1] AS last_verdict
FROM agent_events GROUP BY run_id, alert_id;
