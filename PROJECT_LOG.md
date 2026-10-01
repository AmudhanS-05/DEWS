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

## 2026-09-28 — Disaster focus recommended: flood & severe-weather

Full reasoning + tables in the "Architecture & Open Decisions" project doc.
Summary:

- **Recommended:** flood/severe-weather early warning, anchored on
  **water level** as the primary disaster (official per-gauge threshold via
  NOAA NWPS API, decades of USGS historical data). Rainfall/wind/temp/
  humidity are correlated multivariate features feeding the Isolation Forest,
  NOT independently threshold-backed — checked NWS's own Flash Flood Warning
  criteria directly and confirmed there's no single national rainfall-rate
  number; flash flood warnings are forecaster/model judgment calls. Selling
  a fixed rainfall citation would have been the "inventing thresholds"
  problem, just with extra steps — corrected before it became one.
- **Ruled out as headline:** cyclone/severe wind (real severe conditions too
  rare to reliably demo live) and earthquake (USGS's feed is a pre-detected
  event catalog, not a raw sensor stream — building "our own" anomaly
  detector on top of it is circular; real raw waveform access is a heavier
  lift than the timeline supports). Earthquake noted as a one-paragraph
  future-work mention only.
- **Producer/consumer/topic count set:** 2 producers (weather, river), 4
  topics, 3 consumer groups (windowing/Faust, ML anomaly, raw archival) —
  deliberately more than the circular's stated minimums so partitioning and
  consumer-group rebalancing can be *shown* live (two workers in the same
  group during the demo), not just claimed.
- **Demo reliability:** live real data + a disclosed synthetic fault-injection
  mode so an alert can be triggered on demand during Phase I/II review,
  rather than depending on a real flood happening that week.

**Next**: build `weather_producer.py` / `river_producer.py` against
Open-Meteo + USGS/NOAA NWPS, replacing the current single synthetic
`sensor_simulator.py`.

## 2026-09-28 (later) — Multi-hazard revision: real data first, seismic added

User confirmed: Faust is fine (no Java Kafka Streams swap needed), open to
variables beyond the brief's original five, and wants real data as the
default with synthetic only as a last-resort fallback. Full reasoning +
tables in the project doc. Summary:

- **Added seismic (earthquake frequency/magnitude) as a second real stream**,
  specifically because it's the one variable where a genuine, officially
  labeled anomaly is available essentially every day, regardless of local
  weather — USGS's own magnitude classification (Great/Major/Strong/
  Moderate/Light/Minor) is the ground truth, no invention needed. USGS
  confirms ~16 magnitude-7+ ("major") quakes/year (directly quoted from
  their FAQ); a ~14,000/year figure for magnitude-4+ came from a secondary
  source this round and needs primary-source verification before citing in
  the paper.
- **Air Quality Index (CPCB, India) flagged as an optional third stream** —
  official government breakpoints, and Oct-Nov is stubble-burning/Diwali
  smog season in North India, so real "Poor"-to-"Severe" AQI readings are
  a well-documented annual pattern in exactly this project's window.
- **Flood/water-level stays the anchor** (strongest single citable
  threshold, and the brief names it first) but is no longer the whole
  story — a real breach at one specific gauge on one specific demo day
  isn't guaranteed. Noted the NE/retreating monsoon does genuinely affect
  Karnataka/Tamil Nadu Oct-Nov, so it's plausible, just not certain.
- **Updated component count**: 3 producers (weather, river, seismic), 5
  topics, same 3-consumer-group pattern extended to the new stream.
- **Synthetic fault injection demoted to fallback-only** — used only if, on
  the actual review day, none of the live streams show a real anomaly.

**Next**: build the three real producers (`weather_producer.py`,
`river_producer.py`, `seismic_producer.py`) against Open-Meteo, USGS/NOAA,
and the USGS earthquake GeoJSON feed respectively.

## 2026-09-28 (later still) — FINAL pivot: pure seismic, event-feed level

Teammate shared a rigorous design proposal for raw-waveform seismic
detection (EarthScope SeedLink + ObsPy + TauP + Java Kafka Streams).
Methodologically excellent — several ideas adopted — but too heavy to
attempt as Phase I scope with ~1 week to review and no prior experience
with any of that tooling. Decision, confirmed with the team:

- **Earthquake becomes the single headline disaster** (not multi-hazard),
  built on the **USGS event-feed level** — magnitude/location/depth/time,
  already detected by USGS — not raw seismometer waveforms. Raw-waveform
  detection is an explicit **Phase 2 stretch goal**, not dropped.
- **Faust stays.** Confirmed again: the circular doesn't name Java Kafka
  Streams specifically.
- **Fixed a circularity problem**: if we just re-alert on "USGS reported a
  big earthquake," we're not detecting anything, USGS already did. Fix —
  two layers: (1) rule-based severity alert on individual event magnitude
  vs. USGS's own official classification — a lookup, not ML; (2) the
  actual ML contribution — Isolation Forest on **windowed rate/clustering
  features** (event count, cumulative magnitude/energy, mean depth per
  region per window), trained on normal background seismicity, detecting
  swarm-like clustering that the raw feed never labels as a single value.
  Non-circular because it's a new aggregate signal from our own windowing,
  not a re-read of USGS's own label.
- **Ground truth for the ML layer**: real historical sequences — the 2019
  Ridgecrest, CA sequence (from the teammate's own suggestion) as a real
  positive-class stress test, no raw waveform needed since it's fully in
  the USGS catalog.
- **Region**: pick a globally active region (Southern California, Japan,
  Indonesia) over India specifically, purely for real-event frequency.
- **Architecture**: 2 producers (live USGS poll + historical replay,
  both real data), 3 topics (`catalog.usgs`, `aggregates.window`,
  `alerts.seismic`), 3 consumer groups (Faust windowing, ML anomaly,
  raw archival) — same shape as the flood design, just re-pointed.
- **Adopted from the teammate's proposal regardless of architecture**:
  honest ML-vs-baseline comparison, false-alarm rate stated as an upper
  bound, three separate latency metrics (pipeline/detection/delivery) —
  add a `latency_trace` table, and the specific fault-tolerance mechanisms
  (replication factor 3 + min ISR 2, changelog standby replicas,
  sequence/offset resume with backfill).
- **Not carried over**: multi-station waveform corroboration — doesn't
  apply once USGS has already fused multi-station data into one event
  record; noted explicitly so nobody claims we're doing something we're
  not.
- **Still to do**: get faculty to confirm "other available environmental
  indicators" covers seismic, in writing, before building further —
  teammate's advice, good regardless of which version we build.

This supersedes the flood-anchored and multi-hazard entries above, which
stay as the record of how the decision evolved.

## 2026-09-28 — Detection design simplified to single-layer ML; teammate review incorporated

**Detection-layer question resolved**: dropped the separate rule-based
magnitude-lookup layer. Isolation Forest alone is sufficient because
magnitude enters as a *feature* (`max_magnitude_in_window`,
`cumulative_magnitude`) alongside `event_count`/`mean_depth`/
`magnitude_std`, not as a hardcoded threshold — a single large event and a
dense aftershock sequence both produce short isolation paths on different
features. This also removes the redundant threshold-provenance problem the
rule-based layer reintroduced. The USGS magnitude-class name is still
attached to alerts, but only as human-readable labeling, not as a second
detection gate, and its "accuracy" is never reported as detection accuracy.

**Teammate's second review (attached, full technical critique) accepted
almost in full** and folded into the architecture doc (rev 2):
- **Zero-count windows**: Faust/streams only emit non-empty windows by
  default — fixed by an explicit heartbeat/timer that emits zero-count
  windows too, so background rate isn't biased upward. Same handling used
  for training and live.
- **Event-time messiness**: dedupe producer events by ID; track USGS's
  `status` (automatic/reviewed) and `updated_at`; explicit lateness
  allowance on windows, with re-emission on late/revised events.
- **Live vs. replay must be reported separately** — replay is the final
  revised catalog, live is preliminary; merging them would misrepresent
  results.
- **Ground-truth circularity, round 2**: labeling "sequence" periods by
  high event count (the same quantity used as a feature) still scores the
  detector against itself. Fixed by using documented mainshock-aftershock
  sequences (e.g. Ridgecrest — corrected terminology, it's a
  mainshock-aftershock sequence, not a "swarm") as independent labels, and
  masking the mainshock's own magnitude when evaluating whether the ML
  layer adds value beyond a trivial magnitude check.
- **"Early warning" framing corrected**: a catalog-fed pipeline alerts
  after USGS has already detected and published an event — it cannot give
  advance notice. Renamed throughout to "seismic activity monitoring and
  alerting"; flagged as an open item to raise with faculty rather than
  glossed over in the report.
- **Missing alert delivery mechanism**: added a 4th consumer group
  (Notifier → Telegram/email) — without a real delivery channel,
  "alert delivery time" had nothing to measure.
- **Window size**: 10s (leftover from the flood design) was meaningless on
  a sparse catalog — changed to 1-hour tumbling windows per grid cell;
  replay must run accelerated to show several in a demo.
- **Partitioning weak on a single named region** — switched to lat/lon
  grid-cell keys for a real multi-key partition/rebalance demo.
- **Backfill**: added `updatedafter`-based resume (to confirm against
  current USGS/FDSN feed docs) as the catalog equivalent of SeedLink's
  sequence-number resume.
- **Broker fault tolerance**: RF3/min-ISR2 requires an actual 3-broker
  cluster — noted not to claim broker-level recovery against a
  single-broker demo setup.
- **Stronger baseline**: added a Poisson-quantile test per grid cell as
  the honest comparison point, not just "count above a fixed number."
- **`faust-streaming`** (maintained fork) to be used instead of upstream
  `faust` — smoke-test this week.
- Added a second open item beyond the existing "Kafka Streams" wording
  risk: confirm the brief's "early-warning" wording against what this
  system honestly does.

Full updated architecture (all of the above applied) is in the separate
review-only Claude Docs artifact, not duplicated here in full —
see the doc link in that conversation turn.

**Next**: schema changes for seismic (event-based fields, grid_cell,
dedupe keys, `latency_trace`), then `seismic_live_producer.py` +
`seismic_replay_producer.py` against the USGS feed, with the zero-count
heartbeat and dedupe/backfill logic built in from the start rather than
retrofitted.

## 2026-09-29 to 2026-10-01 — Pivot to floods / flash floods; Phases 0–4 (offline) built

**Why we moved off earthquakes**
- A catalog-fed seismic pipeline can only alert after USGS has published an event; it
  cannot forecast, so the brief's "early warning" and the requested move "from
  detection to prediction" could not be met honestly.
- Options explored and set aside: wildfire fusion (feed latency ~30 min, pixel size;
  geostationary API access unverified), stubble burning / AQI (not a natural disaster
  without faculty approval), landslides and GLOFs (too little timely labelled data:
  e.g. NASA GLC not updated after 2018; HMA GLOF database has exact days for 39% of events).
- Floods fit all six requested updates: prediction is natural (forecast gauge stage vs
  official NWPS flood categories), latency is measurable, labels are external.

**Decisions**
- Scope: riverine floods and flash floods at gauge level, US live data (USGS IV,
  NWPS categories, MRMS rainfall); India as a retrospective test only (CAMELS-IND, daily,
  1980–2020; no open real-time Indian feed found).
- Never claim a flood "will occur": forecast a category crossing with horizon and
  probability. No threshold proves occurrence (WMO: FFG signals "potential flash flooding").
- Not claimed: better forecasts than NSSL FLASH, GloFAS, NWS or Google. Claimed
  contributions: guardrailed agent layer with human approval, measured end-to-end
  latency including the external feed, fault-tolerance evidence, and (novelty
  unchecked) a sparse-gauge / US-to-India transfer experiment.
- Notification: named responders only; agent drafts, a person approves; probabilistic
  wording; provisional-data flag; retraction path; cooldown; SIMULATION stamp on replays.
- Windowing: hand-rolled event-time hourly windows over a changelog-backed Faust table
  (zero-fill, late revisions); producer heartbeats close windows by wall clock.
- Dedupe key includes the sensor series (two series seen at the same timestamps).
- Infra: 3-broker KRaft (RF 3, min ISR 2) by default; single-broker fallback without
  the broker fault-tolerance claim.
- Old synthetic weather simulator, old windowing app, Isolation Forest scripts and the
  weather Streamlit prototype removed (git history keeps them).

**Done (commits)**
- `125e68e` Phase 0: gate check (events per gauge, rise times, kill rules; network
  errors = ERROR, not FAIL), staleness logger, real-sample fetcher, USGS/NWPS parsers.
- `9423630` Phase 1: 3-broker compose + topic init, single-broker fallback, flood schema
  (loads on PostgreSQL 16).
- `ebcda0c` Phases 2–3: live and replay producers, dedupe/envelope, event-time windowing,
  Faust app, topic peek tool.
- `0b1b89c` Phase 4 (offline): baselines, event-based metrics, backtest.
- Docs: `docs/BUILD_PHASES.md`, `docs/MANUAL_CHECKS.md`, `BREADCRUMBS.md`, README.

**Numbers**
- Feed observation (one sample, 30 Sep): newest readings ~1 h and ~1.5 h old at two
  gauges; readings every 15 and 30 min; all qualifier P (provisional).
- Tests: 32 passing, 2 skipped (they need real responses saved on a networked machine).

**Blockers**
- The build workspace cannot reach USGS/NWPS or run Docker: gate check, compose stack,
  producers against Kafka and the Faust worker must be run on the laptop.
- faust-streaming install fails with an old setuptools (opentracing build); fixed by a
  fresh venv with upgraded pip/setuptools.
- Faust's agent test mode hangs without a broker, so the Faust app is import-checked only.
- Parsers use real values but a reconstructed JSON layout until `tools.fetch_sample` runs.

**Next**
- Fill `config/candidate_gauges.csv`; run `tools.fetch_sample` + `pytest`; run the gate
  check; start the staleness logger. Ask faculty the Phase I questions
  (`docs/MANUAL_CHECKS.md`).
- If a gauge passes: Phase 1 compose up, Phase 2–3 against Kafka, baseline backtest
  numbers, then choose the ML model and build the forecaster consumer and sink.

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
