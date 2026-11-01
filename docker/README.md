# Local stack

Two options. Run only one at a time (both bind 9092 and 5432).

| File | Brokers | Use when | Fault-tolerance claim allowed |
|---|---|---|---|
| `docker-compose.yml` | 3 (KRaft, RF 3, min ISR 2) | normal development and the demo | broker failure survives (kill one broker) |
| `docker-compose.single.yml` | 1 | laptop can't run 3 brokers | none at broker level |

## Start (PowerShell)

```powershell
cd docker
docker compose up -d
docker compose ps                      # kafka-1/2/3 and postgres "healthy"
docker compose logs topic-init         # shows the 9 topics, 3 partitions, ReplicationFactor: 3
```

On Windows the brokers can take 60–130 s to become healthy (see the reproducibility
guide); the healthcheck allows for that.

Single-broker fallback:

```powershell
docker compose -f docker-compose.single.yml up -d
$env:KAFKA_BOOTSTRAP="127.0.0.1:9092"; $env:TOPIC_REPLICATION_FACTOR="1"
```

## Checks

1. `docker compose ps` → every service healthy, `topic-init` exited with code 0.
2. `docker compose logs topic-init` → `gauge.usgs`, `rain.mrms`, `replay.gauge`, `replay.rain`,
   `aggregates.window`, `forecasts.stage`, `alerts.flood`, `alerts.approved`, `deadletter.flood`, each `PartitionCount: 3`,
   `ReplicationFactor: 3`, `min.insync.replicas=2`.
3. `docker exec -it flood-postgres psql -U spa_user -d disaster_warning -c "\dt"` →
   14 tables (advisories, advisories_delivered, agent_events, agent_runs, alerts, catchment_rain, evaluation_runs, gauge_impacts,
   gauge_readings, gauges, latency_trace, past_floods, risk_scores, windowed_aggregates). `\dv` → 5 views
   (agent_run_summary, alert_overview, current_advisory, feed_staleness, latency_waterfall).
4. Broker failure smoke test: `docker stop flood-kafka-2`, then
   `docker exec flood-kafka-1 kafka-topics --bootstrap-server kafka-1:29092 --describe --topic gauge.usgs`
   → leaders move off broker 2, `Isr` shrinks to 2 members; `docker start flood-kafka-2`
   and the ISR returns to 3.

`storage/schema.sql` and `storage/views.sql` load automatically only on an empty volume (`docker compose down -v` wipes
data). To change a running database, apply them by hand:
`Get-Content storageiews.sql -Raw | docker exec -i flood-postgres psql -U spa_user -d disaster_warning`.
