# 03 Run a demo: the May 2019 Onion Creek flood, end to end

On 4 May 2019 Onion Creek near Driftwood, Texas (NWS id **DRWT2**, USGS site **08158700**) rose from under 2 ft to a
peak of about 26 ft within a few hours. Its official major flood stage is 13.0 ft. This walkthrough replays the cached
readings of 2 to 6 May 2019 through the whole pipeline. Every record is marked **SIMULATION**. You watch windows,
forecasts and an alert appear, approve the advisory, and optionally send it by email.

Before you start: [02 Setup](02-setup.md) is done, the Docker stack is healthy, and the venv is active in **every**
terminal (`.venv\Scripts\Activate.ps1`, repo root).

**What to expect from the forecaster.** At the 3 h horizon the forecaster now uses the trained `s3_v0` model; it
falls back to the rise-rate rule at other horizons and on any error. On the team's measured run of this replay with
the rise-rate rule, the first DRWT2 alert (`08158700|major|2019-05-04T01:00:00Z`) came in the window where the gauge
was already at 8.2 ft, above moderate, so it gave **no lead time on the minor crossing**. This replay has not been
re-measured with `s3_v0` active. The demo shows the pipeline working, not forecast skill
([06](06-ml-and-evaluation.md)).

**No radar rain for this flood.** The MRMS archive starts in October 2020. For a flood with rain, see
[A flood with radar rain](#a-flood-with-radar-rain) below.

## 0. Get the history (once)

```powershell
python -m ml.fetch_history --gauges-only DRWT2 --start-year 2019 --end-year 2019
```

This writes `data/raw/usgs/08158700_00065_2019.json`. Skip it if the cache already holds that year.

## 1. Start the pipeline

**One command (Windows):** `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -NoLiveProducer` starts
everything below in the background (venv: `.venv` in the repo, else `..\spaenv`, or `-Venv <folder>`). Then go to
step 2.

**By hand**, one terminal each:

```powershell
python -m dashboard.flow_monitor                                  # console, http://127.0.0.1:8765
faust -A streams.faust_app worker -l info --web-port 6066         # Faust worker 1
faust -A streams.faust_app worker -l info --web-port 6067         # Faust worker 2
faust -A streams.faust_app worker -l info --web-port 6068         # Faust worker 3
python -m storage.sink                                            # Kafka -> PostgreSQL, writes advisory v1
python -m streams.forecaster                                      # windows -> forecasts and alerts
```

Open the console and choose **Pipeline flow** on the landing page. Wait for `Worker: Ready` in each Faust terminal.
*Faust windowing* then shows **3/3 instances**, and *Postgres sink* and *Forecaster* show 1/1. On the single-broker
stack, set `$env:KAFKA_BOOTSTRAP` and `$env:TOPIC_REPLICATION_FACTOR` in every terminal first.

Optional, the agents: `python -m agentic_ai.run` (consumer group `agent` on `alerts.flood`). With `LLM_PROVIDER=none`
it writes a deterministic v2 without any LLM. See [07 Agents](07-agents.md).

## 2. Replay the flood

```powershell
python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 3600
```

`--speed 3600` replays one hour of history per second, so the 5 days take about 2 minutes. `--speed 36000` takes about
12 s; `--no-sleep` is the throughput test. To show the neighbouring gauges as well, run the same command with
`--site 08158840` and `--site 08159000`.

What you should see, in order:

| Where | What appears |
|---|---|
| Replay terminal | `replaying N readings for 08158700 (2019-05-02..2019-05-06) -> replay.gauge`; at the end `done: sent=... in ...s = ... msg/s` |
| Pipeline flow | *Replay* and `replay.*` green with msg/s; dashed green lines move into Faust, then `aggregates.window`, Forecaster and the sink |
| Map → **Replay (SIMULATION)** | the DRWT2 pillar rises and changes colour: yellow/orange at action and minor (both 5.5 ft), red at moderate (7.0 ft), purple at major (13.0 ft). A dashed red beam marks the open alert, and an alert line appears under the header |
| Readings & alerts → *Latest windows* | one row per hour for 08158700, mode SIMULATION, stage and rise rate |
| Readings & alerts → *Alerts* | the alert, status **draft** |
| Readings & alerts → *Advisories for approval* | a **v1 (template)** draft within seconds of the alert. With the agents running, a **v2 (agents)** draft with the reviewer verdict replaces it |
| Metrics | throughput per topic, consumer lag rising then draining, the latency waterfall for replay |
| Agents (if they ran) | the agent graph and the last run, step by step |

## 3. Check the run

```powershell
python -m tools.check_windows --mode SIMULATION --site 08158700   # PASS = no window emitted twice
python -m tools.check_sink --mode SIMULATION                      # exit 0 = no gaps, no duplicates
python -m tools.verify_slice S1 --part core                       # the slice's acceptance checks against the running stack
```

`verify_slice` prints PASS or FAIL per check with what it measured, and lists the steps only a person can confirm as
MANUAL. Expect a FAIL on "alert before the minor crossing" while the baseline is in use: that check is the honest one.

## 4. Approve the advisory

In **Readings & alerts → Advisories for approval**:

1. Type your name in **Approver**.
2. Read the draft. It starts with `SIMULATION: replayed historical data, not a real event.` and names the official
   category and stage. Edit the text if needed and click **Save edit**.
3. Click **Approve**, then confirm the second, in-page click. Only now is one record published to `alerts.approved`.
   **Reject** sends nothing.

## 5. Send it (optional)

With the email settings in `.env` ([02](02-setup.md#email-notifier)):
```powershell
python -m alerting.notifier --dry-run        # builds the email and logs it; sends nothing, commits nothing
python -m alerting.notifier                  # sends, then writes delivered_at (start_demo.ps1 already runs it)
```

The email is a flood-forecast bulletin: a headline sentence, a small table of facts from the alert, the approved
advisory text and the approver. **Alerts → Delivered** fills in, and `alert_overview` (and Power BI) show
`raise_to_approval_s` and `approval_to_delivery_s`. If SMTP fails, the notifier retries 3 times, then writes the
record to `deadletter.flood` (the *DLQ* chip turns amber) and leaves the advisory approved but undelivered.

## 6. Break something (optional)

`tools.fault_drill` breaks one part during a replay, restarts it and measures recovery. Each replay drill replays a date
range **after the newest one already replayed** for the gauge, so every window is new output (`--start`/`--end`
override this). The drill writes a `fault_drill:<scenario>` row to `evaluation_runs` (skip it with `--no-db`):

```powershell
python -m tools.fault_drill --scenario faust      # kill Faust worker 6068 mid-replay -> check_windows must PASS
python -m tools.fault_drill --scenario broker     # stop flood-kafka-2 -> windows keep flowing
python -m tools.fault_drill --scenario sink       # kill the sink -> check_sink must PASS
python -m tools.fault_drill --scenario postgres   # stop PostgreSQL -> messages wait on Kafka, check_sink must PASS
python -m tools.fault_drill --scenario notifier   # notifier down; its position must not move; sends nothing
python -m tools.fault_drill --scenario producer   # kill the live USGS producer -> nothing published twice
```

Team measurement (one run each, 31 Oct 2026): recovery 18.1 s (Faust), 21.1 s (broker), 5.4 s (sink), 3.9 s
(PostgreSQL), 1.1 s (notifier), 4.2 s (producer), all checks PASS. The console's *Status changes* log on Pipeline flow
shows each failure and recovery with timestamps.

## Running it again

Faust keeps window state per gauge, so replaying the **same** dates again produces late data, not new windows. Either
replay another flood (pick dates from `gate_output/gate_events.csv` after a gate check), or reset everything. The reset
wipes Kafka and PostgreSQL:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1        # or stop each terminal with Ctrl+C
cd docker; docker compose down -v; docker compose up -d; cd ..
Remove-Item -Recurse -Force *-data, state -ErrorAction SilentlyContinue      # Faust local stores, forecaster state
```

## A flood with radar rain

The MRMS rain producer (`producers/mrms_qpe_producer.py`) is on branch `claude/rain` and is **not yet merged**. Rain is
not yet joined into the windows, so it shows on `replay.rain` and the Pipeline flow, not in the forecast. Once the
branch is merged:

```powershell
python -m producers.mrms_qpe_producer --replay --start 2023-10-26T00 --end 2023-10-26T18 --speed 1800
```

ML's candidate list (`ml/figures/demo_flood_candidates.csv`, 102 minor crossings since Oct 2020) suggests
**AHOT2 / LLAT2 on 26 Oct 2023** as the visual rain demo: 23 to 75 mm in the 24 h before. That date lies in the
**validation** period used to choose the model's threshold, so its alert lead times are not results. Quote only
test-period numbers (2024 to 2026, `ml/figures/s3_eval.csv`) as evidence.

## Agents without the pipeline

`python -m agentic_ai.seed` writes the same measured May 2019 record (DRWT2 and two neighbours, run through the
pipeline's own window and forecast code) straight into PostgreSQL, with no Kafka needed. Then run
`python -m agentic_ai.run --alert-id "08158700|major|2019-05-04T01:00:00Z"`. Details are in [07 Agents](07-agents.md).
