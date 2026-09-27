# Real-Time Natural Disaster Early Warning Using Apache Kafka

Batch 1 – Topic 2. Joint project for:
- **AI372IA – Stream Processing and Analytics** (SPA)
- **AI373TA – Agentic Artificial Intelligence**

## Pipeline (per project brief)

```
Weather / Environmental Sensors
        │
        ▼
   Kafka Producer(s)
        │
        ▼
   Kafka Topics (partitioned)
        │
        ▼
   Kafka Streams (windowing + aggregation)
        │
        ▼
   ML-Based Anomaly / Event Detection
        │
        ▼
   PostgreSQL (observations, risk scores, alerts)
        │
        ▼
   Power BI Dashboard / Early-Warning Alerts
        │
        ▼
   Agentic AI layer (Phase 2): reasoning + action over the alerts
```

## Repo layout

| Folder | Purpose | Course |
|---|---|---|
| `producers/` | Sensor simulators, Kafka producers (JSON, acks/retries, transactional) | SPA |
| `streams/` | Kafka Streams-equivalent app: windowing, aggregation, stateful/stateless transforms | SPA |
| `ml/` | Anomaly/event detection model (Isolation Forest baseline) | SPA |
| `storage/` | PostgreSQL schema + write path | SPA |
| `dashboard/` | Real-time dashboard prototype + Power BI connection notes | SPA |
| `agentic_ai/` | Agent(s) that consume alerts/risk scores and act (Phase 2) | Agentic AI |
| `docker/` | Local Kafka (KRaft) + PostgreSQL compose stack | Both |
| `docs/` | Architecture notes, reference repos, evaluation plan | Both |
| `PROJECT_LOG.md` | Running log of decisions/dates/metrics — source for PPTs + final report | Both |

## Quick start (Windows / PowerShell)

See `docker/README.md` for the Docker Compose flow, or `docs/kafka-setup-reference.md`
which points back to the reproducibility guide already validated on this machine
(`claude/Kafka-Local-Setup-Reproducibility-Guide.md` in the project docs).

```powershell
cd docker
docker compose up -d
docker compose ps        # wait for "healthy"
```

Then, in separate terminals (Python venv active):
```powershell
pip install -r producers/requirements.txt -r streams/requirements.txt -r ml/requirements.txt
python producers/sensor_simulator.py
python streams/windowing_app.py
python ml/anomaly_consumer.py
```

## Status

Tracked in `PROJECT_LOG.md`. Update it after every working session — it is the
raw material for the Phase I / Phase II slide decks and the final technical paper.
