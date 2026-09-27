# Architecture Notes

## Pipeline mapped to methodology steps

| Step | Component | File(s) |
|---|---|---|
| 1. Identify streaming variables | rainfall, temperature, wind_speed, humidity, water_level | `producers/sensor_simulator.py` |
| 2. Publish via producers, separate topics | `sensors.weather`, `sensors.water-level` | `producers/sensor_simulator.py` |
| 3. Partitioning + consumer groups | 3 partitions/topic, keyed by `station_id`; Faust worker group | `docker/README.md`, `streams/windowing_app.py` |
| 4. Event-time windows + aggregation | 10s tumbling window, min/max/avg/count | `streams/windowing_app.py` |
| 5. ML anomaly/event detection | Isolation Forest per sensor type | `ml/train_isolation_forest.py`, `ml/anomaly_consumer.py` |
| 6. Store in PostgreSQL | `sensor_readings`, `windowed_aggregates`, `risk_scores`, `alerts` | `storage/schema.sql` |
| 7. Real-time dashboard / alerts | Streamlit prototype + Power BI (submission) | `dashboard/` |
| 8. Evaluation | precision/recall/F1/false-alarm/latency/throughput/recovery | `storage/schema.sql` (`evaluation_runs`), see below |

## Data flow (Kafka topics)

```
sensors.weather ──┐
                   ├─▶ windowing_app.py (Faust) ──▶ aggregates.window ──▶ anomaly_consumer.py ──▶ Postgres
sensors.water-level┘                          └──▶ Postgres (windowed_aggregates, direct)
```

## Fault tolerance / recovery (explicit rubric item)

Three layers, each independently demonstrable in the Phase II demo:
1. **Kafka level**: kill and restart a Faust worker mid-stream — Faust's
   changelog-topic-backed tables mean in-flight window state survives.
2. **Consumer level**: `anomaly_consumer.py` uses manual offset commits, only
   committing after a successful Postgres write — kill it mid-write and
   restart; the un-committed message replays (at-least-once).
3. **DB level**: `write_aggregate_to_postgres` retries with backoff if Postgres
   is briefly unreachable (e.g. `docker stop lab-postgres` for a few seconds,
   then `docker start lab-postgres`) instead of crashing the stream app.

For the report: measure and record **recovery_time_s** for each of these three
kill/restart scenarios into `evaluation_runs`.

## Evaluation plan (Step 8 / Section D of the brief)

| Metric | How to measure |
|---|---|
| Detection precision/recall/F1 | Inject known synthetic spikes (already built into `sensor_simulator.py` via the `random.choice([0,0,0,spike])` pattern) at known timestamps; compare against `risk_scores.is_anomaly` |
| False-alarm rate | Fraction of `is_anomaly=true` rows where no synthetic spike was injected in that window |
| Detection latency | `alerts.raised_at - sensor_readings.event_time` for the triggering reading |
| Throughput | Messages/sec sustained by producer+consumer without growing consumer lag (`kafka-consumer-groups.bat --describe`) |
| Alert delivery time | `alerts.raised_at - risk_scores.computed_at` |
| Recovery time | Wall-clock time from killing a component to it resuming normal processing (see above) |

Record each run's numbers as a row in `evaluation_runs` (`run_label` = e.g.
`phase1-baseline`) — this table is exactly what the Phase II report's
"quantitative evaluation" section needs, and what the PPT's results slide
should chart.

## Open question: is the CIE program list a separate deliverable?

The circular lists 10 individual Kafka programs ("List of Programs for CIE")
*separately* from the "PROJECT BASED LEARNING" section describing this group
project. Current assumption: **these are separate, likely individual, lab
exercises** distinct from the group project, not something this repo needs to
produce — but several of them (async producer w/ callbacks, JSON producer/
consumer with threshold alerting, transactional producer, manual-offset
consumer, Kafka Streams word count / stock stats / IoT alerting) are exactly
the patterns reused inside this project's producer/streams/ML code. Confirm
with the faculty in charge; if they do need to be submitted individually too,
most of the code here can be lifted into small standalone scripts per item.
