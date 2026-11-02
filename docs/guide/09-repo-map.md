# 09 Repo map

Folder by folder: what is in it, which part of the system it belongs to, and how to run it.

| Folder | Part | What |
|---|---|---|
| `common/` | core | shared code: `config.py` (topics, groups, env overrides), `usgs.py` and `nwps.py` (parse the data services), `hydro.py` (crossings, rise times), `kafka_io.py` (producer with acks=all, idempotence, lz4), `timeutil.py`, `envfile.py` (`.env` loader that never prints values), `progress.py` |
| `producers/` | core | `usgs_iv_producer.py` (live, all 38 gauges every 5 min, dedupe, heartbeats, backfill after downtime), `replay_producer.py` (cached history → `replay.gauge`, SIMULATION), `mrms_qpe_producer.py` (NOAA MRMS radar rain per gauge, live and replay), `tracking.py` (dedupe and the record envelope) |
| `streams/` | core | `faust_app.py` (Faust app, group `flood-windowing`), `windowing.py` (event-time windows as pure functions), `forecaster.py` (windows → forecasts and alert episodes) |
| `storage/` | core | `schema.sql` (tables, loaded by Docker on a new volume), `views.sql` (views for the console and Power BI, applied by hand), `sink.py` (Kafka → PostgreSQL, commit after write) |
| `alerting/` | core | `template.py` (advisory v1), `approval.py` (Approve / Edit / Reject, the only path to `alerts.approved`), `notifier.py` (email, retry, dead letter, `delivered_at`) |
| `dashboard/` | core | `flow_monitor.py` + `flow.html` + `static/` (the console: landing page, globe and map, pipeline flow, approval, metrics, agents); `powerbi/README.md` (Power BI guide) |
| `docker/` | core | `docker-compose.yml` (3 KRaft brokers, topic init, PostgreSQL 16), `docker-compose.single.yml` (one broker) |
| `tools/` | core | command-line checks and data tools (table below), including `verify_slice.py` (acceptance checks per slice) and `fault_drill.py` |
| `scripts/` | core | `start_demo.ps1` / `stop_demo.ps1`: start or stop the whole demo on Windows (`-Venv <folder>` or `DEWS_VENV`; default `.venv`, else `..\spaenv`) |
| `ml/` | ML | baselines, dataset builder, evaluation harness, history fetchers, models; `MODEL_CARD.md`; `predict.py` is the pipeline's entry point |
| `notebooks/` | ML | EDA notebooks 01 to 03, self-contained (Colab/Kaggle); every cell explained in `docs/EDA_WALKTHROUGH.md` |
| `agentic_ai/` | agents | LangGraph graph (`graph.py`), reviewer checks (`checks.py`), disagreement rules (`rules.py`), PostgreSQL tools (`tools.py`), LLM switch (`llm.py`), tracing, `seed.py`, `run.py`, `fixtures/` |
| `config/` | core / ML | `candidate_gauges.csv` (gate-check input), `gauge_locations.csv` (38 gauges, location, official levels), `regions.json` (monitored areas on the map) |
| `tests/` | all | `pytest` suite: core tests at the top level, `tests/ml`, `tests/agents`; `tests/fixtures` holds real USGS/NWPS responses |
| `docs/` | all | contracts, operations runbook, demo script, architecture, this guide (`docs/guide/`), paper draft (`docs/paper/`), EDA figures (`docs/eda/`), console screenshots (`docs/figures/`) |

Git-ignored local folders: `data/` (cached history), `state/` (producer and forecaster state), `gate_output/`
(gate-check results, staleness log), `*-data/` (Faust local stores), `.env`.

## Commands at a glance

Every command runs from the repo root with the venv active. `--help` on any of them lists its options.

| Command | Does |
|---|---|
| `python -m dashboard.flow_monitor [--demo]` | the console at http://127.0.0.1:8765 |
| `python -m producers.usgs_iv_producer [--once] [--every-s 300] [--sites ...]` | live USGS → `gauge.usgs` |
| `python -m producers.replay_producer --site S --start D --end D [--speed 3600 \| --no-sleep]` | cached history → `replay.gauge` |
| `faust -A streams.faust_app worker -l info --web-port 6066` | one Faust worker (run 3, ports 6066 to 6068) |
| `python -m streams.forecaster [--group G]` | windows → `forecasts.stage`, `alerts.flood` |
| `python -m storage.sink [--group G]` | topics → PostgreSQL; a new group name re-reads everything |
| `python -m alerting.notifier [--dry-run]` | `alerts.approved` → email |
| `python -m agentic_ai.seed` / `python -m agentic_ai.run [--alert-id ID] [--force]` | agent test alert / agents |
| `python -m tools.peek TOPIC --n 5 --from-beginning` | print messages from a topic |
| `python -m tools.check_windows [--mode SIMULATION] [--site S]` | no window emitted twice |
| `python -m tools.check_sink [--mode SIMULATION]` | topics vs tables: no gaps, no duplicates |
| `python -m tools.fault_drill --scenario faust\|broker\|sink\|postgres\|notifier\|producer` | break one part, measure recovery, check nothing was lost or duplicated |
| `python -m tools.verify_slice S1 [--part core\|ml\|agents] [--skip-tests]` | acceptance checks against the running stack (stages S1 to S5) |
| `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1` | start everything (Windows); `stop_demo.ps1` stops it |
| `python -m tools.find_gauges --bbox=-100.5,29.4,-97.4,30.9 --top 40 --write` | find candidate gauges (NWPS) |
| `python -m tools.gauge_locations` | write `config/gauge_locations.csv` |
| `python -m tools.gate_check --years 10` | is there enough flood history? Also fills the cache |
| `python -m tools.staleness_logger --every-min 5 --hours 48` | measure the live feed delay |
| `python -m tools.fetch_sample --site 08158700 --nwps DRWT2` | save real responses as test fixtures |
| `python -m ml.fetch_history`, `ml.harness`, `ml.dataset`, `ml.mrms_history`, `ml.rain_model`, `ml.backtest` | ML; see [06](06-ml-and-evaluation.md) |

## Documents in `docs/`

| File | Read it for |
|---|---|
| `CONTRACTS.md` | the exact record fields, tables and function signatures between the parts |
| `architecture.md` | the architecture in one page |
| `RUNBOOK.md` | operations: start, stop, replay again, check, recover |
| `DEMO.md` | a demo script with roles and scenes |
| `EDA_WALKTHROUGH.md`, `references.md` | the EDA notebooks explained cell by cell; literature |
| `paper/` | a draft technical write-up with a source for every number |
