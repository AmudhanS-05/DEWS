# Slice plan S1 to S5 (detailed; rev 1, 30 Oct 2026)

How to use this file. Each slice is one demoable story cut through every workstream. Three Claude Code sessions build
in parallel (core, ML + India, agents); you do the few manual steps marked **YOU**; at the end of each slice the core
session merges the branches, runs `python -m tools.verify_slice S<n>`, and the demo in `docs/DEMO.md` gains one more
scene. Handoffs: `docs/handoffs/CORE.md`, `docs/handoffs/ML_INDIA.md`, `docs/handoffs/AGENTS.md`. Interfaces:
`docs/CONTRACTS.md`. Setup: `docs/CLAUDE_CODE_SETUP.md`.

Definition of done for every task: code + unit tests (`pytest -q` green) + an automated check where possible + one
line in `BREADCRUMBS.md` tagged `[core]`, `[ml]` or `[agents]` + commit on the workstream's branch.

## Visual components (built across the slices)

| Component | What it shows | Slice |
|---|---|---|
| Console: Pipeline flow (built) | every producer, topic, consumer group, broker; messages flowing | done; extended with new nodes each slice |
| Console: Map (built) | each gauge coloured by its official level, live or replay | done; rain rings S2 |
| Console: Readings & alerts | windows, alerts, advisory drafts with Approve / Edit / Reject | S1 |
| Console: **Metrics** | live KPI tiles and charts: throughput per topic, consumer lag, per-alert latency waterfall (reading → fetched → window → forecast → alert → draft v1 → draft v2 → approved → delivered), detection metrics, recovery times, agent metrics | S1 latency/throughput, S3 detection, S4 agents, S5 recovery |
| Console: **Agents** | the multi-agent graph drawn like the pipeline: nodes light up while each agent runs, tool calls travel to an MCP tools hub, the reviewer loop pulses on a revision, a disagreement splits amber, the human gate waits; side panel streams the draft as it is written; a timeline scrubber replays any recorded run | S1 static graph, S2 live, S4 full animation |
| Console: **Story mode** | one replayed flood narrated end to end: a marker leaves the gauge on the map, travels the pipeline, the agents and the approval gate, and the latency waterfall fills in step by step | S5 |
| LangSmith (cloud, free Developer plan) | full trace of every agent run (prompts, tool calls, tokens); each advisory links to its trace | S1 optional, S4 linked |
| Power BI (DirectQuery) | page 1 gauge status and alerts, page 2 rain and stage, page 3 evaluation metrics, page 4 pipeline health | S1, S2, S3, S5 |

## S1 (due 2 Nov): one past flood end to end
**Story:** replay the May 2019 Onion Creek (DRWT2) flood → windows → baseline forecast → alert → template advisory
(v1) in seconds → a person approves → email arrives with delivery time → kill a Faust worker mid-replay and watch it
recover. Agents run on the same alert and post v2.

| Workstream | Tasks | Done when |
|---|---|---|
| **Core** | C1.1 Faust verified (3 workers, kill test). C1.2 schema additions (`advisories`, `agent_runs`, `agent_events`) applied with `psql`, `storage/views.sql`. C1.3 `storage/sink.py` (4 topics, upserts, commit after write) + `tools/check_sink.py`. C1.4 `streams/forecaster.py` (rise-rate baseline via `ml/predict.py` stub, episodes, `forecasts.stage` + `alerts.flood`). C1.5 `alerting/template.py` (v1), console approval (Approve / Edit / Reject → `alerts.approved`), `alerting/notifier.py` (SMTP, retry, `delivered_at`). C1.6 console Metrics tab v1 (throughput, lag, latency waterfall) and Agents tab v1 (static graph + last run from `agent_events`). C1.7 `tools/fault_drill.py` (faust, broker, sink, postgres, notifier, producer). C1.8 `tools/verify_slice.py` (S1 checks). C1.9 `scripts/start_demo.ps1` / `stop_demo.ps1`. | `verify_slice S1` PASS; the S1 scene in DEMO.md runs twice in a row |
| **ML + India** | M1.1 pooled dataset builder from cached history using `streams/windowing.py` (hourly windows, stage relative to minor, labels "crosses minor within h"). M1.2 evaluation harness → `evaluation_runs` (per gauge, slow-only, pooled; event metrics, lead time gross/net). M1.3 `ml/predict.py` stub returning the rise-rate baseline (contract-correct, used by core from day one). M1.4 baseline numbers for all gauges. India: I1.1 `india/audit.py` (US side), list of 3 to 5 Indian stations to download. | `predict.py` passes contract tests; baseline rows in `evaluation_runs` |
| **Agents** | A1.1 `requirements-agents.txt`. A1.2 `agentic_ai/tools.py` (contract functions on PostgreSQL), fixtures + `python -m agentic_ai.seed` (inserts the DRWT2 2019 alert so agents never wait for core). A1.3 LangGraph graph: coordinator → evidence ∥ context → writer → reviewer (loop ≤ 2) → `save_advisory` v2; writes `agent_events`. A1.4 `LLM_PROVIDER` = `groq` / `ollama` / `none` (deterministic stubs for tests). A1.5 `python -m agentic_ai.run` (group `agent` on `alerts.flood`, or `--alert-id`). A1.6 LangSmith tracing by env vars. | graph runs on the seeded alert with `LLM_PROVIDER=none` in tests and `groq` live; v2 row in `advisories`; events in `agent_events` |
| **YOU** | Gmail app password → `.env`; Groq API key (free, console.groq.com) → `.env`; optional LangSmith key → `.env`; install Power BI Desktop and follow the core session's guide for page 1; approve the alert during the demo | email received |
| **Paper / slides** | method section and architecture figure; S1 slide; AAI: CWD design slide | |

## S2 (due 6 Nov): the same flood, now with radar rain
**Story:** rain appears over the Onion Creek catchment hours before the river rises; the windows carry rain; the
Context worker cites it; a "heavy rain, flat gauge" case is marked uncertain.

| Workstream | Tasks | Done when |
|---|---|---|
| **Core** | C2.1 `producers/mrms_qpe_producer.py` live + `--replay` for a date range (10 km circle, mean depth) → `rain.mrms` / `replay.rain` (Docker container fallback if GRIB2 decoding fails on Windows). C2.2 Faust 3b: rain joined into windows (`rain_1h/3h/6h`). C2.3 Map rain rings; flow edge rain → Faust goes live. C2.4 Power BI page 2 guide. | `verify_slice S2` PASS: rain rises before stage in the replay |
| **ML + India** | M2.1 MRMS archive download around the 123 slow floods + matched dry periods (download, sum over 10 km, discard). M2.2 model v1 (stage + rain) vs stage-only. I2.1 audit table for US vs India complete (Table 1). I2.2 `india/indiawris_loader.py` + `india/thresholds.csv`. | Table 1; v1 vs v0 rows in `evaluation_runs` |
| **Agents** | A2.1 Context worker uses `get_rain`, `get_neighbours`. A2.2 `agentic_ai/rules.py` disagreement rule + tests (synthetic cases: rain/no rise, rise/no rain). A2.3 Groq live end to end; `agent_events` streamed for the live Agents tab. | uncertain case produced on the test fixture; live view animates a real run |
| **Core (for agents)** | Agents tab v2: live animation from `agent_events` (nodes light, tool chips, reviewer loop, disagreement split). | |
| **YOU** | India-WRIS downloads (3 to 5 stations, 2015 to 2025, one year per file) + warning/danger levels from CWC bulletins; install Ollama and `ollama pull qwen2.5:7b` (offline fallback) | |
| **Paper / slides** | data section; rain-before-stage figure; AAI: agents and tools slides | |

## S3 (due 10 Nov): a real model, scored on held-out years
**Story:** the forecaster switches from the baseline to the trained model; Power BI and the Metrics tab show precision,
recall, F1, false alarms and lead time on held-out years; the reviewer catches a wrong number.

| Workstream | Tasks | Done when |
|---|---|---|
| **Core** | C3.1 forecaster loads the model through `ml/predict.py` (hot-swap, falls back to baseline). C3.2 Metrics tab detection panel; Power BI page 3 guide. | `verify_slice S3` PASS: forecasts carry the model name; on the May 2019 replay the trained model alerts before the minor crossing (moved here from S1: the rise-rate baseline measurably alerts too late, pooled lead time net of feed delay −0.17 h) |
| **ML + India** | M3.1 final split and evaluation (per gauge, slow-only, distinct storms, PR curves). M3.2 model v2 + `ml/MODEL_CARD.md`. M3.3 radius sensitivity 5 / 10 / 25 km. I3.1 Indian data through the pipeline (replay via loader) → failure log (Table 2). | results table; Table 2 |
| **Agents** | A3.1 reviewer: deterministic number/category/wording checks + LLM judgement; corrupted-draft fixtures. A3.2 Ollama fallback path tested (Groq off). A3.3 episodic memory: episode history, "Update n" wording, edits as examples; guard: numbers only from current evidence. | reviewer catches all seeded errors; update wording on a repeat alert |
| **YOU** | review the results table; ask faculty nothing new unless numbers look odd | |
| **Paper / slides** | results section (detection, lead time); AAI: memory and reflection slides | |

## S4 (due 14 Nov): the full agent workflow, animated
**Story:** an alert arrives → v1 template in seconds → the Agents tab animates the coordinator delegating, the two
workers in parallel, the writer streaming, the reviewer sending it back once, then v2 → the officer approves.

| Workstream | Tasks | Done when |
|---|---|---|
| **Core** | C4.1 Agents tab v3: full animation + timeline scrubber + LangSmith link per run. C4.2 Metrics tab agent panel (time per agent, tokens, pass rate, disagreements, v1 → v2 time). C4.3 fault drill "LLM down" (v1 stays, nothing blocked). | `verify_slice S4` PASS |
| **ML + India** | I4.1 step C: degradation experiment on real US data (resolution, delay, gaps, missing thresholds) → curves. | degradation figure |
| **Agents** | A4.1 `agentic_ai/mcp_server.py` (stdio) wrapping the tools; graph uses the MCP client. A4.2 agent evaluation: faithfulness, reviewer catch rate, disagreement outcomes, v1/v2 timing, tokens → `evaluation_runs` notes or `agent_runs` summary. | evaluation table |
| **YOU** | record the AAI video (the core session writes the shot list) | |
| **Paper / slides** | AAI deck complete; India section draft | |

## S5 (due 18 Nov): hardened demo
**Story:** the full Phase II demo, twice, on the presentation laptop, including failures, with offline fallbacks.

| Workstream | Tasks | Done when |
|---|---|---|
| **Core** | C5.1 all fault drills with recovery times; throughput run (38 gauges, 10 years, `--no-sleep`); latency report. C5.2 Story mode. C5.3 Power BI page 4; demo checklist and offline fallback (cached data, offline map, Ollama). | `verify_slice S5` PASS; two clean rehearsals |
| **ML + India** | I5.1 step D: synthetic "ideal India" dataset + replay through the pipeline; final numbers frozen. | synthetic India figure |
| **Agents** | A5.1 ablation: single agent vs multi-agent on the same replayed alerts; final numbers. | ablation table |
| **YOU** | rehearse with the team (who speaks, who clicks) | |
| **Paper / slides** | paper complete; SPA deck | |
