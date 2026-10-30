# Runbook: what to run, and what you should see on the console

Open the console first and keep it on screen while you run everything else:

```powershell
cd "C:\Users\MY PC\Documents\RVCE\SPA_AAI\disaster-warn-kafka"
..\spaenv\Scripts\Activate.ps1
python -m dashboard.flow_monitor            # http://127.0.0.1:8765  (tabs: Pipeline flow | Map | Readings & alerts)
```
Every command below is run from this folder, in its own PowerShell window, with `spaenv` active.

## Who uses the console, and how

| Who | What they do with it |
|---|---|
| **Duty officer** (county emergency management, river authority; our stand-in is the team mailbox) | Watches the **Map**: every gauge coloured by its official NWS level (below action, action, minor, moderate, major). Opens **Readings & alerts** to read a draft advisory and approves or rejects it. Nothing is sent without that approval. |
| **Pipeline operator** (us) | Watches **Pipeline flow**: is each producer alive, is each consumer group running with all its instances, is lag growing, are brokers up. Reads recovery time off the status-change log. |
| **Reviewer / faculty at the demo** | Sees live mode (real gauges, usually green because it is dry) and **Replay (SIMULATION)**, a past flood played through the same pipeline. Then watches us kill a worker or a broker and sees the pipeline recover. |

Demo story (about 3 minutes): normal day, everything green → switch the Map to Replay and play the May 2019 Onion Creek flood: the gauge climbs through action, minor, moderate → windows appear, the forecaster drafts an alert, the agents write an advisory, a person approves, an email goes out and its delivery time is logged → kill a Faust worker and a broker; the flow tab turns amber, then recovers, with the recovery time in the log.
The console is a development and demo view. The brief names Power BI for the submission dashboard; that still reads PostgreSQL (DirectQuery). The system supports officials; it is not an official warning authority.

## Steps

| # | Run | You should see on the console |
|---|---|---|
| 0 | Restart the console after every `git pull` or code update (the page reloads from disk, the server does not). `python -m tools.gauge_locations` (internet; writes `config/gauge_locations.csv`, 38 gauges) | **Map** tab: the gauges appear (street map; **Offline view** button for demo day without internet). |
| 1 | Live producer loop: `python -m producers.usgs_iv_producer` (polls every 5 min) | **Flow**: USGS live green, `gauge.usgs` receiving. **Map**: most gauges green, "x/38 reporting". |
| 2 | Ctrl-C the producer. To see it quickly, start the console with `--live-stale-s 400` (the producer beats every 5 min; default is 15 min). | **Flow**: USGS live turns red ("SILENT") and the change appears in the log with a time. |
| 3 | Start it again. | It publishes only new readings (a few dozen), not the ~2,400-reading backfill; USGS live goes green again. |
| 4 | Once: `pip install "aiokafka[lz4]==0.12.0" cramjam` (Faust 0.11.3 crashes with newer aiokafka, and needs the lz4 codec). Then three Faust workers, one window each: `faust -A streams.faust_app worker -l info --web-port 6066` (then `6067`, `6068`) | **Flow**: Faust windowing shows `3/3 instances`. |
| 5 | Replay a flood: `python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 36000` | **Flow**: replay topics flow, Faust busy, `aggregates.window` fills. **Map → Replay**: Onion Creek (DRWT2) climbs through the official levels. |
| 6 | Check the windows: `python -m tools.peek aggregates.window --n 3 --from-beginning` then `python -m tools.check_windows --mode SIMULATION --site 08158700` | Expect `PASS: no window emitted twice`; one row per hour, zero-filled hours have `n_readings` 0. |
| 7 | Kill one worker (Ctrl-C in its window) while replaying again (step 5 command). | **Flow**: Faust turns amber `2/3`, status log shows the change; the other two take over its partitions. |
| 8 | Restart that worker (step 4 command), then run `tools.check_windows` again. | `3/3` again; still `PASS`. This is the changelog-recovery claim. |
| 9 | Broker failure: `docker stop flood-kafka-2`, wait 20 s, `docker start flood-kafka-2` | Chip `brokers 2/3` and `under-replicated 8`, then back to 3/3 and 0; both changes in the log. |
| 10 | Throughput baseline: `python -m producers.replay_producer --site 08158700 --start 2017-01-01 --end 2026-09-30 --no-sleep` | Replay topic rate jumps; Faust lag rises then drains. Write the printed `msg/s` in `PROJECT_LOG.md`. |
| 11 | Housekeeping: `docker stats --no-stream`, `w32tm /query /status` | Dead-letter chip stays 0. Memory comfortable, clock synced. |
| 12 | Overnight: `python -m tools.staleness_logger --every-min 5 --hours 48` (laptop awake, mains). Separately, stop the live producer for more than 6 h and restart it. | Restart log shows a backfill request and no gap in readings. |

Phase 4+ rows (forecaster, sink, agents, notifier) are added here as they are built; their nodes show grey "not running" until then, and **Readings & alerts** says it is waiting for the sink.

## Open questions

For faculty (get written answers):
1. **Pooled evaluation**: one model across all gauges, because only 2 of 38 gauges have enough slow floods alone. Acceptable?
2. **Faust instead of Kafka Streams**: the brief names Kafka Streams. Is Faust (Python) accepted?
3. **Agent layer scope**: now a multi-agent design (see `agentic_ai/README.md`). In scope for the SPA review, or Agentic AI only?
4. **What Phase I and II require**: proposal only, or a running demo? How much of Phases 3 to 5 must run?
5. **Hazard wording**: "forecast of crossing an official flood category" (US live pipeline, India as retrospective) instead of "predicts a flood".
6. **Dashboard**: is our console plus Power BI (DirectQuery) acceptable for "real-time Power BI dashboard"?

For us to decide:
1. MRMS catchment: circle around each gauge, radius? (start simple, say so in the report).
2. How soon after the last message a live producer counts as silent (now 15 min).
3. Who receives the demo email, and the email account the notifier sends from.

## Remaining build steps: what to run and how to test each (commands exist once that step is built)

| # | Step (slice) | Run | Test: pass when |
|---|---|---|---|
| 13 | Faust (S1) | `pip install "aiokafka[lz4]==0.12.0" cramjam`, then steps 4 to 8 | 3/3 instances; `aggregates.window` fills during a replay; `tools.check_windows` PASS after a worker kill |
| 14 | New topic (S1) | `cd docker; docker compose up -d --force-recreate topic-init` | `forecasts.stage` listed; console shows 9 topics |
| 15 | Sink (S1) | `python -m storage.sink` | after a replay, row counts in PostgreSQL match topic counts; kill it mid-replay, restart: no gaps, no duplicates (`python -m tools.check_sink`) |
| 16 | Forecaster (S1) | `python -m ml.forecaster_consumer` | May 2019 DRWT2 replay: one forecast per window in `forecasts.stage`; one alert before the minor crossing; console Readings tab shows it |
| 17 | Approval + email (S1) | `python -m alerting.notifier`; approve in the console | version 1 draft appears within seconds; approve → email arrives; `delivered_at` filled; reject → nothing sent |
| 18 | Power BI (S1) | open `dashboard/powerbi/DEWS.pbix`, DirectQuery to `localhost:5432` | a new replayed reading appears within one page refresh |
| 19 | Fault drills (S1) | `python -m tools.fault_drill --scenario faust` (also `broker`, `sink`, `postgres`, `notifier`, `producer`) | console log shows the failure and recovery; `recovery_time_s` row written |
| 20 | Rainfall (S2) | `python -m producers.mrms_qpe_producer` and `--replay` for the May 2019 flood | `rain.mrms` has a record per gauge per hour; rain rises before stage in the console |
| 21 | Model (S3) | forecaster picks up `ml/models/*.joblib` automatically | forecasts show `model = <name>`; evaluation rows in Power BI |
| 22 | Agents (S1 skeleton → S4) | `ollama pull qwen2.5:7b`; `python -m agentic_ai.run` | version 2 advisory replaces version 1 after the reviewer passes; uncertain cases marked |
