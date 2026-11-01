# Runbook: what is left to run, and how to tell it worked

This file lists only steps that are still open. When a step is confirmed, it is deleted here and its measured result
is recorded in `BREADCRUMBS.md`. The story, roles and demo scenes are in `docs/DEMO.md`; the plan is in
`docs/SLICES.md`.

Start: repo root, venv active, console running and on screen:
```powershell
python -m dashboard.flow_monitor            # http://127.0.0.1:8765; restart it after every code update
```

## Open steps

| # | Slice | Run | Pass when |
|---|---|---|---|
| 5 | S1 | Stop the live producer with the console started as `python -m dashboard.flow_monitor --live-stale-s 400`; start it again | "USGS live" turns red and back to green; both changes in the status log |
| 6 | S1 | Throughput: `python -m producers.replay_producer --site 08158700 --start 2017-01-01 --end 2026-09-30 --no-sleep` | note the printed msg/s; Faust lag rises then drains |
| 7 | S1 | `docker stats --no-stream`; `w32tm /query /status` | memory comfortable; clock recently synced |
| 8 | S1 | Overnight: `python -m tools.staleness_logger --every-min 5 --hours 48` (laptop awake, on mains) | 48 h of polls in `gate_output/staleness_log.csv` |
| 10 | S1 | `python -m streams.forecaster` | the 2019 replay gives one forecast per window and one alert per episode, with its lead time measured and reported (negative allowed: alerting before the crossing is S3's acceptance, against the trained model) |
| 12 | S1 | Power BI page 1 (guide in `dashboard/powerbi/README.md`) | a new replayed reading appears within one page refresh |
| 14 | S1 | `python -m agentic_ai.run --alert-id <id>` (agents workstream) | v2 advisory replaces v1; events visible in the Agents tab |
| 15 | S1 | `python -m tools.verify_slice S1` | all checks PASS |
| 16 | S2 | After merging `claude/rain`: `pip install -r requirements-dev.txt` (adds numpy, eccodes, eccodeslib); `python -c "import eccodes; print(eccodes.codes_get_api_version())"`; apply the schema to the running database: `Get-Content storage\schema.sql -Raw \| docker exec -i flood-postgres psql -U spa_user -d disaster_warning`; restart the 3 Faust workers, the sink and the console | eccodes prints a version; psql ends without ERROR; `catchment_rain` has a `mode` column and `windowed_aggregates` a `rain_24h` column |
| 17 | S2 | AHOT2 history if not cached: `python -m ml.fetch_history --gauges-only AHOT2 --start-year 2023 --end-year 2023`. Then, on a reset replay state (the replay table drops readings older than its newest window), rain FIRST: `python -m producers.mrms_qpe_producer --replay --start 2023-10-25 --end 2023-10-26 --sites 08156800 --no-sleep`, then stage: `python -m producers.replay_producer --site 08156800 --start 2023-10-25 --end 2023-10-26 --speed 3600` | rain: 48/48 hours, failed=0; Faust log quiet; AHOT2 windows in the console/PostgreSQL carry rain_1h/3h/6h |
| 18 | S2 | Live rain next to the live USGS producer: `python -m producers.mrms_qpe_producer --probe`, then `python -m producers.mrms_qpe_producer` (every 120 s) | probe: RadarOnly about 3 min late; each hour 38 LIVE rows in `catchment_rain` within about 5 min of the hour; live windows carry rain_1h |
| 19 | S2 | `python -m tools.check_sink --mode SIMULATION`; `python -m tools.check_windows --mode SIMULATION --site 08156800` | catchment_rain 0 gaps, 0 duplicates; no window emitted twice |
| 20 | S2 | `python -m tools.verify_slice S2 --part core` | 6 of 6 PASS on the REAL AHOT2 stage (the cloud run passed on a synthetic stage only); note the reported rain-peak lead |
Steps for S3 to S5 are added here when their code exists (see `docs/SLICES.md`).
