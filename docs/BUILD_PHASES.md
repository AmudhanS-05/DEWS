# Build plan: phases, schedule and tests (rev 5, 30 Oct 2026)

Project: Real-Time Flood & Flash-Flood Forecasting and Alerting using Apache Kafka.
Courses: AI372IA Stream Processing and Analytics (SPA), AI373TA Agentic AI (AAI).
Step-by-step commands with what the console shows: `docs/RUNBOOK.md`. Parallel ML track: `docs/ML_HANDOFF.md`.
India data study (paper section): `docs/INDIA_STUDY.md`.
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
| 2b | MRMS rainfall producer | to build (31 Oct) |
| 3 | Faust event-time windows | built; first run crashed on an aiokafka 0.14 incompatibility, fixed by pinning `aiokafka==0.12.0`; to verify |
| 4 | Forecaster (ML) | offline baselines done; streaming consumer with baseline (30 Oct); ML model swap-in (ML track) |
| 5 | PostgreSQL sink + latency trace | to build (30 Oct) |
| 6a | Alert pipeline: approval, notifier (no LLM) | to build (31 Oct) |
| 6b | Multi-agent advisory system (AAI) | 3 to 18 Nov |
| 7 | Console (built: flow, map, readings) + Power BI | Power BI report (1 Nov) |
| 8 | Fault injection + evaluation | drills by 2 Nov; evaluation numbers 10 to 18 Nov |
| 9 | Paper, demo script, rehearsal | 10 to 18 Nov |
| India | India data study (paper section) | 3 to 18 Nov, `docs/INDIA_STUDY.md` |

## Schedule

Two tracks run in parallel. The **SPA core** (every phase working end to end on replay, baseline forecaster, template
advisory, email) is frozen on **2 Nov**. Everything after that adds quality (ML model, rainfall in windows, agents,
India study, evaluation numbers) without the demo depending on it.

| Day | You (laptop) | This chat (code) |
|---|---|---|
| **Fri 30 Oct** | `pip install "aiokafka==0.12.0"`; restart console; RUNBOOK steps 4 to 8 (Faust, kill test); `docker compose up -d --force-recreate topic-init` (adds `forecasts.stage`) | Phase 5 sink + latency trace; Phase 4 streaming forecaster (rise-rate baseline) → `forecasts.stage` + `alerts.flood` |
| **Sat 31 Oct** | replay the May 2019 flood: windows → forecasts → alert → rows in PostgreSQL; sink kill test | Phase 6a: approve / reject in the console, template advisory, email notifier; Phase 2b MRMS producer |
| **Sun 1 Nov** | email arrives with delivery time; MRMS producer runs; Power BI report on the views | `tools/fault_drill.py`; `storage/views.sql`; latency report |
| **Mon 2 Nov** | full core demo on replay twice; fault drills; **core frozen** | fixes only |
| 3 to 9 Nov | | 3b rain into windows; ML model swap-in; Phase 6b agents (with Ollama); India study steps A and B |
| 10 to 18 Nov | evaluation runs; paper; rehearsals | India study C and D (ML chat); agent evaluation; paper results |
| **19 to 26 Nov** | **Phase II demo + paper** | |

Risk: the MRMS producer depends on GRIB2 decoding on Windows. If it slips past 1 Nov, the core is frozen without
rainfall (stage-only baseline) and rain joins in the 3 to 9 Nov block.

## Vertical slices (from now to 18 Nov)

Every slice is one demoable story, cut through all tracks at once: pipeline, ML, agents, India study, console,
Power BI and paper/slides. Each slice ends with a short demo on the laptop, one paragraph in the paper and one slide.

| Slice | Story | Core pipeline | ML | Agents (AAI) | India | Paper / slides |
|---|---|---|---|---|---|---|
| **S1** (by 2 Nov) | One past flood end to end: the May 2019 Onion Creek (DRWT2) replay raises an alert, a person approves it, an email arrives; a Faust worker is killed during it and recovers | sink, forecaster (baseline), approval, notifier, Power BI page 1, fault drill | stage-only baseline numbers for DRWT2 in `evaluation_runs` | LangGraph skeleton: coordinator + workers reading PostgreSQL, template writer; runs on the S1 alert | step A audit script started | method section; S1 slide |
| **S2** (by 6 Nov) | The same flood with rainfall: radar rain appears before the river rises | MRMS producer + replay rain, rain in windows | rain features, model v1 | Context worker uses rain; disagreement rule | step A table done | data section; rain figure |
| **S3** (by 10 Nov) | A real model instead of the baseline, scored on held-out years | ML model in the forecaster; Power BI evaluation page | model v2, evaluation rows, model card | reviewer checks numbers; Ollama in place | step B: pipeline on Indian data, failure log | results table |
| **S4** (by 14 Nov) | Full agent workflow: reactive draft in seconds, agent version later, uncertain cases, update wording | MCP server; advisories table | radius sensitivity (5/10/25 km) | episodic memory, update wording; agent evaluation | step C degradation curves | AAI deck and video; India section |
| **S5** (by 18 Nov) | Hardened demo: every fault drill, throughput, latency report, two rehearsals | all drills, throughput run | final numbers | ablation (single vs multi-agent) | step D synthetic India | paper complete; SPA deck |

## Who works where

| Workstream | Where | Owns (writes only here) |
|---|---|---|
| Core pipeline + integration | this chat, or a Claude Code session on the laptop | `common/`, `producers/`, `streams/`, `storage/`, `alerting/`, `dashboard/`, `docker/`, `tools/`, `tests/` (its own) |
| ML + India steps C and D | ML chat / Claude Code session | `ml/`, `india/`, `notebooks/`, `requirements-ml.txt` |
| Agents (AAI) | AAI chat / Claude Code session | `agentic_ai/`, `requirements-agents.txt` |
| Paper and slides (SPA, AAI) | claude.ai chats (no code) | the paper doc, decks in `presentations/` |

Rules: the interfaces between workstreams are fixed in `docs/CONTRACTS.md` (change them only with a breadcrumb line);
each workstream commits only its own folders; each adds its lines to `BREADCRUMBS.md` tagged `[core]`, `[ml]`,
`[agents]` or `[paper]`; integration and the slice demo happen in the core workstream. `CLAUDE.md` gives every
session the same rules.

## Phase details

### Phase 2b: MRMS rainfall producer (W1)
- `producers/mrms_qpe_producer.py`: every 2 to 5 min, find the newest file of the live product in the public
  `noaa-mrms-pds` bucket (HTTPS), decode the GRIB2 grid, sum rainfall over a **circle of radius R around each gauge**
  (decided: R = 10 km, mean depth over about 314 one-km cells; state it in the paper), publish one record per gauge to
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
officer to approve. The agents draft and check; they never send.

**Two layers (decided 30 Oct, in the AAI deck).** A **reactive layer** posts the 6a template advisory as a draft the
moment an alert arrives (seconds, no LLM). The **deliberative layer** (the agents) then works on it and replaces the
draft with version 2 when its reviewer passes. The officer sees "agent review pending" on version 1. If the officer
approves version 1 first (timeliness wins), the agents' version is attached as a suggested update and is not sent
automatically. If an agent or the LLM fails, version 1 simply stays. The SPA pipeline is never blocked.

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
| **Evidence worker** | The gauge itself: what its own readings and forecast say. | `get_recent_windows(site, hours)`, `get_forecast(site)`, `get_official_levels(site)`, `get_past_floods(site)` | evidence JSON: stage trend, rise rate, forecast range, similar past floods |
| **Context worker** | The surroundings: radar rainfall over the catchment, upstream and downstream gauges, and what the official level means there. | `get_rain(site)`, `get_neighbours(site)` (upstream/downstream gauge status), `get_gauge_info(site)` (county, location), `get_impacts(site, level)` (NWS impact statements per stage, from NWPS) | context JSON: rain signal, neighbour signal, impacts |
| **Advisory writer** | Writes the advisory in a fixed schema: headline, gauge, current stage, forecast and probability, lead time, official category, impacts at that level, uncertainty, provisional-data note, SIMULATION label. | none (uses evidence and context only) | draft advisory |
| **Reviewer** (critic) | Checks the draft before a person sees it: numbers match the evidence, category matches the forecaster, no certainty language ("will flood"), probability and horizon stated, SIMULATION marked for replay, length limit. Deterministic checks first, LLM judgement second. Sends it back with reasons (reflection, AAI CO2). | `check_numbers(draft, evidence)` | pass, or fail with reasons |
| Human approval (not an agent) | Approve, edit or reject in the console. Edits and decisions are stored. | | `alerts.approved` |
| Notifier (service, not an agent) | Sends the approved advisory, logs delivery time. | | email + log |

**When the workers disagree (conflict resolution, AAI Unit III).** Each worker reports a direction: rising or not
rising. Evidence says "rising" when the rise rate or the forecast crosses its threshold; Context says "rising" when
catchment rain in the last 3 h or an upstream gauge's rise passes its threshold (thresholds set from the EDA, written
in `agentic_ai/rules.py`). If the two disagree (heavy radar rain but a flat gauge, or a rising gauge with no rain and
no upstream rise, which can be a sensor fault), the coordinator does not let one side win silently: the advisory is
marked **uncertain**, states both signals, and asks for a re-check at the next window. The disagreement and the
outcome are logged in `agent_runs`, so the paper can count how often each case happened and which side was right.

**Tools through MCP** (AAI Unit V): `agentic_ai/mcp_server.py` exposes the read-only PostgreSQL and NWPS tools as a
Model Context Protocol server (stdio transport). The LangGraph agents are its client. This shows the MCP
architecture (host, client, server, transport) and keeps tools independent of the agent framework.

**Memory** (AAI Unit IV):
- short-term (working) memory: the LangGraph state for one alert run;
- long-term (knowledge base): gauge metadata, official levels, NWS impact statements, the 213 past floods;
- episodic memory: past advisories, reviewer verdicts and human edits for the same gauge. Two uses: (1) a repeat
  alert in the same flood episode is written as an **update** ("Update 2: Onion Creek is now forecast to reach
  moderate stage ..."), not a fresh alarm; (2) the officer's edits are retrieved as examples of preferred wording.
  Guard: numbers are never copied from memory; the reviewer checks every number against the current evidence only.

**Sequential and parallel processing** (Unit IV): evidence and context workers run in parallel; writer and reviewer
in sequence.

**LLM:** model-agnostic through LangChain chat models. Decided (D2): **Ollama on the laptop** (free, no account,
offline). Every call has a timeout; on failure version 1 (template) stays.

**Storage:** new table `advisories` (see `docs/CONTRACTS.md`) and `agent_runs` (run_id, alert_id, agent, input summary, output, tool calls, start, end, tokens,
verdict) and `advisories` (draft text, revisions, reviewer reasons, human edit). Schema change applied by hand with
`psql` (no volume wipe).

**Evaluation (AAI):**
1. Faithfulness: share of advisories whose numbers and category match the evidence (automatic check).
2. Reviewer catch rate on deliberately corrupted drafts (wrong number, missing SIMULATION label, "will flood").
3. Human approval rate and edit size on replayed floods.
4. Time and tokens per agent and per advisory; added latency on top of the SPA alert time.
5. Ablation: single agent vs the multi-agent system on the same replayed alerts.
6. Disagreement cases: how often, and whether "uncertain" was the right call in hindsight.
7. Reactive vs deliberative timing: time to version 1 (template) and to version 2 (agents).

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
| D1 | Topic `forecasts.stage` (forecaster → sink, every forecast) | **decided (30 Oct): yes.** 9 topics; sink reads 4 |
| D2 | LLM for the agents (only the Phase 6b agents need one) | **decided: free and simple → Ollama on the laptop** (no account, works offline on demo day); `qwen2.5:7b` with 16 GB RAM, `llama3.2:3b` with 8 GB |
| D3 | MRMS radius and live product | **decided: R = 10 km** (mean rainfall depth over the circle); radar-only or Pass 1 live, Pass 2 for evaluation; test 5 and 25 km as a sensitivity in the ML track |
| D4 | Rainfall history for training | **decided:** MRMS only around the 123 slow floods plus matched dry periods (download, sum, discard) |
| D5 | Notifier email | **decided:** team address in the local `.env` (`NOTIFY_TO`), more recipients later |
| D6 | Test years | ML track proposes; must contain flood years (e.g. 2024 to 2025) |

## Plan B (if Faust stays brittle)
Replace the Faust app with a plain `confluent-kafka` consumer group running the same `streams/windowing.py` logic
(already pure and unit-tested), with window state in a compacted changelog topic. Keeps every SPA requirement; costs
about two days. Decide by 31 Oct if Phase 3 is not passing.

## Appendix: commands for the finished phases

One-time setup (Windows, PowerShell, repo root):
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1          # the team uses ..\spaenv instead
python -m pip install -U pip setuptools wheel              # old setuptools fails on faust-streaming's opentracing
pip install -r requirements-dev.txt                        # includes aiokafka==0.12.0 for Faust
pytest -q                                                  # expect: 51 passed
```

Phase 0 (gate checks):
```powershell
python -m tools.find_gauges --bbox=-100.5,29.4,-97.4,30.9 --top 40 --write   # config/candidate_gauges.csv
python -m tools.fetch_sample --site 08158700 --nwps DRWT2                      # real USGS + NWPS fixtures
python -m tools.gate_check --years 10 --category minor                        # gate_output/gate_summary.csv
python -m tools.staleness_logger --every-min 5 --hours 48                     # feed delay
python -m tools.gauge_locations                                               # config/gauge_locations.csv (map)
```
EDA notebooks 01 to 03: `notebooks/README.md`; explained cell by cell in `docs/EDA_WALKTHROUGH.md`.

Phase 1 (infrastructure): `docker/README.md`. Pass: services healthy; 8 topics, 3 partitions, RF 3, min ISR 2;
8 tables; stopping one broker keeps topics available (ISR 2, back to 3 on restart).

Phase 2 (producers):
```powershell
python -m producers.usgs_iv_producer --once
python -m tools.peek gauge.usgs --n 5 --from-beginning
python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 36000
python -m tools.peek replay.gauge --n 5 --from-beginning
```
Pass: readings keyed by `site_no`; a second poll publishes only new readings plus heartbeats; replay goes to
`replay.gauge` with `"mode": "SIMULATION"`; after a long outage the producer backfills from its last poll.
