# Architecture notes

This file maps the design to the code. The pipeline diagram is in the [README](../README.md#architecture) and in
[paper/01-method.md](paper/01-method.md); the record fields are in [CONTRACTS.md](CONTRACTS.md).

## Methodology step → code

| Step | Implementation | File(s) |
|---|---|---|
| 1. Streaming variables | gage height (00065), discharge (00060), catchment rainfall (MRMS) | `common/usgs.py`, `common/mrms.py` |
| 2. Producers, separate topics | 3 producers (USGS live, MRMS live, replay) → `gauge.usgs`, `rain.mrms`, `replay.gauge`, `replay.rain`; downstream `aggregates.window`, `alerts.flood`, `alerts.approved`, `forecasts.stage`, plus `deadletter.flood` (9 topics) | `producers/`, `docker/docker-compose.yml` |
| 3. Partitions + consumer groups | 3 partitions, key = `site_no`; groups: `flood-windowing` (Faust, 1 group × 3 instances), `forecaster`, `postgres-sink` (reads `aggregates.window` and `alerts.flood`), `agent`, `notifier`. Power BI reads PostgreSQL, so it is not a Kafka group | `streams/faust_app.py` |
| 4. Event-time windows + aggregation | hourly tumbling windows on the reading's own timestamp; zero-fill; late revisions | `streams/windowing.py` |
| 5. ML / prediction | persistence and rise-rate baselines; gradient-boosting model `s3_v0` active at 3 h, rise-rate otherwise | `ml/`, `streams/forecaster.py` |
| 6. PostgreSQL | readings, windows, scores, alerts, latency trace, evaluation | `storage/schema.sql` |
| 7. Dashboard + alerts | web console, Power BI (DirectQuery), human-approved notifier | `dashboard/`, `alerting/` |
| 8. Evaluation | precision/recall/F1, false alarms, lead time gross and net of feed delay, throughput, recovery | `ml/evaluate.py`, `ml/backtest.py` |

## Key design decisions (why)

- **Labels are official NWPS categories**, never thresholds we set.
- **Dedupe key includes the sensor series**: a real USGS response showed two series at
  the same timestamps with different values.
- **Hand-rolled event-time windows over a changelog-backed Faust table**: USGS sends
  hourly batches, silent gauges must still produce rows, and provisional values get
  revised. All window logic is pure and unit-tested; Faust provides partitioned,
  fault-tolerant state.
- **Heartbeats from the producer** drive wall-clock window closing and zero-fill, so the
  stream app never has to write state outside stream processing.
- **Backtest uses the same window code as the stream**, so offline and live features match.
- **Every replayed record and alert carries `mode = SIMULATION`**, and replay has its own topics (`replay.gauge`, `replay.rain`) and its own Faust state table, so it never mixes with live data.
- **Notifier channel: email + log** (team mailbox now; a county emergency-management channel is future work).
- **3 brokers, RF 3, min ISR 2** is the default stack; the single-broker fallback exists,
  and with it we do not claim broker-level fault tolerance.
