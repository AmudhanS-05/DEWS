# Project Log — Real-Time Natural Disaster Early Warning Using Apache Kafka

Purpose: a running, dated record of what was built, decided, tested, and measured.
This is the raw source for the Phase I review, Phase II demo slides, and the final
technical paper — write entries as you go, not retroactively.

Key dates (from the SPA Lab circular, AI372IA):
- Phase I Review: **5 Oct – 8 Oct 2026**
- Phase II Demo + technical paper submission: **19 Nov – 26 Nov 2026**
- Lab CIE (updated paper due): **14 Dec – 18 Dec 2026**

---

## How to use this file
Each entry: date, what changed, why, what's next, any numbers worth keeping
(latency, throughput, accuracy) for the evaluation section of the report.

---

## 2026-09-27 — Project kickoff

**Decisions**
- Repo scaffolded: `producers/`, `streams/`, `ml/`, `storage/`, `dashboard/`,
  `agentic_ai/`, `docker/`, `docs/`.
- Stream-processing layer will use **Faust** (Python) to mirror Kafka Streams
  concepts (tables, tumbling/hopping windows) while staying in Python end-to-end,
  matching the CIE program list (word count, stock stats, threshold alerts) and the
  already-validated Kafka local setup (native KRaft, `localhost:9092`).
  Alternative considered: true Kafka Streams (Java/Kotlin) — rejected for now to
  keep one language across the pipeline; can be swapped in later if the SPA
  rubric specifically wants Java Kafka Streams DSL.
- ML stage: **Isolation Forest** (scikit-learn) as the anomaly-detection baseline —
  unsupervised, no labeled disaster data needed, cheap to retrain, used by multiple
  reference repos (see `docs/references.md`).
- Storage: PostgreSQL, plain (TimescaleDB extension optional later for time-series
  query performance — flagged as a stretch goal).
- Dashboard: Power BI is the brief's target, but Power BI Desktop needs Windows +
  a live ODBC/Direct Query connection to Postgres — it can't run inside this Linux
  workspace. Plan: build a lightweight local prototype (Streamlit or Grafana) here
  for demo/dev, and connect the *same* Postgres tables to Power BI Desktop on the
  student's own Windows machine for the actual submission. Documented in
  `dashboard/README.md`.
- Sequencing: **SPA pipeline first (Producer → Kafka → Streams → ML → Postgres →
  Dashboard) is the critical path.** Agentic AI layer is scoped as a consumer of
  the *output* of that pipeline (alerts/risk scores table), so it is a phase-2,
  additive layer, not something that can start productively before there is real
  data flowing. Rationale + phasing detail written up separately for the user.

**Open questions**
- [ ] Confirm with faculty whether "Kafka Streams" in the rubric requires the
      Java Kafka Streams library specifically, or accepts an equivalent
      Python stream-processing library (Faust) — affects `streams/` implementation.
- [ ] Confirm whether the 10 numbered "List of Programs for CIE" in the circular
      are separate individual-submission exercises distinct from this group
      project (currently assumed yes — see `docs/architecture.md` note).
- [ ] Pick real sensor variables to simulate: rainfall, temperature, wind speed,
      humidity, water level — need at least 2 Kafka topics (e.g. `sensors.weather`,
      `sensors.water-level`) per the "at least two Kafka topics" requirement.

**Next**
- Build `producers/sensor_simulator.py` (JSON messages, 2 topics, partition key
  by station/sensor id).
- Stand up docker-compose Kafka + Postgres locally and confirm topics visible.

---

## 2026-09-27 — Repo placed on machine, found existing prior work

**Done**
- Repo created at `Documents\RVCE\SPA_AAI\disaster-warn-kafka`, git initialized,
  initial commit made (`d22912d`).
- Named `disaster-warn-kafka` for now — student flagged this may be renamed
  once the specific event types/data source are frozen.

**Found while setting up (not yet reconciled — flagging for next session)**
- `Documents\RVCE\SPA_AAI\LabPrograms\` already contains all 10 items from the
  circular's "List of Programs for CIE" (program_1a through program_10),
  confirming these are separate individual deliverables from this group
  project, as assumed in `docs/architecture.md`.
- `Documents\RVCE\SPA_AAI\kafka-disaster-warning\` is an earlier, mostly-empty
  attempt at this same project topic — contains only a `docker-compose.yml`
  using **InfluxDB + Grafana** instead of **PostgreSQL + Power BI** (the brief
  asks for the latter). No producer/streams/ML code in it. Left untouched;
  worth a quick look before fully discarding in case any config is worth
  reusing, but the brief's required stack (Postgres + Power BI) is what this
  new repo builds toward.
- A `spaenv\` Python virtualenv already exists in the same parent folder with
  `confluent-kafka` installed — can likely be reused instead of creating a new
  venv for this repo (confirm Python version compatibility with `streams/`
  and `ml/` requirements before relying on it for those).

**Next**
- Decide whether to fold anything from `kafka-disaster-warning/` in, or leave
  it as an abandoned first draft.
- Activate `spaenv` and try installing this repo's requirements into it.

## 2026-09-28 — Pushback resolved: Kafka-Streams compliance, ML choice, thresholds

Full detail + citations in the "Architecture & Open Decisions" project doc
(claude.ai project docs). Summary of what changed:

- **Faust vs "must use Kafka"**: checked the circular's actual text — it
  requires "Apache Kafka must be the central messaging/streaming platform"
  and "at least one window-based streaming operation," not the Java Kafka
  Streams library by name. Faust connects to the same real Kafka broker via
  Kafka's wire protocol and its windows are changelog-topic-backed, same
  fault-tolerance mechanism Kafka Streams itself uses — compliant. Still
  confirming with faculty whether the rubric additionally wants the literal
  Java library; if so, `streams/` swaps to Java/Kotlin with no change
  downstream.
- **ML method**: keeping Isolation Forest but explicitly as one signal, not
  the disaster label — compared against Z-score/domain-threshold, One-Class
  SVM, LSTM Autoencoder, LOF, and STA/LTA (seismology-specific). Current
  `contamination=0.05` in `ml/anomaly_consumer.py` is a placeholder and must
  be replaced by a sweep calibrated against domain-labeled events, not left
  as a guess.
- **Thresholds — real citable sources found**, replacing any invented
  numbers: IMD rainfall-intensity classification, IMD cyclone wind-speed
  classification, CWC per-station Warning/Danger Level (or NOAA NWPS API's
  per-gauge flood category levels, more directly usable in code), STA/LTA
  ratio trigger for seismic (Journal of the Geological Society of India,
  2018). IMD figures sourced from a social-media repost of IMD's own
  terminology — **flagged to verify against IMD's primary PDF/circular
  before citing in the actual technical paper.**
- **Dataset sufficiency**: Open-Meteo + USGS water services satisfy the
  multi-topic/multi-stream requirement for the brief's listed variables.
  Earthquake is NOT covered by either — would need the USGS Earthquake
  Catalog real-time GeoJSON feed as its own topic (`sensors.seismic`).
  Live APIs alone won't give enough history to characterize "normal" or
  validate against real past disasters — a historical-data backfill
  (IMD/NOAA/USGS/CWC archives, replayed at accelerated speed) is needed
  alongside live polling.

**Next**: decide real vs. synthetic dataset (comment open on the doc),
then revisit `ml/anomaly_consumer.py`'s thresholds once that's settled.

## Template for new entries

```
## YYYY-MM-DD — <short title>

**Done**
-

**Decisions**
-

**Numbers** (latency / throughput / accuracy / false-alarm rate, etc.)
-

**Blockers**
-

**Next**
-
```
