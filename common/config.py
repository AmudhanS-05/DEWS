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
TOPIC_ADVISORIES = "advisories.approved"  # human-approved advisories -> notifier
TOPIC_DLQ = "deadletter.flood"        # records that failed parsing/processing

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
