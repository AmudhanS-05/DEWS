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
| 10 | S1 | `python -m streams.forecaster` | the 2019 replay gives one forecast per window and one alert before the minor crossing |
| 12 | S1 | Power BI page 1 (guide in `dashboard/powerbi/README.md`) | a new replayed reading appears within one page refresh |
| 14 | S1 | `python -m agentic_ai.run --alert-id <id>` (agents workstream) | v2 advisory replaces v1; events visible in the Agents tab |
| 15 | S1 | `python -m tools.verify_slice S1` | all checks PASS |
Steps for S2 to S5 are added here when their code exists (see `docs/SLICES.md`).
