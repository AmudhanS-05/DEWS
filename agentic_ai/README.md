# Agentic AI layer: multi-agent flood advisories (workstream `agents`, AI373TA)

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
| coordinator | loads the alert, drops duplicates and retractions, counts the update number in the episode, plans | get_alert, get_episode_history |
| evidence | the gauge's own windows and forecast, official levels; direction rising / not rising | get_recent_windows, get_forecast, get_official_levels, get_past_floods |
| context | radar rain, upstream/downstream/nearby gauges, NWS impact statements; direction or `unknown` | get_rain, get_neighbours, get_gauge_info, get_impacts |
| reconcile | evidence and context disagree -> advisory marked UNCERTAIN, both signals stated | (pure rules, `rules.py`) |
| writer | LLM draft from the facts (or the deterministic template when there is no LLM or it fails) | `llm.py`, `advisory.py` |
| reviewer | deterministic checks first (`checks.py`), then an LLM judgement on a draft that passed them | `checks.py`, `llm.py` |

Every step writes `agent_events` (drives the console's Agents tab) and `agent_runs`; the result is a row in
`advisories`. Any failure (tool crash, LLM down, reviewer still failing) leaves template v1 in place.

**Numbers come from the alert's own window.** `get_alert` finds the window the alert was raised on
(`tools.anchor_window`): the window ending at `raised_at`, else the first window from the episode start whose
forecast reaches the alert's category while the gauge is still below it (a Kafka replay stamps `raised_at` with
wall-clock time, so "latest window before raised_at" would be the end of the replay). Evidence and context read up
to that window (`as_of`) and use the forecast row with the alert's horizon.

**The advisory text** (template and LLM alike) follows the deterministic checks, each of which is also a numbered
writer instruction with the exact values (a test keeps the two lists equal):
numbers only from the facts; stages and rates to 0.1 ft; the alert's official category and its stage; when the
forecast stage crosses a higher official category, that category and its stage too ("forecast to reach MAJOR
(13.0 ft)", "Alert category: MINOR (5.5 ft)"), never "forecast to reach MINOR"; probability as a whole % or
"not available" when the model gives none; horizon in hours; lead time in hours or "not available"; "rain data not
available" when radar rain is missing; never null/None/NaN/"?"; no Markdown; SIMULATION label on replayed data (and
never on live data); "provisional" for provisional data; UNCERTAIN with both signals when they disagree; no
certainty language; at most 900 characters.

## Files

| File | |
|---|---|
| `graph.py` | the LangGraph graph, event log, `run_alert()` (never raises) |
| `tools.py` | read tools, `anchor_window`, writes (`save_advisory`, `log_agent_run`, `log_agent_event`) |
| `advisory.py` | facts in report precision, the template writer, writer prompt + requirements |
| `checks.py` | deterministic reviewer checks (`CHECKS`), Unicode -> ASCII normalisation |
| `rules.py` | official category helpers, direction and disagreement rules, thresholds, forbidden phrases |
| `llm.py` | `LLM_PROVIDER` groq / gemini / ollama / none, timeout, one fallback, never raises |
| `tracing.py` | LangSmith tracing from env vars (off without a key) |
| `run.py` | CLI: one alert by id, or the `alerts.flood` consumer (group `agent`) |
| `seed.py` | puts the May 2019 DRWT2 flood into PostgreSQL (measured record, see below) |
| `schema_agents.sql` | `advisories`, `agent_events`, `agent_runs`, `gauge_impacts`, `past_floods` (also in `storage/schema.sql`) |
| `fixtures/drwt2_2019-05_replay.json` | measured USGS record for 08158700 + 2 neighbours, run through core's windowing and forecaster |
| `fixtures/agent_events_DRWT2.json` | recorded agent_events for the console (regenerate: `python -m tests.agents.record_fixture`) |

## Set up and run (Windows laptop, PowerShell, repo folder)

```
..\spaenv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -r requirements-agents.txt
cd docker; docker compose up -d; cd ..
python -m agentic_ai.seed                    # measured May 2019 record; also removes the old illustrative seed rows
python -m agentic_ai.run --alert-id '08158700|major|2019-05-04T01:00:00Z'      # --force re-runs
python -m agentic_ai.run                     # or: consume alerts.flood (group agent), Ctrl-C to stop
python -m pytest -q tests/agents             # always LLM_PROVIDER=none, no network
```

`.env` (git-ignored; never commit keys):

| Variable | |
|---|---|
| `LLM_PROVIDER` | `groq` (default model `GROQ_MODEL=openai/gpt-oss-120b`, key `GROQ_API_KEY`), `gemini` (`GEMINI_API_KEY` from Google AI Studio, starts with `AIza`; `GEMINI_MODEL` default gemini-2.5-flash), `ollama` (local `qwen2.5:7b`, `OLLAMA_HOST`), `none` (template only) |
| `LLM_FALLBACK` | `gemini` / `groq` / `none`: a failed call is retried once there, then the template text is used |
| `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` | optional tracing; the CLI prints the trace link |
| `PG_DSN` | PostgreSQL (default the docker one) |

The seed: `python -m agentic_ai.seed` writes the measured USGS gauge-height record for DRWT2 (Onion Creek near
Driftwood, 08158700), the downstream gauge 08159000 and nearby 08158840, 2019-05-02..05, as core's own windowing
and rise-rate forecaster turn it into rows (mode SIMULATION). It is the same data a core replay writes, so the
order does not matter. Its alert 08158700|major|2019-05-04T01:00:00Z: gauge 8.16 ft (already above MODERATE 7.0 ft),
rising 5.5 ft/h, forecast 24.72 ft in 3 h (MAJOR is 13.0 ft), no probability (the rise-rate baseline gives none), no
radar rain joined. Rebuild the fixture with `python -m tests.agents.make_replay_fixture` (needs network and
`requirements.txt`). `seed.build()` is a separate ILLUSTRATIVE shape (made-up rain and probabilities) used only by
in-memory tests; it is never written to a database that holds replay rows.

Tests with a real PostgreSQL: set `AGENTS_TEST_DSN` (any scratch database); each test session uses a throwaway
schema and drops it. Without it those tests skip.
