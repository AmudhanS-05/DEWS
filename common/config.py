"""Shared settings. Everything can be overridden with environment variables so the
same code runs against the 3-broker cluster, a single broker, or a test setup."""
import os

# Kafka. Default = the 3-broker compose stack in docker/docker-compose.yml.
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9093,127.0.0.1:9094")
# Replication factor for topics Faust creates itself (changelogs). 3 for the
# 3-broker stack, 1 for docker-compose.single.yml.
TOPIC_REPLICATION_FACTOR = int(os.getenv("TOPIC_REPLICATION_FACTOR", "3"))
TOPIC_PARTITIONS = int(os.getenv("TOPIC_PARTITIONS", "3"))

TOPIC_GAUGE = "gauge.usgs"            # raw gauge readings, key = site_no
TOPIC_RAIN = "rain.mrms"              # catchment rainfall, key = site_no (phase 2b)
TOPIC_AGG = "aggregates.window"       # windowed features, key = site_no
TOPIC_ALERTS = "alerts.flood"         # forecaster output (drafts, never sent directly)
TOPIC_APPROVED = "alerts.approved"    # human-approved advisories -> notifier (the only topic it reads)
TOPIC_REPLAY_GAUGE = "replay.gauge"   # historical gauge replay (SIMULATION), kept apart from live data
TOPIC_REPLAY_RAIN = "replay.rain"     # historical rainfall replay (SIMULATION; with the MRMS producer, phase 2b)
TOPIC_DLQ = "deadletter.flood"        # records that failed parsing/processing

# Every topic the stack creates (docker/*.yml must list the same names)
ALL_TOPICS = [TOPIC_GAUGE, TOPIC_RAIN, TOPIC_REPLAY_GAUGE, TOPIC_REPLAY_RAIN, TOPIC_AGG,
              TOPIC_ALERTS, TOPIC_APPROVED, TOPIC_DLQ]

# Consumer groups (one per consuming component; Faust's app id is the windowing group)
GROUP_WINDOWING = "flood-windowing"   # Faust, 3 instances = 3 partitions
GROUP_FORECASTER = "forecaster"
GROUP_SINK = "postgres-sink"          # reads aggregates.window and alerts.flood
GROUP_AGENT = "agent"
GROUP_NOTIFIER = "notifier"

# PostgreSQL (matches docker/docker-compose.yml)
PG_DSN = os.getenv("PG_DSN", "postgresql://spa_user:spa_pass@127.0.0.1:5432/disaster_warning")

# External data services
USGS_IV_URL = os.getenv("USGS_IV_URL", "https://waterservices.usgs.gov/nwis/iv/")
NWPS_GAUGE_URL = os.getenv("NWPS_GAUGE_URL", "https://api.water.noaa.gov/nwps/v1/gauges/{lid}")
HTTP_TIMEOUT_S = float(os.getenv("HTTP_TIMEOUT_S", "30"))
USER_AGENT = os.getenv("USER_AGENT", "rvce-flood-warning-student-project")

# Gauge parameter codes (USGS): 00065 = gage height (ft), 00060 = discharge (cfs)
PARAM_STAGE = "00065"
PARAM_DISCHARGE = "00060"

# Windowing
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "3600"))

# Local folders (git-ignored)
DATA_DIR = os.getenv("DATA_DIR", "data")
STATE_DIR = os.getenv("STATE_DIR", "state")
GATE_OUTPUT_DIR = os.getenv("GATE_OUTPUT_DIR", "gate_output")
