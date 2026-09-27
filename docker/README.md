# Docker stack — Kafka (KRaft) + PostgreSQL

Single-node Kafka (KRaft mode, no Zookeeper) + Postgres, both healthchecked.
This is the "Method 1: Docker (KRaft)" pattern from the reproducibility guide,
plus a Postgres service with `storage/schema.sql` auto-applied on first boot.

## Start
```powershell
cd docker
docker compose up -d
docker compose ps      # wait until both services show "healthy"
```

## Create the two required topics
```powershell
docker exec lab-kafka kafka-topics --bootstrap-server localhost:9092 --create --topic sensors.weather --partitions 3 --replication-factor 1
docker exec lab-kafka kafka-topics --bootstrap-server localhost:9092 --create --topic sensors.water-level --partitions 3 --replication-factor 1
docker exec lab-kafka kafka-topics --bootstrap-server localhost:9092 --create --topic alerts.disaster --partitions 1 --replication-factor 1
docker exec lab-kafka kafka-topics --bootstrap-server localhost:9092 --list
```

## Connect to Postgres
```powershell
docker exec -it lab-postgres psql -U spa_user -d disaster_warning
```
Or from Power BI Desktop / any client: host `localhost`, port `5432`,
db `disaster_warning`, user `spa_user`, password `spa_pass` (dev only —
change before anything resembling a real deployment).

## Stop / reset
```powershell
docker compose down       # stop, keep data
docker compose down -v    # stop and wipe volumes (fresh start)
```

## Why not native Kafka here
The project's own reproducibility guide documents three working local Kafka
setups (Docker KRaft, native KRaft, native+Zookeeper). Docker is used here
because it also brings up Postgres in the same `docker compose up -d`, but the
Python producer/consumer code is identical against any of the three — see
that guide's "Cheat Sheet" section if Docker isn't available on exam day.
