# Agentic AI layer: multi-agent flood advisories (workstream `agents`)

Turns a forecaster alert (`alerts.flood` / the `alerts` table) into a checked, plain-language advisory **draft**
(version 2+, the template text from the sink is version 1). Agents draft and check only: they never produce to
Kafka and never send email. A person approves a draft in the console; only approved text goes to `alerts.approved`,
and only the notifier sends. Design: `docs/BUILD_PHASES.md` "Phase 6b"; interfaces: `docs/CONTRACTS.md`.

## What runs (LangGraph, Coordinator-Worker-Delegator)

```
coordinator -> [evidence || context] -> reconcile -> writer -> reviewer --pass--> save_advisory (v2+, draft)
                                                        ^---- fail (at most 2 revisions) ----'  \-- still failing --> template v1 stays
```

| Agent | Does | Tools (PostgreSQL, `tools.py`) |
|---|---|---|
| coordinator | loads the alert, drops duplicates and retractions, counts the update number in the episode, reads the episodic memory (earlier alerts of the episode, officer edits), plans | get_alert, get_episode_history, get_edit_examples |
| evidence | the gauge's own windows and forecast (p_minor from s3_v0), official levels, sensor-fault flag; direction rising / not rising | get_recent_windows, get_forecast, get_official_levels, get_past_floods, get_data_quality |
| context | radar rain, upstream/downstream/nearby gauges, NWS impact statements; direction, signal kind (LEADING / SUPPORTING) or `unknown` | get_rain, get_neighbours, get_gauge_info, get_impacts |
| reconcile | evidence and context disagree -> advisory marked UNCERTAIN, both signals stated | (pure rules, `rules.py`) |
| writer | LLM draft from the facts (or the deterministic template when there is no LLM or it fails) | `llm.py`, `advisory.py` |
| reviewer | deterministic checks first (`checks.py`), then an LLM judgement on a draft that passed them | `checks.py`, `llm.py` |

**Tools over MCP (A4.1).** The agents reach their tools through the Model Context Protocol: `run_alert` wraps the
database `Tools` in `mcp_client.McpTools`, which starts `python -m agentic_ai.mcp_server` (stdio) with the same
database and calls each tool there; if the server cannot start, a call errors or times out (10 s), the same call is
made directly (`via: direct (mcp failed: ...)` in the event). The run log (`agent_events`, `agent_runs`) is written
directly, not as an MCP tool. The CLI's last line says how many calls went via MCP.

Every step writes `agent_events` (drives the console's Agents tab) and `agent_runs`; the result is a row in
`advisories`. Any failure (tool crash, LLM down, reviewer still failing) leaves template v1 in place.

**Numbers come from the alert's own window.** `get_alert` finds the window the alert was raised on
(`tools.anchor_window`): the window ending at `raised_at`, else the first window from the episode start whose
forecast reaches the alert's category while the gauge is still below it (a Kafka replay stamps `raised_at` with
wall-clock time, so "latest window before raised_at" would be the end of the replay). Evidence and context read up
to that window (`as_of`) and use the forecast row with the alert's horizon.

**Context signals** (`rules.context_signals`, user decision 31 Oct): radar rain and upstream gauges are LEADING
signals (water on its way to the gauge); downstream and nearby gauges rising in the same window are SUPPORTING
context only (the storm affects the area). The text names the kind and never says a downstream or nearby rise causes
or predicts the rise at this gauge (deterministic check `context_kind`). `unknown` only when there is no radar rain
and no gauge reading at all. Only a LEADING signal can disagree with the gauge evidence (-> UNCERTAIN); quiet
neighbouring gauges do not, because a flash flood can stay local. Driftwood, May 2019: downstream Onion Creek at US 183
+6.1 ft and nearby Slaughter Creek +7.4 ft in 3 h -> `rising: SUPPORTING signal only`, no leading signal.

**Facts the writer and reviewer see** are in plain words: `data_source` ("a replay of a historical USGS gauge record
(mode SIMULATION), not a real-time event", or "live USGS gauge readings"), `data_status` ("provisional per USGS (may
be revised)" / "approved per USGS"), the model as "rise-rate baseline", `readings_time_utc`, and, when radar rain is
missing, `rain_note` (the MRMS archive starts 2020-10-14, so a 2019 replay never has radar rain). Booleans and raw
field names never reach the text (deterministic check `raw_field`: snake_case names such as as_of, true/false,
"provisional data not available").

**The alert's own window:** `get_alert` uses `window_start` / `window_start_ts` from the alert (core adds them to
`alerts.flood` since 31 Oct; the sink keeps them in `alerts.details`); older rows without them use the fallback below.

**The advisory text** (template and LLM alike) follows the deterministic checks, each of which is also a numbered
writer instruction with the exact values (a test keeps the two lists equal):
numbers only from the facts; stages and rates to 0.1 ft; the alert's official category and its stage; when the
forecast stage crosses a higher official category, that category and its stage too ("forecast to reach MAJOR
(13.0 ft)", "Alert category: MINOR (5.5 ft)"), never "forecast to reach MINOR"; probability as a whole % or
"not available" when the model gives none; horizon in hours; lead time in hours or "not available"; "rain data not
available" when radar rain is missing; never null/None/NaN/"?"; no raw field names or
true/false; downstream/nearby gauges only as a supporting signal; no Markdown; SIMULATION label on replayed data (and
never on live data); "provisional" for provisional data; UNCERTAIN with both signals when they disagree; no
certainty language; at most 900 characters.

**Episodic memory (A3.3).** The coordinator reads the flood episode (same gauge, same episode start): a repeat alert
gets "Update n:" (and "(upgraded from MINOR)" when the category rose); the facts carry an `episode` section in words
only (earlier categories, earlier advisories and whether the officer edited them). Duty-officer edits (`edited_text`
that the console stores on approval, same mode, this gauge first) go to the writer as WORDING EXAMPLES with every
number replaced by `#`. Guard: the reviewer's number check skips the `episode` section, so a number from an earlier
alert is rejected; numbers come only from the current evidence. Deterministic check `update`: "Update n" exactly on
repeat alerts, never on a first alert.

**s3_v0 probabilities and the sensor-fault flag.** ml's active model `s3_v0` (3 h) gives `p_minor` only. When the
alert's own category has no probability (e.g. a MAJOR alert) the text says "probability of reaching MAJOR: not
available" and adds "probability of reaching MINOR: 97%" (model named "trained S3 model v0", never the raw id).
`get_data_quality` runs `ml.fault_rules.check_gauge` (shipped rules no_readings / stuck / step, docs/CONTRACTS.md) on
the last 48 h of raw readings in `gauge_readings` with neighbour windows as context; if the alert itself carries
`data_quality` (core may add it) that is used instead. A flagged gauge -> "readings at this gauge look like a sensor
fault (...); the data are questionable" (deterministic check `suspect_data`). No raw readings stored -> not checked,
nothing said. A flag never blocks or changes the alert.

**Alerts raised by the model's probability (core 1 Nov, `alert_rule = alert_by_model`, p_minor >= `tau` at 3 h;
both read from the alert record in `alerts.details`).** The headline is the probability, never the stage forecast:
"probability of crossing MINOR flood stage (10.0 ft) within 3 h: 89% (model alert threshold 83%)". When the model's
own stage forecast stays below the category (LLAT2 2023-10-26: 5.4 ft vs MINOR 10.0 ft) the advisory is UNCERTAIN and
says, in the facts' exact sentence (`advisory.disagreement_sentence`): "UNCERTAIN: the two forecasts disagree. The
model's probability of crossing MINOR within 3 h is 89%, but its stage forecast is 5.4 ft, below the MINOR stage (10.0
ft). Treat as uncertain and re-check at the next window."; lead time "not available (the two forecasts disagree)".
Deterministic check `model_rule` (the sentence's parts present; "forecast to reach MINOR" rejected); the LLM judge is
told this wording is correct. Under `alert_rule = forecast_stage` (rise-rate horizons) the wording is unchanged.

**Reviewer checks added in A3.1** (each also a writer requirement): `category_stage` (a category paired with another
category's stage), `threshold` (danger/warning levels or made-up categories instead of the official NWPS stages),
`cause` (no "caused by / due to / dam release" outside data notes), `context_kind` on every alert, `update`,
`suspect_data`. Seeded-error suite: `python -m agentic_ai.eval.catch_rate` -> `agentic_ai/eval/reviewer_catch_rate.md`.

## Files

| File | |
|---|---|
| `graph.py` | the LangGraph graph, event log, `run_alert()` (never raises) |
| `tools.py` | read tools, `anchor_window`, writes (`save_advisory`, `log_agent_run`, `log_agent_event`) |
| `mcp_server.py` | stdio MCP server (`python -m agentic_ai.mcp_server`) wrapping the 12 read tools + `save_advisory` |
| `mcp_client.py` | the graph's MCP client (`AGENT_TOOLS=mcp`, default) with a direct-call fallback per call |
| `drill.py` | C4.3 fault drill "LLM down" for core's tools/fault_drill.py: `python -m agentic_ai.drill [--alert-id ID] [--no-key]` or `llm_down(tools, alert_id)` |
| `eval/` | A3.1 seeded-error catch rate, A4.2 evaluation on replayed alerts, A5.1 ablation (tables in `eval/`) |
| `advisory.py` | facts in report precision, the template writer, writer prompt + requirements |
| `checks.py` | deterministic reviewer checks (`CHECKS`), Unicode -> ASCII normalisation |
| `rules.py` | official category helpers, direction and disagreement rules, thresholds, forbidden phrases |
| `llm.py` | `LLM_PROVIDER` groq / gemini / ollama / none, timeout, one fallback, never raises |
| `tracing.py` | LangSmith tracing from env vars (off without a key) |
| `run.py` | CLI: one alert by id, or the `alerts.flood` consumer (group `agent`) |
| `seed.py` | puts the May 2019 DRWT2 flood into PostgreSQL (measured record, see below) |
| `schema_agents.sql` | `advisories`, `agent_events`, `agent_runs`, `gauge_impacts`, `past_floods` (also in `storage/schema.sql`) |
| `fixtures/drwt2_2019-05_replay.json` | measured USGS record for 08158700 + 2 neighbours, run through core's windowing and forecaster |
| `fixtures/agent_events_DRWT2.json` | recorded agent_events for the console (regenerate: `python -m tests.agents.record_fixture --write`) |

## Evaluation (A3.1, A4.2, A5.1) and the LLM-down drill (C4.3)

```
python -m agentic_ai.eval.catch_rate                     # seeded errors on template drafts -> eval/reviewer_catch_rate.md
python -m agentic_ai.eval.build_replays --write          # (network) re-fetch the 8 measured USGS floods -> eval/replay_events.json
python -m agentic_ai.eval.evaluate --dsn <scratch db>    # multi / single / template / llm_down on the 14 replayed alerts
python -m agentic_ai.eval.evaluate --report-only         # rebuild eval/agent_eval.md, ablation.md, agent_eval_runs.csv
python -m ml.import_runs agentic_ai/eval/agent_eval_runs.csv   # rows into evaluation_runs (run_label agent_eval:*)
python -m agentic_ai.drill [--alert-id ID] [--no-key]   # LLM down: PASS/FAIL line, exit 0/1
```
Use a scratch database for `evaluate` (it loads 8 replayed floods, mode SIMULATION, and writes agent rows). Live runs
are spaced 65 s (`--gap`) for the Groq free tier; 28 live runs take about 30 min. Results: `eval/agent_eval.md`,
`eval/ablation.md`; raw per-run records `eval/runs.jsonl`. Current tables: replay set rebuilt through core 6b105c1
(alerts raised by the model's probability, `alert_by_model`): 12 floods, 9 alerts. The earlier tables (stage-forecast
rule, 8 floods, 14 alerts) are kept in `eval/before_alert_by_model/` with run labels `agent_eval_before_alert_by_model:*`.

| live Groq, replay only | multi-agent now | single agent now | multi before | single before |
|---|---|---|---|---|
| alerts | 9 | 9 | 14 | 14 |
| pass all deterministic checks | 9/9 | 6/9 | 14/14 | 7/14 |
| saved on attempt 1 | 8/9 | - | 14/14 | - |
| numbers in the evidence | 142/142 | 70/70 | 176/176 | 124/125 |
| UNCERTAIN where needed | 8 of 8 | 8 of 8 | 2 of 2 | 0 of 2 |
| tokens median / time median | 3376 / 2.7 s | 2731 / 1.86 s | 3056 / 2.38 s | 2514 / 1.67 s |

What changed between the two: the alert rule (model probability instead of stage forecast; alerts MINOR only, 8 of 9
with the stage forecast below MINOR, so UNCERTAIN), 4 more floods, and the single agent's rules gained the
model-probability rule.

## Set up and run (Windows laptop, PowerShell, repo folder)

```
..\spaenv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -r requirements-agents.txt
cd docker; docker compose up -d; cd ..
python -m agentic_ai.seed                    # measured May 2019 record; also removes the old illustrative seed rows
python -m agentic_ai.seed --drop-neighbour-alerts   # once: the 5 neighbour-gauge alerts older seeds wrote (windows stay)
python -m agentic_ai.run --alert-id '08158700|major|2019-05-04T01:00:00Z'      # --force re-runs
python -m agentic_ai.run                     # or: consume alerts.flood (group agent), Ctrl-C to stop
python -m pytest -q tests/agents             # always LLM_PROVIDER=none, no network
```

`.env` (git-ignored; never commit keys):

| Variable | |
|---|---|
| `LLM_PROVIDER` | `groq` (default model `GROQ_MODEL=openai/gpt-oss-120b`, key `GROQ_API_KEY`), `gemini` (`GEMINI_API_KEY` from Google AI Studio, starts with `AIza`; `GEMINI_MODEL` default gemini-3.5-flash-lite; gemini-2.5-flash is closed to new keys, gemini-3.5-flash timed out at 30 s once), `ollama` (local `qwen2.5:7b`, `OLLAMA_HOST`), `none` (template only) |
| `GROQ_REASONING_EFFORT` | `low` (default) / `medium` / `high`, sent only for `openai/gpt-oss-*`. Measured 31 Oct, 5 runs each: low 2.0-3.1 s, 3.0-3.3k tokens, 5/5 on attempt 1; not set (Groq default, round 4) 4.2-11.0 s, 4.0-8.4k tokens, 4/5 on attempt 1 |
| Groq 429 | waits once for the reset hint (`retry-after`, else `x-ratelimit-reset-*`) when it is at most 20 s, shown as a progress line, then retries Groq; a longer hint goes straight to `LLM_FALLBACK` |
| `LLM_FORCE_DOWN=1` | every LLM call fails at once (fault drill). Any LLM failure (no key, provider error, timeout, forced) keeps template v1: no agents draft, no judge call, outcome `v1_kept` with reason "LLM unavailable: ..." (since 1 Nov; before, the template text was saved again as v2) |
| `LLM_FALLBACK` | `gemini` / `groq` / `none`: a failed call is retried once there, then the template text is used |
| `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` | optional tracing; the CLI prints the trace link, and every `agent_runs` row of the run carries `trace_run_id` and `trace_url` (console link) |
| `PG_DSN` | PostgreSQL (default the docker one) |
| `AGENT_TOOLS` | `mcp` (default: the agents' tool calls go through the stdio MCP server, started once per process, about 1 s) or `direct` (plain Python calls). Any MCP failure falls back to the direct call for that call; each tool_call event says `via` |

The seed: `python -m agentic_ai.seed` writes the measured USGS gauge-height record for DRWT2 (Onion Creek near
Driftwood, 08158700), the downstream gauge 08159000 and nearby 08158840, 2019-05-02..05, as core's own windowing
and rise-rate forecaster turn it into rows (mode SIMULATION). It is the same data a core replay writes, so the
order does not matter. Its alert 08158700|major|2019-05-04T01:00:00Z: gauge 8.16 ft (already above MODERATE 7.0 ft),
rising 5.5 ft/h, forecast 24.72 ft in 3 h (MAJOR is 13.0 ft), no probability (the rise-rate baseline gives none), no
radar rain joined. Rebuild the fixture with `python -m tests.agents.make_replay_fixture --write` (without `--write` it only builds a temp copy) (needs network and
`requirements.txt`). `seed.build()` is a separate ILLUSTRATIVE shape (made-up rain and probabilities) used only by
in-memory tests; it is never written to a database that holds replay rows.

Tests with a real PostgreSQL: set `AGENTS_TEST_DSN` (any scratch database); each test session uses a throwaway
schema and drops it. Without it those tests skip.
