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
| 1 | S1 | Once: `pip install "aiokafka[lz4]==0.12.0" cramjam`. Then three Faust workers: `faust -A streams.faust_app worker -l info --web-port 6066` (and `6067`, `6068`) | console: Faust windowing `3/3 instances`, no crash |
| 2 | S1 | `cd docker; docker compose up -d --force-recreate topic-init` | `forecasts.stage` in the topic list (9 topics) |
| 3 | S1 | Replay: `python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 36000`, then `python -m tools.check_windows --mode SIMULATION --site 08158700` | `aggregates.window` fills; `PASS: no window emitted twice`; zero-filled hours have `n_readings` 0 |
| 4 | S1 | Kill one Faust worker (Ctrl-C) during a replay; restart it; `tools.check_windows` again | Faust amber `2/3`, then `3/3`; still PASS |
| 5 | S1 | Stop the live producer with the console started as `python -m dashboard.flow_monitor --live-stale-s 400`; start it again | "USGS live" turns red and back to green; both changes in the status log |
| 6 | S1 | Throughput: `python -m producers.replay_producer --site 08158700 --start 2017-01-01 --end 2026-09-30 --no-sleep` | note the printed msg/s; Faust lag rises then drains |
| 7 | S1 | `docker stats --no-stream`; `w32tm /query /status` | memory comfortable; clock recently synced |
| 8 | S1 | Overnight: `python -m tools.staleness_logger --every-min 5 --hours 48` (laptop awake, on mains) | 48 h of polls in `gate_output/staleness_log.csv` |
| 9 | S1 | `python -m storage.sink` | after a replay, PostgreSQL row counts match the topics; kill and restart mid-replay: `python -m tools.check_sink` reports no gaps, no duplicates |
| 10 | S1 | `python -m streams.forecaster` | the 2019 replay gives one forecast per window and one alert before the minor crossing |
| 11 | S1 | `python -m alerting.notifier`; approve the draft in Readings & alerts | v1 draft within seconds; email arrives; `delivered_at` filled; rejecting sends nothing |
| 12 | S1 | Power BI page 1 (guide in `dashboard/powerbi/README.md`) | a new replayed reading appears within one page refresh |
| 13 | S1 | `python -m tools.fault_drill --scenario faust` (also `broker`, `sink`, `postgres`, `notifier`, `producer`) | failure and recovery in the console log; `recovery_time_s` row written |
| 14 | S1 | `python -m agentic_ai.run --alert-id <id>` (agents workstream) | v2 advisory replaces v1; events visible in the Agents tab |
| 15 | S1 | `python -m tools.verify_slice S1` | all checks PASS |
Steps for S2 to S5 are added here when their code exists (see `docs/SLICES.md`).
