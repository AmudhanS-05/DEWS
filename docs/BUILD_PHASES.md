# Build plan: phases, schedule and tests (rev 4, 30 Oct 2026)

Project: Real-Time Flood & Flash-Flood Forecasting and Alerting using Apache Kafka.
Courses: AI372IA Stream Processing and Analytics (SPA), AI373TA Agentic AI (AAI).
Step-by-step commands with what the console shows: `docs/RUNBOOK.md`. Parallel ML track: `docs/ML_HANDOFF.md`.
Rule: a phase is done when its test passes; record it in `BREADCRUMBS.md` (one line) and numbers in `PROJECT_LOG.md`.

## Dates that drive the plan

| When | What | Source |
|---|---|---|
| **19 to 26 Nov 2026** | **SPA Phase II: full demonstration + technical paper submitted** | SPA circular |
| 14 to 18 Dec 2026 | SPA Lab CIE (lab programs) + updated paper | SPA circular |
| ask faculty | AAI experiential learning: case study (10), program-specific requirements (10), video seminar / presentation / demo (20) | AAI syllabus rubric |

## Decisions confirmed with faculty (30 Oct)

1. **Pooled evaluation** across gauges is accepted (caveats and mitigations in Phase 8).
2. **Faust** is accepted for the stream-processing layer. Faust is not a wrapper around Kafka Streams (a Java
   library): it re-implements the same ideas in Python (stream processors = agents, state stores = tables backed by
   changelog topics, windows, co-partitioning, consumer groups) and talks to Kafka through the `aiokafka` client.
3. **Agent layer:** a brief mention in SPA reviews; the full multi-agent system is the AAI project.
4. **Phase II = a complete, running demo of every phase.**
5. **Wording:** "forecast of crossing an official flood category", not "predicts a flood".
6. **Dashboard:** the console (operations, live pipeline) plus Power BI (analytics, metrics) is accepted, provided
   metrics update live.

## Status board

| Phase | What | Status |
|---|---|---|
| 0 | Gate checks, EDA | **done** (38 gauges, 213 floods, pooled PASS) |
| 1 | 3-broker Kafka, 8 topics, PostgreSQL | **done** (verified 29 Oct: RF 3, ISR 2, broker-stop test) |
| 2 | USGS live producer, replay producer | **done** (dedupe, heartbeats, backfill after 34 h outage verified 30 Oct) |
| 2b | MRMS rainfall producer | to build (week 1) |
| 3 | Faust event-time windows | built; first run crashed on an aiokafka 0.14 incompatibility, fixed by pinning `aiokafka==0.12.0`; to verify |
| 4 | Forecaster (ML) | offline baselines done; ML model (parallel track) + streaming consumer to build |
| 5 | PostgreSQL sink + latency trace | to build (week 1) |
| 6a | Alert pipeline: approval, notifier (no LLM) | to build (week 2) |
| 6b | Multi-agent advisory system (AAI) | to build (weeks 2 to 3) |
| 7 | Console (built: flow, map, readings) + Power BI | Power BI report to build (week 2) |
| 8 | Fault injection + evaluation | to build (week 3) |
| 9 | Paper, demo script, rehearsal | weeks 3 to 4 |

## Schedule

Two tracks run in parallel. The **SPA core** must be complete and demo-ready by 18 Nov without depending on the
agents or the ML track: the forecaster falls back to the rise-rate baseline and the advisory to a template.

| Week | SPA core (this chat) | ML track (separate chat) | AAI track (separate chat for slides/docs; code here) |
|---|---|---|---|
| **W1** 30 Oct to 5 Nov | Phase 3 verified (3 Faust workers, kill test); Phase 5 sink + latency trace; Phase 2b MRMS live producer; `forecasts.stage` topic (decision D1) | stage-only model on cached history; split, metrics, model card | design deck: problem, CWD architecture, agent roles, tools, memory (this file, section 6b) |
| **W2** 6 to 12 Nov | Phase 4 streaming forecaster (baseline first, ML model when ready); Phase 6a approval in console + email notifier; Power BI report; MRMS rain into windows (3b) | add rainfall features; compare with stage-only; `ml/predict.py` contract | Phase 6b: MCP tool server, LangGraph graph, coordinator + workers, reviewer loop |
| **W3** 13 to 18 Nov | Phase 8 fault drills + evaluation runs; paper results section; demo script and two full rehearsals | final model, evaluation rows | agent evaluation (faithfulness, reviewer catch rate, single vs multi-agent), video recording |
| **19 to 26 Nov** | **Phase II demo + paper** | | AAI presentation when scheduled |
| 27 Nov to 13 Dec | paper update; Lab CIE programs 1 to 10 | | |

## Phase details

### Phase 2b: MRMS rainfall producer (W1)
- `producers/mrms_qpe_producer.py`: every 2 to 5 min, find the newest file of the live product in the public
  `noaa-mrms-pds` bucket (HTTPS), decode the GRIB2 grid, sum rainfall over a **circle of radius R around each gauge**
  (decided: start simple; R = 10 km first, tune once; state it in the paper), publish one record per gauge to
  `rain.mrms` keyed by `site_no`, with product name and file time.
- Product: a fast one for live (radar-only or Pass 1, about 3 to 16 min late); the gauge-corrected Pass 2 (about
  58 min late) for evaluation.
- Replay of rainfall into `replay.rain` for the demo flood.
- **Risk:** `cfgrib`/`ecCodes` on Windows. Fallback: run this producer as a Linux container in `docker-compose.yml`.
- **Test:** records for all 38 gauges every cycle; rain totals for a known storm match the notebook 03 values.

### Phase 3: Faust windows (W1)
- Fix first: `pip install "aiokafka==0.12.0"` (now pinned in `requirements.txt`). aiokafka 0.13+ changed its request
  API and faust-streaming 0.11.3 still sends the old request type (`MetadataRequest_v1 ... has no attribute 'prepare'`).
- 3 workers, one group (`flood-windowing`), 3 partitions each for live and replay.
- 3b (W2): join `rain.mrms` / `replay.rain` into the windows (rain_1h, rain_3h, rain_6h); the `rain.mrms → Faust`
  edge stops being "planned".
- **Test:** RUNBOOK steps 4 to 8: one aggregate per gauge per hour, zero-filled gaps, revisions flagged; kill a worker
  (2/3), restart; `tools.check_windows` PASS (no window emitted twice).

### Phase 4: forecaster (W2)
- `ml/forecaster_consumer.py`, group `forecaster`: reads `aggregates.window`, keeps each gauge's recent windows,
  calls `ml/predict.py` (ML model when the ML track delivers it; rise-rate baseline until then), and publishes:
  - every forecast to **`forecasts.stage`** (decision D1) → sink → `risk_scores` table (Power BI risk lines);
  - an alert to `alerts.flood` only when the forecast crosses an official category within the horizon.
- One alert per gauge per category episode (`alert_id = site|category|episode start`); an upgrade raises a new alert;
  a forecast that falls back below the level for 2 windows closes the episode (retraction recorded, not emailed).
- Every alert carries the timestamps it inherited (reading, fetched, window close, forecast) for the latency trace.
- **Test:** replaying the May 2019 DRWT2 flood raises an alert before the minor crossing; no duplicate alerts.

### Phase 5: PostgreSQL sink + latency trace (W1)
- `storage/sink.py`, group `postgres-sink`: reads `aggregates.window`, `forecasts.stage`, `alerts.flood`; idempotent
  upserts on the table primary keys; commits Kafka offsets only after the database commit (at-least-once delivery
  made effectively-once by the upserts).
- Writes `latency_trace` rows; `storage/views.sql` adds views for Power BI (latest per gauge, alert latency,
  throughput per minute).
- Raw readings: `gauge_readings` is filled by the sink from `gauge.usgs` as well (cheap; Power BI trend lines need it).
  The diagram's "reads 2 topics" becomes "reads 4 topics"; update the slide.
- **Test:** after a replay, row counts match topic counts; kill the sink mid-replay, restart: no gaps, no duplicates.

### Phase 6a: approval and notifier, no LLM (W2)
- Draft advisory from a fixed template (gauge, current stage, forecast, probability, horizon, official category,
  SIMULATION label) written to `alerts` with status `draft`. This path always works, with or without the agents.
- Console **Readings & alerts** tab gets Approve / Edit / Reject. Approve publishes to `alerts.approved` and sets
  status `approved`. Nothing is sent without it.
- `alerting/notifier.py`, group `notifier`: reads `alerts.approved`, sends email over SMTP (team mailbox; app password
  in a local `.env`), writes `delivered_at` and the log line. If SMTP is down, it retries and the backlog waits in
  Kafka; the delivery delay is measured.
- **Test:** replayed flood → draft → approve → email arrives; `delivered_at − alert_time` recorded; rejecting sends
  nothing.

### Phase 6b: multi-agent advisory system (AAI; W2 to W3)

**Purpose.** Turn a forecaster alert (numbers) into a checked, plain-language advisory with evidence, for a duty
officer to approve. The agents draft and check; they never send. If any agent fails or the LLM is unreachable, the
6a template is used, so the SPA pipeline is never blocked.

**Pattern: Coordinator, Worker, Delegator (CWD)** (AAI Units III and IV), as a **hybrid architecture** (Unit II):
reactive (triggered by each Kafka alert) plus deliberative (the coordinator plans which workers to run).
Implemented in **LangGraph** (Unit III) as a state graph with a human-in-the-loop interrupt.

```
alerts.flood ─▶ Coordinator (triage + plan + delegate)
                   ├─▶ Evidence worker ──┐  (parallel)
                   └─▶ Context worker  ──┤
                                         ▼
                        Advisory writer ─▶ Reviewer ──fail (max 2)──▶ back to writer
                                                    └─pass──▶ draft in PostgreSQL ─▶ human approval (console)
                                                                                   ─▶ alerts.approved ─▶ notifier
```

| Agent | Role | Tools (read-only, via MCP) | Output |
|---|---|---|---|
| **Coordinator** (delegator) | Consumes `alerts.flood` (group `agent`). Triage: drop duplicates and downgrades, merge alerts for the same storm, mark major-category alerts urgent. Plans which workers to call and assembles the final package. | `get_open_alerts`, `get_alert(alert_id)` | plan + final advisory package |
| **Evidence worker** | Hydrology analyst: what is happening at this gauge and how sure we are. | `get_recent_windows(site, hours)`, `get_forecast(site)`, `get_official_levels(site)`, `get_rain(site)`, `get_past_floods(site)` | evidence JSON: trend, rise rate, rain, forecast range, similar past floods |
| **Context worker** | Geography and impacts: who is affected and what the official level means there. | `get_gauge_info(site)` (county, location), `get_impacts(site, level)` (NWS impact statements per stage, from NWPS), `get_neighbours(site)` (upstream/downstream gauge status) | context JSON |
| **Advisory writer** | Writes the advisory in a fixed schema: headline, gauge, current stage, forecast and probability, lead time, official category, impacts at that level, uncertainty, provisional-data note, SIMULATION label. | none (uses evidence and context only) | draft advisory |
| **Reviewer** (critic) | Checks the draft before a person sees it: numbers match the evidence, category matches the forecaster, no certainty language ("will flood"), probability and horizon stated, SIMULATION marked for replay, length limit. Deterministic checks first, LLM judgement second. Sends it back with reasons (reflection, AAI CO2). | `check_numbers(draft, evidence)` | pass, or fail with reasons |
| Human approval (not an agent) | Approve, edit or reject in the console. Edits and decisions are stored. | | `alerts.approved` |
| Notifier (service, not an agent) | Sends the approved advisory, logs delivery time. | | email + log |

**Tools through MCP** (AAI Unit V): `agentic_ai/mcp_server.py` exposes the read-only PostgreSQL and NWPS tools as a
Model Context Protocol server (stdio transport). The LangGraph agents are its client. This shows the MCP
architecture (host, client, server, transport) and keeps tools independent of the agent framework.

**Memory** (AAI Unit IV):
- short-term (working) memory: the LangGraph state for one alert run;
- long-term (knowledge base): gauge metadata, official levels, NWS impact statements, the 213 past floods;
- episodic memory: past advisories, reviewer verdicts and human edits for the same gauge, retrieved as examples
  ("last time at DRWT2 the officer removed ...").

**Sequential and parallel processing** (Unit IV): evidence and context workers run in parallel; writer and reviewer
in sequence.

**LLM:** model-agnostic through LangChain chat models (decision D2: an API model if a key is available, otherwise a
local model through Ollama). Every call has a timeout; on failure the template advisory is used and marked
`auto-template`.

**Storage:** new table `agent_runs` (run_id, alert_id, agent, input summary, output, tool calls, start, end, tokens,
verdict) and `advisories` (draft text, revisions, reviewer reasons, human edit). Schema change applied by hand with
`psql` (no volume wipe).

**Evaluation (AAI):**
1. Faithfulness: share of advisories whose numbers and category match the evidence (automatic check).
2. Reviewer catch rate on deliberately corrupted drafts (wrong number, missing SIMULATION label, "will flood").
3. Human approval rate and edit size on replayed floods.
4. Time and tokens per agent and per advisory; added latency on top of the SPA alert time.
5. Ablation: single agent vs the multi-agent system on the same replayed alerts.

**For SPA slides:** one sentence and the diagram's bottom row: "consumer group `agent` reads `alerts.flood`; nothing
is sent without human approval".

### Phase 7: console + Power BI (W2)
- Console (built): Pipeline flow, Map (live and replay), Readings & alerts (approval buttons added in 6a).
- **Power BI Desktop** runs separately, on the same PostgreSQL, in DirectQuery mode. Live updates: turn on
  **automatic page refresh** for the report pages (Desktop allows short intervals for DirectQuery; verify on your
  version). Pages: gauge status (table with conditional colours by official category), stage and forecast per gauge
  with official level lines, alerts (draft, approved, delivered, delivery time), evaluation metrics
  (`evaluation_runs`), pipeline health (latency percentiles, throughput).
- Combining them: embedding Power BI inside the console would need Power BI Service, a gateway and a licence; not
  worth it. Show them side by side; both read the same tables, so the numbers agree.
- **Test:** a replayed reading appears in Power BI within one refresh interval.

### Phase 8: fault injection and evaluation (W3)
- `tools/fault_drill.py` runs each scenario, stamps start and recovery, and writes `recovery_time_s` to
  `evaluation_runs`: broker stop; Faust worker kill; sink kill mid-replay; PostgreSQL restart; notifier down with
  approvals waiting; USGS unreachable (producer backfill); producer crash and restart. The console's status log shows
  each one live.
- Throughput: replay 10 years of all 38 gauges with `--no-sleep`; messages per second through each stage.
- Detection metrics on held-out years (time split with flood years on both sides): precision, recall, F1, false-alarm
  rate, lead time gross and net of the feed delay, detection latency, alert delivery time.
- **Pooled evaluation caveats** (state them in the paper):
  1. Fast urban gauges dominate the event count (AHOT2 has 38 of 213 floods); weight events per gauge and report
     per-gauge and slow-only results.
  2. Gauges have different scales and datums; use stage relative to each gauge's minor stage and normalised rise rate.
  3. One storm floods several gauges at once, so events are correlated; the time split prevents leakage, and the paper
     also reports the number of distinct storms.
  4. A time split does not show generalisation to unseen gauges; add a leave-one-gauge-out test if time allows.
  5. Floods are 0.05 to 0.3% of readings; use event-based metrics and precision-recall curves, never accuracy.
- Live and replay results are reported separately, never merged.

### Phase 9: paper and demo (W3 to W4)
- Technical paper: the Claude Doc draft, results filled from `evaluation_runs`.
- Demo script (3 to 5 min): live map and flow → replay the May 2019 flood → windows, forecast, alert, agent advisory,
  approval, email with delivery time → kill a Faust worker and a broker, show recovery → Power BI metrics.
- Two full rehearsals on the presentation laptop and network; offline fallback (cached data, offline map view).

## Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Add topic `forecasts.stage` (forecaster → sink, every forecast) | Yes. Diagram: 9 topics; sink reads 4 topics |
| D2 | LLM for the agents | API model if a key is available; else Ollama locally |
| D3 | MRMS radius and live product | R = 10 km, radar-only or Pass 1 live, Pass 2 for evaluation |
| D4 | Rainfall history for training | MRMS for windows around the 123 slow floods plus matched dry periods (download, sum, discard); Open-Meteo hourly as fallback |
| D5 | Email account and recipients for the notifier | team mailbox; faculty in the demo |
| D6 | Test years | ML track proposes; must contain flood years (e.g. 2024 to 2025) |

## Plan B (if Faust stays brittle)
Replace the Faust app with a plain `confluent-kafka` consumer group running the same `streams/windowing.py` logic
(already pure and unit-tested), with window state in a compacted changelog topic. Keeps every SPA requirement; costs
about two days. Decide by 4 Nov if Phase 3 is not passing.
