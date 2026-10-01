# Architecture notes (rev 3, proposed — floods and flash floods)

The full design (scope, data sources with verification status, prediction design,
latency, notification audience, fault tolerance, gate checks, limitations, open
faculty items) is in the Claude Doc "Architecture — Floods & Flash Floods (rev 3,
proposed)", with the pipeline diagram and real sample records on its second tab.
This file maps that design to the code.

## Methodology step → code

| Brief step | Implementation | File(s) |
|---|---|---|
| 1. Streaming variables | gage height (00065), discharge (00060), catchment rainfall (MRMS, Phase 2b) | `common/usgs.py` |
| 2. Producers, separate topics | `gauge.usgs`, `rain.mrms` (+ `aggregates.window`, `alerts.flood`, `advisories.approved`, `deadletter.flood`) | `producers/`, `docker/docker-compose.yml` |
| 3. Partitions + consumer groups | 3 partitions, key = `site_no`; groups: flood-windowing (Faust), forecaster, agent, notifier, dashboard | `streams/faust_app.py` |
| 4. Event-time windows + aggregation | hourly tumbling windows on the reading's own timestamp; zero-fill; late revisions | `streams/windowing.py` |
| 5. ML / prediction | persistence and rise-rate baselines; ML model chosen after baseline numbers | `ml/` |
| 6. PostgreSQL | readings, windows, scores, alerts, latency trace, evaluation | `storage/schema.sql` |
| 7. Dashboard + alerts | Power BI (DirectQuery) + human-approved notifier | Phase 6–7 |
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
- **Every replayed record and alert carries `mode = SIMULATION`.**
- **3 brokers, RF 3, min ISR 2** is the default stack; the single-broker fallback exists,
  and with it we do not claim broker-level fault tolerance.
