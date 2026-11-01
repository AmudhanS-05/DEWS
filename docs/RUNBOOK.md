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
| 8 | S1 | In progress (started 31 Oct; 48 h logger running). Overnight: `python -m tools.staleness_logger --every-min 5 --hours 48` (laptop awake, on mains) | 48 h of polls in `gate_output/staleness_log.csv` |
| 12 | S1 | Power BI page 1 (guide in `dashboard/powerbi/README.md`) | a new replayed reading appears within one page refresh |
| 15 | S1 | `python -m tools.verify_slice S1` with both floods replayed (main scene, DRWT2 May 2019) and one draft approved | all checks PASS |
To replay the same dates again: `python -m tools.reset_replay --site <id> --start <date> --end <date> --yes` (stops Faust and the forecaster for 1.5 to 5 minutes; longer the more replay history Faust has to reload).
Steps for S2 to S5 are added here when their code exists (see `docs/SLICES.md`).
