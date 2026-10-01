# Local stack (Phase 1)

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
docker compose logs topic-init         # shows the 6 topics, 3 partitions, ReplicationFactor: 3
```

On Windows the brokers can take 60–130 s to become healthy (see the reproducibility
guide); the healthcheck allows for that.

Single-broker fallback:

```powershell
docker compose -f docker-compose.single.yml up -d
$env:KAFKA_BOOTSTRAP="127.0.0.1:9092"; $env:TOPIC_REPLICATION_FACTOR="1"
```

## Phase 1 tests

1. `docker compose ps` → every service healthy, `topic-init` exited with code 0.
2. `docker compose logs topic-init` → `gauge.usgs`, `rain.mrms`, `aggregates.window`,
   `alerts.flood`, `advisories.approved`, `deadletter.flood`, each `PartitionCount: 3`,
   `ReplicationFactor: 3`, `min.insync.replicas=2`.
3. `docker exec -it flood-postgres psql -U spa_user -d disaster_warning -c "\dt"` →
   8 tables (alerts, catchment_rain, evaluation_runs, gauge_readings, gauges,
   latency_trace, risk_scores, windowed_aggregates).
4. Broker failure smoke test: `docker stop flood-kafka-2`, then
   `docker exec flood-kafka-1 kafka-topics --bootstrap-server kafka-1:29092 --describe --topic gauge.usgs`
   → leaders move off broker 2, `Isr` shrinks to 2 members; `docker start flood-kafka-2`
   and the ISR returns to 3.

Schema changes only load on an empty volume: `docker compose down -v` wipes data.
