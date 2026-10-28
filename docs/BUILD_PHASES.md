# Build phases and how to test each one

Hazard: riverine floods and flash floods, gauge level (design: Claude Doc
"Architecture — Floods & Flash Floods (rev 3, proposed)").
Rule: do not start a phase until the previous phase's test passes. Record each
result in `BREADCRUMBS.md` (one line) and anything report-worthy in `PROJECT_LOG.md`.

Status key: **built + unit-tested** = code exists and `pytest` covers its logic;
**needs your machine** = must be run where USGS/NWPS/Docker are reachable
(the build workspace cannot reach USGS or run Docker).

## One-time setup (Windows, PowerShell, repo root)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel     # old setuptools fails on faust-streaming's opentracing
pip install -r requirements-dev.txt
pytest -q                                          # expect: 39 passed
```

## Phase 0 — Gate checks (decide if floods are buildable) · built + unit-tested · needs your machine

1. Fill `config/candidate_gauges.csv` with 5–10 gauges:
   `python -m tools.find_gauges --bbox=-100.5,29.4,-97.4,30.9 --top 10 --write`
   (any region; it keeps only NWS gauges linked to a USGS site that have official
   action + minor flood stages). To look up one known gauge's NWPS id:
   `python -m tools.find_gauges --near-usgs 09510200`.
2. `python -m tools.fetch_sample --site 09510200 --nwps <LID>` then `pytest -q`
   → the 2 skipped tests now run against **real** responses. Both must pass;
   if not, the parser layout is wrong (fix before anything else).
3. `python -m tools.gate_check --years 10 --category minor`
   → `gate_output/gate_summary.csv` + `gate_events.csv`.
   **Pass:** at least one gauge `PASS` (≥ 10 events, median rise ≥ 3 h).
   `ERROR` means data could not be fetched, which is not a verdict.
   **Kill:** every gauge `FAIL` → stop and revisit the hazard choice.
4. Start `python -m tools.staleness_logger --hours 72` and leave it running
   (feeds the latency section; look for staleness during rain).
5. EDA for submission: run `notebooks/01–03` in Colab/Kaggle (see `notebooks/README.md`).
6. For the top 2–3 gauges, plot stage vs rainfall for 2–3 events (rainfall source:
   MRMS, Phase 2b) and check they visibly track.

## Phase 1 — Infrastructure · built (compose YAML-validated, schema loads on PG16) · needs your machine

`docker/README.md` has the full steps. Watch it live: `python -m dashboard.flow_monitor` (http://127.0.0.1:8765).
**Pass:** all services healthy; 8 topics with
3 partitions, RF 3, min ISR 2; 8 tables; stopping one broker keeps the topics available.

## Phase 2 — Producers · built + unit-tested · needs your machine

```powershell
python -m producers.usgs_iv_producer --once          # one poll
python -m tools.peek gauge.usgs --n 5 --from-beginning
```
**Pass:** readings arrive keyed by site_no with `fetched_at`; a second `--once`
publishes 0 new readings (only heartbeats); `state/usgs_producer_state.json` exists.
Replay: `python -m producers.replay_producer --site <S> --start <d> --end <d> --speed 3600`
→ messages go to `replay.gauge` (not `gauge.usgs`) and have `"mode": "SIMULATION"`. Throughput: add `--no-sleep`, note msg/s.

Phase 2b (not built yet): `mrms_qpe_producer.py` → `rain.mrms` (needs the MRMS
product choice and a catchment-boundary source, both unverified).

## Phase 3 — Windowing (Faust) · logic built + unit-tested; Faust app import-checked only · needs your machine

```powershell
# three terminals, one Faust instance each (one group, 3 partitions)
faust -A streams.faust_app worker -l info --web-port 6066
faust -A streams.faust_app worker -l info --web-port 6067
faust -A streams.faust_app worker -l info --web-port 6068
python -m producers.replay_producer --site <S> --start <d> --end <d> --speed 36000
python -m tools.peek aggregates.window --n 5 --from-beginning
```
**Pass:** one aggregate per gauge per hour, `n_readings` > 0 during data, `n_readings`
0 rows for gaps (zero-fill), revisions flagged. Kill the worker (Ctrl-C), restart it:
it resumes without re-emitting closed windows (state recovered from the changelog).

## Phase 4 — Forecaster · offline backtest built + unit-tested; streaming consumer not built

```powershell
python -m ml.backtest --site <S> --start 2015-01-01 --end 2025-12-31 --lid <LID> --category minor --horizon-h 3
```
**Pass:** numbers for persistence and rise-rate (precision, recall, F1, false alarms,
lead time, MAE/RMSE). These are the baselines any ML model must beat. Next to build:
one ML model (choice made after seeing these numbers) and `ml/forecaster_consumer.py`
(aggregates.window → risk_scores + alerts.flood).

## Phase 5 — Storage sink + latency trace · not built
`storage/sink.py` (group `postgres-sink`): consumes aggregates.window and alerts.flood, upserts into Postgres,
commits offsets only after the write. **Test:** row counts match topic counts after a
replay; killing the sink mid-replay and restarting leaves no gaps and no duplicates.

## Phase 6 — Alerts, agent triage, human approval, notifier · not built
`alerts.flood` → agent drafts an advisory → a person approves (CLI or dashboard) →
`alerts.approved` → notifier sends an email, writes a log line and records delivered_at.
**Test:** a replayed flood produces a SIMULATION-stamped draft; nothing is sent until
approved; delivered_at − alert_time is logged.

## Phase 7 — Dashboard · not built
Power BI Desktop (DirectQuery → Postgres) for submission; Streamlit for development.
**Test:** dashboard refresh shows a new replayed reading within the refresh interval.

## Phase 8 — Fault injection and evaluation · not built
Scripted kills (broker, Faust worker, sink, Postgres, notifier, feed) with recovery
times; final metrics into `evaluation_runs`, live and replay kept separate.
