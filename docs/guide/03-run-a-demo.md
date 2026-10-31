# 03 Run a demo: the May 2019 Onion Creek flood, end to end

On 4 May 2019 Onion Creek near Driftwood, Texas (NWS id **DRWT2**, USGS site **08158700**) rose above its minor flood
stage of 5.5 ft. This walkthrough replays the cached readings of 2 to 6 May 2019 through the whole pipeline. Every
record is marked **SIMULATION**. You watch windows, forecasts and an alert appear, approve the advisory, and
optionally send it by email.

Before you start: [02 Setup](02-setup.md) is done, the Docker stack is healthy, the views are applied, and the venv is
active in **every** terminal (`.venv\Scripts\Activate.ps1`, repo root).

**What to expect from the forecaster.** It is still the rise-rate baseline. On the team's measured run of this replay,
it raised its alert **only after** the stage had crossed minor, so it gave no lead time on this flood. The demo shows
the pipeline working, not forecast skill. The trained model is meant to change this, and it will be shown only once it
is measured ([06](06-ml-and-evaluation.md)).

## 0. Get the history (once)

```powershell
python -m ml.fetch_history --gauges-only DRWT2 --start-year 2019 --end-year 2019
```

This writes `data/raw/usgs/08158700_00065_2019.json`. Skip it if `tools.gate_check` already filled the cache.

## 1. Console

Terminal 1:
```powershell
python -m dashboard.flow_monitor
```
Open <http://127.0.0.1:8765> and select **Pipeline flow**. All nodes are grey ("not running") or waiting.

## 2. Faust: three windowing workers

Terminals 2, 3 and 4, one command each:
```powershell
faust -A streams.faust_app worker -l info --web-port 6066
faust -A streams.faust_app worker -l info --web-port 6067
faust -A streams.faust_app worker -l info --web-port 6068
```
Wait for `Worker: Ready` in each. **Console:** *Faust windowing* turns green with **3/3 instances**. Single-broker
stack: set `$env:KAFKA_BOOTSTRAP` and `$env:TOPIC_REPLICATION_FACTOR` in these terminals first.

## 3. Sink and forecaster

Terminal 5: `python -m storage.sink`
Terminal 6: `python -m streams.forecaster`

**Console:** *Postgres sink* and *Forecaster* show 1/1 instance. The sink commits Kafka offsets only after PostgreSQL
has the rows.

Optional, terminal 7, the agents: `python -m agentic_ai.run` (consumer group `agent` on `alerts.flood`). It needs
`LLM_PROVIDER` in `.env`. With `none` it writes a deterministic v2 without any LLM. See [07 Agents](07-agents.md).

## 4. Replay the flood

Terminal 8:
```powershell
python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 3600
```
`--speed 3600` replays one hour of history per second, so the 5 days take about 2 minutes. Use `--speed 36000` for a
12 s run, or `--no-sleep` for a throughput test.

What you should see, in order:

| Where | What appears |
|---|---|
| Replay terminal | `replaying N readings for 08158700 (2019-05-02..2019-05-06) -> replay.gauge`; at the end `done: sent=... in ...s = ... msg/s` |
| Pipeline flow | *Replay* and `replay.*` green with msg/s; dashed green lines move into Faust, then `aggregates.window`, Forecaster and the sink |
| Orbit / Flat map → **Replay (SIMULATION)** | the DRWT2 pillar or dot changes colour as the stage passes action/minor (orange) and moderate (red) |
| Readings & alerts → *Latest windows* | one row per hour for 08158700, mode SIMULATION, stage and rise rate |
| `alerts.flood` node, then *Alerts* table | the alert(s) of this episode, status **draft** |
| Readings & alerts → *Advisories for approval* | a **v1 (template)** draft within seconds of the alert. With the agents running, a **v2 (agents)** draft with the reviewer verdict replaces it |

## 5. Check the run

```powershell
python -m tools.check_windows --mode SIMULATION --site 08158700   # PASS = no window emitted twice
python -m tools.check_sink --mode SIMULATION                      # exit 0 = no gaps, no duplicates
```
The team's measured run gave 119 hourly windows and a PASS from both checks.

## 6. Approve the advisory

In **Readings & alerts → Advisories for approval**:

1. Type your name in **Approver**.
2. Read the draft. It starts with `SIMULATION: replayed historical data, not a real event.` and names the official
   category and stage. Edit the text if needed and click **Save edit**.
3. Click **Approve**, then confirm the second, in-page click. Only now is one record published to `alerts.approved`.
   **Reject** sends nothing.

The status changes to **approved** and the `alerts.approved` node shows one message.

## 7. Send it (optional)

Terminal 9, with the email settings in `.env`:
```powershell
python -m alerting.notifier --dry-run        # logs what would be sent; sends nothing, commits nothing
python -m alerting.notifier                  # sends, then writes delivered_at
```
The email holds exactly the approved text. **Readings & alerts → Alerts → Delivered** fills in, and `alert_overview`
(and Power BI) show `raise_to_approval_s` and `approval_to_delivery_s`. If SMTP fails, the notifier retries 3 times,
then writes the record to `deadletter.flood` (the *dead letters* chip turns amber) and leaves the advisory approved but
undelivered.

## 8. Break something (optional)

- **Kill a Faust worker** mid-replay: stop the terminal with Ctrl+C or let the drill do it.
  `python -m tools.fault_drill --scenario faust` replays 1 to 5 June 2019, kills the worker on port 6068, restarts it,
  measures the recovery and runs `check_windows`. The team measured a 35.6 s recovery with a PASS. The console's
  *Status changes* log shows *Faust windowing* go to 2/3 and back.
- **Stop a broker:** `docker stop flood-kafka-2`. The *brokers* chip shows 2/3 and the topics stay available.
  `docker start flood-kafka-2` brings it back.
- **Kill the sink** mid-replay and start it again, then run `check_sink`.

## Running it again

Faust keeps window state per gauge, so replaying the **same** dates again produces late data, not new windows. Either
replay another flood (pick dates from `gate_output/gate_events.csv` after a gate check), or reset everything. The reset
wipes Kafka and PostgreSQL:

```powershell
cd docker; docker compose down -v; docker compose up -d; cd ..
Get-Content storage\views.sql | docker exec -i flood-postgres psql -U spa_user -d disaster_warning
Remove-Item -Recurse -Force *-data, state -ErrorAction SilentlyContinue      # Faust local stores, forecaster state
```

## Agents without the pipeline

To try only the agent layer: `python -m agentic_ai.seed` inserts a hand-made DRWT2 2019 alert. Its official levels
are real, but the stage, rain and forecast values are illustrative. Then run
`python -m agentic_ai.run --alert-id "08158700|minor|2019-05-04T03:00:00Z"`. Details are in [07 Agents](07-agents.md).
