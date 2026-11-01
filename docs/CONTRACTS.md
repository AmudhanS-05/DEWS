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
`carried_forward`, `provisional`, `last_reading_ts`, `fetched_ts`, `ingest_ts` (when the windowing worker read that
last reading; added 31 Oct, null in a zero-filled window), `window_close_time`; from S2 also `rain_1h`, `rain_3h`,
`rain_6h`. Source of truth: `streams/windowing.py`.

**`forecasts.stage`** (S1): one record per gauge per window.
`site_no`, `mode`, `window_start`, `model` (`rise_rate` | `persistence` | model name), `horizon_h`, `forecast_stage`,
`p_action`, `p_minor`, `p_moderate`, `p_major` (null if the model gives no probability), `made_at`, `window_start_ts`
(epoch, added 31 Oct) and `trace` = {`reading_ts`, `fetched_ts`, `ingest_ts`, `window_close_time`}.

**`alerts.flood`** (S1): only when a forecast crosses an official category within the horizon.
`alert_id` (`site_no|category|episode_start`), `kind` = `new` | `upgrade` | `retract`, `site_no`, `site_name`, `mode`,
`category` (`action` | `minor` | `moderate` | `major`), `probability`, `horizon_h`, `forecast_stage`,
`current_stage`, `official_levels` {action, minor, moderate, major}, `provisional_data`, `model`, `raised_at` (wall
clock when it was raised), `window_start` / `window_start_ts` (the data time of the window that raised it, added 31
Oct; for a replay this is the historical time, `raised_at` is not), `episode_start`, `trace` = forecast trace +
`forecast_time`.

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
`started_at`, `ended_at`, `tokens`, `verdict`, `trace_run_id`, `trace_url` (agents 1 Nov: the LangSmith root run id
on every row of a run when tracing is on, and its web URL filled after the run; both NULL when tracing is off or
LangSmith is unreachable). Core, for storage/schema.sql:
`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS trace_run_id TEXT; ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS trace_url TEXT;`
(agentic_ai/schema_agents.sql already runs both). Console link: `SELECT DISTINCT run_id, trace_url FROM agent_runs`.

## ML model (S3)

`ml/predict.py` (called by `streams/forecaster.py`): `predict(features: dict) -> dict` with keys `forecast_stage`, `p_action`, `p_minor`, `p_moderate`,
`p_major`, `horizon_h`, `model`. `features` = the latest `aggregates.window` record for the gauge plus the previous
windows as `history` (oldest first) and `official_levels`. Must run in under 50 ms per call on the laptop and never
raise (return the baseline with `model = "rise_rate"` on any error).

**Sensor-fault flag (S3, PROPOSED by ml, revised 31 Oct 2026 after the team decision; core and agents to accept
or amend):** `ml/fault_rules.py`: `check_gauge(readings, site_no, ctx=None) -> {suspect, reasons, flags}`.
`readings` = the gauge's recent `gauge.usgs` / `replay.gauge` readings, oldest first (at least the last 48 h; the
rules work on readings, not on hourly windows). `ctx` = `Context.from_windows(windows_by_site, rain_by_site)` built
from recent `aggregates.window` records of all gauges (neighbours within 60 km) and, if available, hourly rain per
gauge from `rain.mrms`; without `ctx` the stuck and step rules cannot find independent evidence and stay silent.
`reasons` is a list from the SHIPPED rules only: `no_readings` (>= 3 h without readings), `stuck` (identical value
for >= 24 h while a nearby gauge moves >= 0.1 ft or >= 5 mm of rain fell), `step` (a jump that holds flat, with no
rain and no matching change at nearby gauges); `unavailable` on error. `flags` = {rule: [flag_ts]}. Never raises;
settings and per-gauge scales in `ml/models/fault_rules.json`. Shipping rule (team): a rule is included only if its
false-flag rate on real flood windows (12 h before to 6 h after a minor crossing) is <= 1 % on the 2024-2026 test
period; measured 0.49 % (no_readings), 0 % (stuck), 0 % (step). The spike rule is implemented but NOT shipped
(1.07 %). The Isolation Forest (`ml/anomaly.py`) is not part of the contract (measured negative result,
ml/MODEL_CARD.md). Proposed use: core shows `suspect`/`reasons` next to the gauge in the console; agents may say
"readings at <gauge> look like a sensor fault (<reason>)". A flag never suppresses or raises a flood alert.

## Agent tools (MCP server, S4; plain Python functions in S1)

Environment: `LLM_PROVIDER` = `groq` | `gemini` | `ollama` | `none`; `GROQ_API_KEY`; `GROQ_MODEL` (default
`openai/gpt-oss-120b`); `GROQ_REASONING_EFFORT` = `low` (default) | `medium` | `high` (only for `openai/gpt-oss-*`, else not sent); `LLM_FALLBACK` = `gemini` | `groq` | `none` (a Groq 429 first waits once for its reset hint when it is <= 20 s and retries Groq; a
failed call is then retried once on it, then template v1 stays); `GEMINI_API_KEY`; `GEMINI_MODEL` (default `gemini-3.5-flash-lite`; `gemini-2.5-flash` is closed to new keys); `AGENT_TOOLS` = `mcp` (default; the agents' tools via the stdio server `agentic_ai/mcp_server.py`, direct-call
fallback) | `direct`; `OLLAMA_MODEL` (default
`qwen2.5:7b`, optional local provider); `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` (optional,
default `dews-agents`). All in `.env`.

Read-only: `get_alert(alert_id)`, `get_recent_windows(site, hours)`, `get_forecast(site)`, `get_official_levels(site)`,
`get_past_floods(site)`, `get_rain(site, hours)`, `get_neighbours(site)`, `get_gauge_info(site)`,
`get_impacts(site, level)`, `get_episode_history(alert_id)`, `get_edit_examples(site, mode, limit)` (officer edits
as wording examples, agents 1 Nov), `get_data_quality(site, until, mode)` (agents call ml's `check_gauge` on
`gauge_readings`; an alert field `data_quality` = {suspect, reasons} from core would take precedence, optional).
Write (agents only): `save_advisory(...)`,
`log_agent_run(...)`. Agents never publish to Kafka and never send email.
