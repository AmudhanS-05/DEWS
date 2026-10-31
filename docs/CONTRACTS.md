# Contracts between workstreams (v1, 30 Oct 2026)

The core pipeline, ML and agent workstreams are built in parallel. They meet only at the interfaces below.
Change one only together with a `BREADCRUMBS.md` line tagged with every affected workstream. All Kafka records are
JSON, keyed by `site_no` (USGS site number, text), and carry `mode` = `LIVE` or `SIMULATION`.

## Kafka records

**`gauge.usgs`, `replay.gauge`** (built): `type` = `reading` | `heartbeat`. Reading fields: `site_no`, `site_name`,
`parameter` (`00065` stage ft, `00060` discharge cfs), `series`, `method`, `value`, `unit`, `qualifiers` (`P`
provisional, `A` approved), `reading_time`/`reading_ts`, `fetched_at`/`fetched_ts`, `mode`, `revision`. Heartbeat:
`site_no`, `now`/`now_ts`, `mode`. Source of truth: `producers/tracking.py`.

**`rain.mrms`, `replay.rain`** (S2): `site_no`, `product`, `window_end`/`window_end_ts` (end of the accumulation
hour), `accum_hours`, `rain_mm` (mean depth over the 10 km circle), `n_cells`, `file_time`, `fetched_ts`, `mode`.

**`aggregates.window`** (built): `site_no`, `series`, `mode`, `window_start`, `window_end`, `window_start_ts`,
`n_readings` (0 = zero-filled), `revision`, `stage_last`, `stage_max`, `stage_min`, `rise_rate_ft_per_h`,
`carried_forward`, `provisional`, `last_reading_ts`, `window_close_time`; from S2 also `rain_1h`, `rain_3h`,
`rain_6h`. Source of truth: `streams/windowing.py`.

**`forecasts.stage`** (S1): one record per gauge per window.
`site_no`, `mode`, `window_start`, `model` (`rise_rate` | `persistence` | model name), `horizon_h`, `forecast_stage`,
`p_action`, `p_minor`, `p_moderate`, `p_major` (null if the model gives no probability), `made_at`, `window_start_ts`
(epoch, added 31 Oct) and `trace` = {`reading_ts`, `fetched_ts`, `window_close_time`}.

**`alerts.flood`** (S1): only when a forecast crosses an official category within the horizon.
`alert_id` (`site_no|category|episode_start`), `kind` = `new` | `upgrade` | `retract`, `site_no`, `site_name`, `mode`,
`category` (`action` | `minor` | `moderate` | `major`), `probability`, `horizon_h`, `forecast_stage`,
`current_stage`, `official_levels` {action, minor, moderate, major}, `provisional_data`, `model`, `raised_at`,
`episode_start`, `trace` = forecast trace + `forecast_time`.

**`alerts.approved`** (S1): written by the console only after a person approves. `alert_id`, `advisory_id`,
`version`, `text`, `approved_by`, `approved_at`, `mode`. The notifier reads only this topic.

## PostgreSQL (additions; existing tables in `storage/schema.sql`)

**`advisories`** (S1): `advisory_id` (`alert_id|version`), `alert_id`, `version` (1 = template, 2+ = agents),
`source` = `template` | `agents`, `text`, `confidence` = `normal` | `uncertain`, `status` = `draft` | `approved` |
`rejected` | `superseded` | `sent`, `reviewer_verdict`, `edited_text`, `created_at`, `approved_at`, `approved_by`,
`delivered_at`.

**`agent_events`** (S1, drives the console's live Agents tab): `event_id` (serial), `run_id`, `alert_id`, `agent`
(`coordinator` | `evidence` | `context` | `writer` | `reviewer` | `human`), `kind` = `start` | `end` | `tool_call` |
`token_chunk` | `verdict` | `disagreement` | `revision`, `detail` (JSON: tool name, short text, verdict reasons),
`ts`. Written by the agents as they run; read-only for the console.

**`agent_runs`** (S1 skeleton, S4 full): `run_id`, `alert_id`, `agent`, `input_summary`, `output`, `tool_calls`,
`started_at`, `ended_at`, `tokens`, `verdict`.

## ML model (S3)

`ml/predict.py` (called by `streams/forecaster.py`): `predict(features: dict) -> dict` with keys `forecast_stage`, `p_action`, `p_minor`, `p_moderate`,
`p_major`, `horizon_h`, `model`. `features` = the latest `aggregates.window` record for the gauge plus the previous
windows as `history` (oldest first) and `official_levels`. Must run in under 50 ms per call on the laptop and never
raise (return the baseline with `model = "rise_rate"` on any error).

## Agent tools (MCP server, S4; plain Python functions in S1)

Environment: `LLM_PROVIDER` = `groq` | `ollama` | `none`; `GROQ_API_KEY`; `OLLAMA_MODEL` (default `qwen2.5:7b`);
`LANGSMITH_TRACING`, `LANGSMITH_API_KEY` (optional). All in `.env`.

Read-only: `get_alert(alert_id)`, `get_recent_windows(site, hours)`, `get_forecast(site)`, `get_official_levels(site)`,
`get_past_floods(site)`, `get_rain(site, hours)`, `get_neighbours(site)`, `get_gauge_info(site)`,
`get_impacts(site, level)`, `get_episode_history(alert_id)`. Write (agents only): `save_advisory(...)`,
`log_agent_run(...)`. Agents never publish to Kafka and never send email.
