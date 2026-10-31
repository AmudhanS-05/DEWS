# 01 Concepts

The ideas you need to read the console, the code and the results. Each section says how DEWS uses the idea, not
the general theory.

## The problem: lead time and feed delay

A **gauge** is one station on a river. It records the water level (**stage**, also called gage height, in feet) every
5 or 15 minutes. USGS publishes the readings, but they are transmitted roughly hourly, so the newest reading is
usually 15 to 75 minutes old when it arrives. One sample measured a delay of 24 to 69 minutes.

- **Lead time** is how long before the stage reaches a flood category the alert is raised.
- **Gross lead time** is measured from the reading's own timestamp. **Net lead time** subtracts the feed delay, so it
  counts from when we could actually have known. DEWS always reports both.
- **Rise time** is the time from the start of a rise to the flood crossing. It caps any warning. At slow rivers such
  as Onion Creek near Driftwood (DRWT2) it is about 10 h. At fast urban creeks it is under an hour, which the feed
  delay mostly uses up. This is why lead-time claims are made only on slow events (rise of 3 h or more).

## Official flood categories (NWPS)

Every NWS forecast gauge has official stages published by the NOAA National Water Prediction Service (NWPS):

| Category | Meaning (NWS) | DRWT2 example |
|---|---|---|
| action | the level where officials prepare to act | 5.5 ft |
| minor | minimal or no property damage, some public threat | 5.5 ft |
| moderate | some inundation of structures and roads, some evacuations | 7.0 ft |
| major | extensive inundation, significant evacuations | 13.0 ft |

DEWS uses these as its **only** labels and alert levels. We never invent thresholds. Some gauges have action equal to
minor (DRWT2 does). The levels for the 38 gauges are in `config/gauge_locations.csv`, built by
`python -m tools.gauge_locations`.

### Why "forecast of crossing a category" and not "predicts a flood"

NWS defines minor flood stage as the level where flooding starts to cause impacts at that gauge. So "the stage is
forecast to reach minor within 3 h" is a statement about one gauge location. It says nothing about:

- flooded area, depth away from the gauge, or damage;
- creeks without a gauge, which is where many flash floods happen;
- whether an official warning will be issued. NWS issues warnings; DEWS does not.

So every alert and advisory reads like *"Onion Creek near Driftwood: forecast to reach MINOR flood stage (5.5 ft)
within 3 h, probability 79%"* and never *"a flood will occur"*. The agents' reviewer rejects drafts with certainty
phrases such as "will flood".

## Kafka as used here

Apache Kafka is a distributed log. Producers append records to **topics**; consumers read them at their own pace.
DEWS runs three brokers (Docker, KRaft mode, no ZooKeeper).

### Topics

| Topic | Written by | Read by | Content |
|---|---|---|---|
| `gauge.usgs` | live USGS producer | Faust, sink | one record per new reading, plus a heartbeat per poll |
| `rain.mrms` | MRMS producer (planned, S2) | Faust | rain over a 10 km circle around each gauge |
| `replay.gauge`, `replay.rain` | replay producer | Faust, sink | historical readings, always `mode = SIMULATION` |
| `aggregates.window` | Faust | forecaster, sink | one hourly window per gauge |
| `forecasts.stage` | forecaster | sink | every forecast |
| `alerts.flood` | forecaster | sink, agents | only when a forecast reaches an official category |
| `alerts.approved` | console, after a person clicks Approve | notifier | the approved advisory text |
| `deadletter.flood` | any stage | (inspected by hand) | records that failed to parse or send |

Record fields are fixed in `docs/CONTRACTS.md`. Every record is JSON and carries `mode` (`LIVE` or `SIMULATION`).
Replay has its own topics, so a replayed flood can never touch live window state.

### Partitions and keys

Every topic has **3 partitions** and **replication factor 3** with `min.insync.replicas = 2`. One broker can stop and
the topics stay writable. Every record is keyed by `site_no`, the USGS site number. Kafka sends the same key to the
same partition, so each gauge's readings stay in order and one worker owns that gauge's window state.

### Consumer groups

A consumer group is a set of processes that share a topic's partitions. Each partition goes to one member, and the
group's committed offsets record how far it has read.

| Group | Process | Members |
|---|---|---|
| `flood-windowing` | Faust app `streams/faust_app.py` | 3 workers, one per partition. Kill one and the other two take over its partition |
| `forecaster` | `python -m streams.forecaster` | 1 |
| `postgres-sink` | `python -m storage.sink` | 1. It commits offsets only after the PostgreSQL write |
| `agent` | `python -m agentic_ai.run` | 1 |
| `notifier` | `python -m alerting.notifier` | 1. It commits only after the email is sent and recorded |

**Lag** is how many records a group has not read yet. The console shows it per group. Power BI is not a consumer
group, because it reads PostgreSQL.

**Delivery guarantee:** the sink and notifier are *at least once*: after a crash a few records are read again. The
sink's writes are idempotent upserts, so a re-read overwrites the same rows. That makes it effectively once, which
`tools.check_sink` verifies (0 gaps, 0 duplicates after a kill). The notifier can re-send at most one email after a
crash between sending and committing.

## Faust and event-time windows

[Faust](https://github.com/faust-streaming/faust) (the `faust-streaming` fork) is a Python stream-processing library.
It re-implements the ideas of Kafka Streams (stream processors, state tables backed by changelog topics, consumer
groups) and talks to Kafka through `aiokafka`. It is not a wrapper around the Java library.

DEWS builds **hourly tumbling windows** per gauge. The logic is in `streams/windowing.py` as pure functions, unit
tested without a broker:

- **Event time:** a reading lands in the window of its own timestamp, not its arrival time. Because USGS sends hourly
  batches, a window closes when a reading for a later window arrives or, in live mode, when a producer heartbeat
  passes the window end plus a 90 min grace period.
- **Zero-fill:** a silent gauge still produces a window with `n_readings = 0` and the last stage carried forward, so
  "no data" is visible to the forecaster and the console.
- **Revisions:** provisional USGS values get revised. A late reading for a window that has already closed re-emits that
  window marked `revision = true`, for up to 3 windows back.
- **Fault tolerance:** window state lives in a Faust table backed by a changelog topic. A killed worker's partition
  moves to another worker, which rebuilds the state from the changelog.

Each window carries `stage_last`, `stage_min`, `stage_max`, `rise_rate_ft_per_h` and timestamps for the latency
trace. The ML training code uses the same window functions (`ml/backtest.py`), so offline and live features match.

## Forecast, alert, advisory, approval

1. The **forecaster** calls `ml.predict.predict()` on each closed window. Today that is a rise-rate extrapolation 3 h
   ahead with no probability. The trained model replaces it in S3, falling back to the baseline on any error.
2. If the forecast reaches a category above the gauge's current one, it raises an **alert**. All alerts in one rise
   form an *episode*: `new` for the first category, `upgrade` when a higher one is forecast later, and `retract` when
   the forecast falls back before the crossing. A retraction is recorded, not emailed.
3. The sink stores the alert and writes **advisory v1**, a fixed template in which every number comes from the alert.
4. The **agents** may then write **v2**: checked, plain-language, with rain and neighbouring gauges as context. If they
   fail, v1 stays.
5. A **person** approves, edits or rejects in the console. Only Approve publishes to `alerts.approved`.
6. The **notifier** emails exactly the approved text and records `delivered_at`.

## Live vs SIMULATION, and what counts as a result

- `LIVE` means real gauges right now. Usually it is dry, so live mode shows that the pipeline runs and how fast it is,
  not detection skill.
- `SIMULATION` covers replayed history, the seeded agent alert and synthetic India data. Replays show floods as they
  unfolded and are used for evaluation on held-out years.
- Results are reported per mode and never merged. Detection is scored per event (precision, recall, F1, false
  alarms, lead time), never by accuracy, because flood readings are 0.05 to 0.3% of all readings.
  [06 ML and evaluation](06-ml-and-evaluation.md) has the details.
