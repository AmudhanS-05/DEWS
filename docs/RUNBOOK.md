# Operations runbook

How to start, check, replay, recover and stop DEWS. Commands run from the repo root with the venv active. Every
command takes `--help`. The demo scenes are in [`DEMO.md`](DEMO.md); setup is in
[`guide/02-setup.md`](guide/02-setup.md); known problems are in [`guide/08-troubleshooting.md`](guide/08-troubleshooting.md).

## Start

| Step | Command | Healthy when |
|---|---|---|
| Everything at once (Windows) | `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1` | console chips green |
| Kafka + PostgreSQL | `cd docker; docker compose up -d; cd ..` | 3 brokers up, 9 topics, tables and views loaded on first start |
| Console | `python -m dashboard.flow_monitor` | http://127.0.0.1:8765 answers; restart it after code changes |
| Live gauges | `python -m producers.usgs_iv_producer` | new rows on `gauge.usgs` every 5 min (heartbeats when the river is quiet) |
| Live radar rain | `python -m producers.mrms_qpe_producer` | rows on `rain.mrms` about 3 min after the hour |
| Windows (3 workers) | `faust -A streams.faust_app worker -l info --web-port 6066` (and 6067, 6068) | group `flood-windowing` has 3 members |
| Forecaster | `python -m streams.forecaster` | one forecast per closed window |
| Sink | `python -m storage.sink` | topics match tables (`tools.check_sink`) |
| Notifier | `python -m alerting.notifier` | sends only from `alerts.approved` |
| Agents | `python -m agentic_ai.run --alert-id ID` | a v2 draft saved, or v1 kept with the reason printed |

## Check

| What | Command | Pass when |
|---|---|---|
| No window emitted twice | `python -m tools.check_windows --mode SIMULATION --site 08158700` | PASS |
| Topics match PostgreSQL | `python -m tools.check_sink --mode SIMULATION` | exit 0: no gaps, no duplicates |
| Acceptance checks | `python -m tools.verify_slice S1` (then `S2` to `S5`) | every check PASS; MANUAL lines need a person |
| Live feed delay | `python -m tools.staleness_logger --every-min 5 --hours 48` | a staleness row per gauge and poll |
| Throughput | `python -m tools.throughput_run` | one `throughput:replay` row in `evaluation_runs` |

The acceptance checks are grouped in stages: S1 the pipeline end to end on a replayed flood, S2 radar rain, S3 the
trained forecaster, S4 the agents, S5 robustness (every fault drill, throughput, demo fallbacks).

## Replay a flood again

Replay refuses dates already replayed since the last reset (Faust's position is past them, and the readings would go
to the dead-letter topic). Reset first, then replay:

```powershell
python -m tools.reset_replay --site <id> --start <date> --end <date> --yes
python -m producers.replay_producer --site <id> --start <date> --end <date> --speed 3600
```

The reset stops Faust and the forecaster for 1.5 to 5 minutes (longer the more replay history Faust has to reload).
The console's Control tab runs the same steps.

## Recover

| Failure | Do this | Drill |
|---|---|---|
| A Faust worker dies | restart it; its partition is rebuilt from the changelog topic | `python -m tools.fault_drill --scenario faust` |
| A broker stops | restart it; topics stay available with RF 3, min ISR 2 | `--scenario broker` |
| Sink or PostgreSQL stops | restart; the sink commits offsets only after the database commit, so nothing is lost or doubled | `--scenario sink`, `--scenario postgres` |
| Notifier stops | restart; it resumes from `alerts.approved` | `--scenario notifier` |
| Live producer stops | restart; it backfills from the last published reading | `--scenario producer` |
| LLM down or no key | nothing to do: `LLM_FALLBACK` is tried, then the template draft v1 stays | `--scenario llm` |

Each drill breaks one component during a replay, restarts it, times the recovery and checks for loss or duplication.
Results go to `evaluation_runs` (`run_label` `fault_drill:*`).

## Stop

`scripts\stop_demo.ps1` (Windows), or stop the Python processes and `cd docker; docker compose down`. Add `-v` to
`docker compose down` only to delete the PostgreSQL and Kafka volumes.
