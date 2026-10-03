# FAQ — flood forecasting build

Answers to questions the team has asked while building, kept so reviews and new
teammates get the same answer. `BREADCRUMBS.md` says **where the build is**; this file says
**why things are the way they are**. Numbers come from the gate check of 3 Oct 2026
(`gate_output/gate_summary.csv`, `gate_events.csv`); rerun it and update them if they change.

---

## Data

### What is a gauge? Is each gauge a dataset?
No. A gauge is one **station** on a river or creek: a sensor that records the water level
(**stage** or **gage height**, in ft) and, from that, the flow (**discharge**, in cfs). Each gauge has two ids:
- a **USGS site number** (e.g. `08158700`) — the id for its readings;
- an **NWS/NWPS id ("lid")** (e.g. `DRWT2`) — the id for its official flood categories.

The **dataset** is the service the gauges publish to. All gauges' readings come from one
dataset (USGS Instantaneous Values); all their flood thresholds come from another (NWPS).
Each gauge is a **set of rows inside** those datasets, identified by its site number.

### How many datasets do we use?
Four, as named in the deck:

| # | Dataset | What it gives us | Per gauge? |
|---|---|---|---|
| 1 | **USGS Instantaneous Values (IV)** — live | stage `00065` and discharge `00060` every 5 or 15 min | yes, one time series per gauge × parameter |
| 2 | **NOAA MRMS radar rainfall** | hourly rainfall grid, ~1 km, all of the US | no: one grid; we cut out the cell(s) around each gauge |
| 3 | **NOAA NWPS gauge metadata** | official flood stages: action, minor, moderate, major | yes, 4 numbers per gauge (the labels) |
| 4 | **Historical archives** (USGS IV history + MRMS archive on AWS) | the same data for past years, for replay demos and evaluation | yes (USGS) / grid (MRMS) |

### What does the data look like?
**USGS IV** (one JSON response, flattened by `common/usgs.py` into one row per reading):

| site_no | parameter | series | reading_time (UTC) | value | qualifiers |
|---|---|---|---|---|---|
| 08158700 | 00065 (stage, ft) | 141337 | 2026-10-03T01:00:00Z | 0.71 | P |

- `series` is the USGS time-series id. A gauge can have **more than one series per parameter**,
  e.g. a primary sensor and a "[PT Backup]" pressure transducer. We use the series with the most readings.
- `qualifiers`: `P` = provisional (may be revised later), `A` = approved.
- Readings are recorded every 5 or 15 min but **transmitted roughly hourly**, so the newest reading is usually 15–75 min old.

**NWPS** (one JSON per gauge): `DRWT2 → usgsId 08158700, action 5.5 ft, minor 5.5 ft, moderate 7.0 ft, major 13.0 ft`.

**MRMS**: one gzipped GRIB2 file per hour (~0.4 MB), a grid of rainfall in mm for the
previous hour. Product used: `MultiSensor_QPE_01H_Pass2_00.00`.

### How many gauges are there, and how many are we considering?

| Stage | Count | How |
|---|---|---|
| NWS gauges in the Texas Hill Country search box | **134** | `tools/find_gauges.py --bbox=…` |
| usable (have a USGS id **and** official action + minor stages) | **49** | same tool |
| written to `config/candidate_gauges.csv` and gate-checked | **40** | `--top 40` |
| after dropping the two lake gauges (3 Oct) | **38** | see "Which gauges were dropped" |
| with at least one minor flood in 10 years | **37** | gate check |
| with at least one flood that rose for ≥ 3 h (the pooled set) | **36** | gate check |
| pass the single-gauge rule (≥ 10 floods, median rise ≥ 3 h) | **2** — DRWT2, LCPT2 | gate check |

History: 10 years (2017-01-01 → today), about **26 million stage readings** across the 38
gauges (~1.0 M per 5-min gauge, ~0.34 M per 15-min gauge), **213 minor-flood crossings**, of which
**123** rose for ≥ 3 h.

### Which gauges were dropped, and why?
- **MDLT2** (Medina Lake, 08179500) and **MSDT2** (Lake Travis, 08154500): these are **reservoir
  level** gauges (thresholds of 1064 ft and 685 ft are lake elevations, not river stages) and
  USGS returned **no gage-height history** for them. Dropped on 3 Oct 2026.
- Kept but contributes no events: **ACRT2** (Colorado River at Austin) — 0 floods in 10 years; the
  river is regulated by upstream dams. It stays in the live stream as an ordinary feed.

### How many gauges feed the producers?
- **Live producer** (`producers/usgs_iv_producer.py`): **all 38**. One request fetches all of them
  every poll; each reading is keyed by `site_no`, so a gauge always lands on the same Kafka
  partition and its readings stay in order.
- **Replay producer** (`producers/replay_producer.py`): replays cached history for chosen
  gauges and dates — for demos and evaluation, mainly real flood events at **DRWT2** and **LCPT2**,
  stamped as SIMULATION.
- **Rainfall producer** (MRMS): not built yet; it will publish rainfall at the 38 gauge locations.

### How many gauges are used to train the ML model?
The model is **not built yet** (Phase 4 after the baselines). The plan:
- **One model for all gauges** (pooled), not one per gauge, because no single gauge has enough floods.
  Inputs are made comparable across gauges by expressing stage **relative to that gauge's own
  minor flood stage**, plus rise rate and recent rainfall.
- Trained on **all 37 gauges with flood history**; ordinary non-flood periods from all 38 are also training data.
- **Split by time, not at random** (e.g. earlier years train, later years test), because random
  splits leak the same storm into train and test. If the test years hold too few floods, use
  year-blocked cross-validation and report results per event.
- Headline results are reported on the **slow events** (≥ 3 h rise), with DRWT2 and LCPT2 as the showcase gauges.
- Floods are rare (well under 1 % of readings), so accuracy is not a useful score; we report
  event-level precision, recall, F1, false-alarm rate and lead time.

### Which are fast gauges and which are slow?
**Rise time** = hours from the start of the rise to the minor-flood crossing. It is the most lead time any forecast can give.

- **Fast** (median rise < 3 h — the hourly feed delay eats most of the lead time):
  AHOT2 Shoal Creek 0.9 h · KNLT2 Sandy Creek 0.8 h · ABUT2 Bull Creek 1.5 h · BTVT2 West Nueces 1.5 h ·
  SEGT2 Guadalupe at Seguin 1.8 h · LLAT2 Llano at Llano 2.3 h · CNCT2 Frio at Concan 2.4 h ·
  GETT2 San Gabriel 2.6 h. Borderline: BDAT2 Medina at Bandera 3.0 h.
- **Slow** (median rise ≥ 3 h): the remaining 28 gauges with events, e.g. DRWT2 10.9 h, LCPT2 11.8 h,
  JNCT2 9.5 h, MLRT2 15.1 h, HNTT2 17.3 h, LLGT2 17.0 h, GNLT2 27.0 h, GBCT2 28.1 h.

Fast gauges still go through the pipeline (they are useful for latency and throughput
figures and for "detect as early as possible"), but **lead-time claims are only made on slow events**.
Separately, gauges record every **5 min** (e.g. DRWT2, JNCT2, the Austin creeks) or every **15 min**
(e.g. LCPT2, KRRT2, LLGT2). That is the recording interval, not the speed of the river.

### What is "pooled" data?
Floods at one gauge are rare: only 2 of 38 gauges had ≥ 10 minor floods in 10 years. **Pooling**
means treating flood events from **all gauges together** as one evaluation set, so the scores rest on
123 slow events from 36 gauges instead of 15 events from one. The gate check prints both verdicts:
2/40 pass per gauge; the **pooled set passes** (123 ≥ 10).

Caveat to state in reviews: pooled events are **not fully independent**. One storm can flood several
gauges on the same day (e.g. 4 May 2019 hit both DRWT2 and LCPT2). Report the number of
distinct storm days alongside the number of events. **Status:** pooled evaluation still needs team/faculty confirmation.

---

## Model and wording

### What are the options for the "anomaly / event detection" stage?
The brief asks for "ML-based anomaly/event detection". Four ways to read it:

| Option | What the model outputs | Labels needed | Fit for us |
|---|---|---|---|
| **A. Forecast stage, then threshold** | stage 1–6 h ahead (regression: gradient-boosted trees or LSTM); the alert fires when the forecast crosses the official minor stage | past stage only (self-labelling) | matches Objective 2 word for word — **primary** |
| **B. Event classifier** | probability that the gauge reaches minor flood stage within the next N hours | crossings derived from NWPS categories + history | closest to "detect a flood event"; good second head or alternative to A |
| **C. Unsupervised anomaly detection** (Isolation Forest, rolling z-score of rise rate) | "this reading or rise is unusual" | none | cannot predict a stage or a flood; use it for **sensor faults** (spikes, flat lines, primary vs backup disagreement) and as an "abnormal rise" flag |
| **D. Rule / statistical baselines** | persistence, rise-rate extrapolation, CUSUM change detection | none | the **baselines** A/B must beat (already built: `ml/baselines.py`) |

Recommended deck wording (replaces "A supervised model (Isolation forest) predicts river stage"):
> *A supervised model forecasts river stage several hours ahead and raises an alert when the forecast
> crosses the official flood category; an Isolation Forest flags abnormal sensor readings before they reach the model.*

This keeps Objective 2's wording, uses Isolation Forest for what it can actually do, and covers the
brief's "anomaly/event detection" (event = a forecast category crossing; anomaly = an abnormal reading or rise).

### So is there a supervised model *and* an Isolation Forest? What exactly do we predict?
Two models with two different jobs. Only the first one is required.

**1. Supervised forecaster (the core, required).** Every hour, for every gauge, the
windowing stage builds one row of features:
- current stage, and stage minus that gauge's minor flood stage;
- rise over the last 1, 3 and 6 h, and the fastest 15-min rise;
- MRMS rainfall over the gauge for the last 1, 3, 6 and 24 h;
- month and hour;
- how old the newest reading is.

"Supervised" means the training answers come from the history itself: for each past hour
we already know what the stage was 1–6 h later. Nobody labels anything by hand. What it
outputs, per gauge, every hour:

| Output | Example (DRWT2, minor stage 5.5 ft) |
|---|---|
| stage at +1, +2, … +6 h (regression) | 3.1 ft now → 4.0, 4.8, 5.4, 6.2 ft … |
| forecast **category** at each horizon (stage compared with the NWPS levels) | below action → … → **minor at +4 h** |
| probability of reaching minor stage within 6 h (classification head or from the forecast spread) | 0.72 |
| forecast time of crossing | ~14:00 UTC |

So the alert reads: *"DRWT2 Onion Creek near Driftwood: 3.1 ft now, forecast to reach minor
flood stage (5.5 ft) in about 4 h (p = 0.72). Newest reading 35 min old."*

What it **cannot** predict:
- flooded area, depth away from the gauge, or damage;
- anything at ungauged creeks;
- reliable peak height for big floods (we may report it but do not promise it);
- warning beyond the river's rise time — about 10–12 h at DRWT2/LCPT2, near zero at fast urban creeks once the ~hourly feed delay is taken off.

**2. Isolation Forest (optional add-on, data quality).** It is unsupervised: it learns what
normal *sensor behaviour* looks like and flags readings that don't fit, for example:
- a jump between consecutive readings that no river can make;
- a flat line for hours during rain;
- primary and backup sensor disagreeing;
- a negative or no-data code.

A flagged reading is marked *suspect*: the alert it would trigger waits for a human check
instead of going out. It must **not** use flood features (rise, rainfall), because a real flood
is also "unusual" and it would flag floods as faults. It has no labelled faults to be scored
against, so it is tested by injecting spikes and flat lines into a replay (fault injection, not fake river data).

Why keep it: the brief asks for "ML-based anomaly/event detection". The forecaster covers
*event* detection (a forecast category crossing); the Isolation Forest covers *anomaly*
detection literally, at little cost. If time runs short, drop it; the forecaster alone still meets the objectives.

### Is predicting river stage the same as predicting flooding?
**At the gauge, nearly.** NWS sets each gauge's *minor flood stage* as the level where flooding
begins to cause impacts there. So "the stage will cross minor flood stage within N hours" is an
official flood forecast **for that gauge location**.

**It is not:**
- flood extent or inundation maps;
- damage;
- flooding at places with no gauge, such as a small creek flash-flooding with no sensor.

So we never say "a flood will occur". We say: *"Gauge X is forecast to reach minor flood stage
(5.5 ft) within N hours, probability p."*

---

## Notebooks

### What data do the EDA notebooks use?
All three download their own data, so they run in Colab or Kaggle with Internet on. Defaults set 3 Oct 2026:

| Notebook | Gauges | Period | Data sources |
|---|---|---|---|
| `01_classic_eda_usgs` | DRWT2 (08158700, 5-min), LCPT2 (08172400, 15-min) | last 2 years | USGS IV: stage + discharge, all series and qualifiers |
| `02_flood_specific_eda` | DRWT2, LCPT2 (pass the gate), JNCT2 (slow, pooled only), AHOT2 (fast urban creek, for contrast) | last 10 years | USGS IV stage + NWPS flood categories + Open-Meteo hourly rainfall at the gauge |
| `03_mrms_rainfall_eda` | DRWT2 | 24 h before → 12 h after its 1 Jun 2024 06:40 UTC flood crossing (rise 20.3 h, peak 6.94 ft) | MRMS hourly QPE grids from the AWS bucket |

Run 02 before 03: notebook 02's event table is where 03's event time comes from. Notebook 03
picks a 2024 event because the MRMS archive on AWS starts in late 2020, after the large May 2019 floods (check the bucket listing if you choose another event).
Notebook 02's numbers should match `gate_output/gate_events.csv` for the same gauges (same event definitions).

### Where do the candidate gauge codes come from?
`python -m tools.find_gauges --bbox=…` asked the NOAA NWPS API for every NWS forecast gauge in
a box over the Texas Hill Country (134 gauges). It kept those that list a **USGS site number**
(where the readings come from) **and** official action + minor flood stages (49). It wrote the top
40 to `config/candidate_gauges.csv`. The 5-letter code (e.g. `DRWT2`) is the NWS gauge id ("lid");
the 8-digit code (e.g. `08158700`) is the USGS site number. Both appear on the NWPS gauge page.

### What do the gauge codes mean?
- **NWS id** (5 characters, e.g. `DRWT2`): a place mnemonic plus a state code. For example, DRW = Driftwood and T2 = Texas.
  NOAA's flood categories are filed under this id.
- **USGS site number** (8 digits, e.g. `08158700`): the first two digits are the hydrologic region (08 = Texas–Gulf
  basins); the rest orders the station along its river, upstream to downstream. Readings are filed under this number.
- The notebooks label plots by USGS number. `docs/EDA_WALKTHROUGH.md` starts with a key; the deck's EDA charts use
  "DRWT2 · Onion Creek near Driftwood" style labels.

### Why US data and not Indian data?
Of the sources we checked, only the US offers all of these openly, free of charge and with archives: live gauge readings
every 5–15 min going back 10+ years, official flood levels for each gauge, and hourly 1-km radar rainfall. India's CWC
publishes warning and danger levels on its forecasting portal, but has no open real-time API or downloadable archive.
CAMELS-IND is daily and access-restricted, and GloFAS is a modelled daily product. The deck states this on the
"EDA Results – Data at a glance" slide.

### Which rainfall product should the pipeline stream (notebook 03)?
On 2 Oct 2026, the gauge-corrected hourly MRMS (Pass 2) appeared about 58 min after its hour, Pass 1 about 16 min after,
and the radar-only products about 3–5 min after (refreshed every 2 min). The live producer uses radar-only or Pass 1;
evaluation uses Pass 2. One hourly CONUS grid is 3500 × 7000 cells, about 0.8 MB compressed; −3 marks cells with no coverage.

### What did the EDA find (3 Oct 2026, notebook 02 on DRWT2, LCPT2, JNCT2, AHOT2)?
- **Categories:** each gauge spends 0.05–0.3 % of its readings at or above minor flood stage.
  Floods are a rare class, so accuracy is meaningless; score events.
- **Events** match the gate check exactly (16, 15, 9, 38): the notebook and the pipeline use the same definitions.
- **Season:** most floods fall in May–June and October; 2022–2023 were nearly dry.
- **Hydrographs:** LCPT2 climbs for 3–8 h before crossing. AHOT2 and DRWT2 jump in the last hour or so.
- **Caveat on "rise time":** at DRWT2 the median rise time is 10.9 h, but the aligned plot shows
  the stage creeps slowly and then jumps in about the last hour. The rise-start rule finds an
  early, shallow trough, so **rise time overstates the warning that stage alone gives**. The
  rise-rate baseline's median warning (0.8 h at DRWT2, 2 h at LCPT2) is the honest stage-only
  number. Rainfall is what moves the warning earlier.
- **Rain → stage:** at DRWT2 on 4 May 2019 about 107 mm fell in the days around the flood, much of it before the jump.
  The best rain–stage lag varies from 0 to 18 h between events, so the model needs several
  rainfall windows (1, 3, 6, 24 h), not one fixed lag.
- **Rise rate:** about 0 ft/h in normal hours vs a median of about 1 ft/h in the 6 h before a flood
  (LCPT2). This is a strong feature, but it arrives late.
- **Live feed delay (one sample):** 24–69 min. Usable warning (median rise minus delay) is
  about 10 h at DRWT2, 11 h at LCPT2 and below zero at AHOT2, before the caveat above.
- **Baselines:** persistence never warns (recall 0). Rise-rate extrapolation at 3 h: F1 0.65 at DRWT2,
  0.26 at LCPT2, 0.64 at JNCT2, 0.41 at AHOT2, with under 1–2 h of warning. This is the bar the ML model must beat.

Notebook 01 has been rerun on DRWT2 and LCPT2 (3 Oct). Neither gauge reached minor flood stage in its 2-year window
(peaks 5.49 ft and 13.73 ft in June 2026). Every cell and figure is explained in `docs/EDA_WALKTHROUGH.md`.

### How do I check that MRMS files decode at all (notebook 03)?
Run after the cell that builds `files` (the day's file listing). `files` is a **table**
(DataFrame), not a list, so take the key from its `key` column: `files[0]` fails with `KeyError: 0`.
```python
import gzip, os, tempfile, requests, xarray as xr
key = files.key.iloc[0]
r = requests.get(f"{BUCKET}/{key}", timeout=60); r.raise_for_status()
tmp = os.path.join(tempfile.gettempdir(), "mrms_test.grib2")
open(tmp, "wb").write(gzip.decompress(r.content))
ds = xr.open_dataset(tmp, engine="cfgrib", backend_kwargs={"indexpath": ""})
print(ds)
print("lon", float(ds.longitude.min()), "->", float(ds.longitude.max()),
      "| lat", float(ds.latitude.min()), "->", float(ds.latitude.max()))
```
Expected: one variable (often named `unknown`, because MRMS uses its own GRIB tables) on a
grid of about 3500 × 7000. Longitudes run about 230 → 300; `load_grid` converts them to −130 → −60.
Latitudes run about 20 → 55. Confirmed on a real file, 3 Oct 2026: 3500 × 7000 grid, longitudes 230.005 → 299.995, latitudes 20.005 → 54.995.
If the event cell then says "0 of N hourly files available", the event time was not on the hour.
MRMS files are valid at HH:00, so the notebook rounds `EVENT_UTC` down to the hour (fixed 3 Oct).
If it fails with "Cannot find the ecCodes library", run
`!pip install -q eccodes eccodeslib cfgrib` and restart the runtime.

---

## Running things

### The staleness logger was stopped midway — how do I resume?
Run the same command again:
```
python -m tools.staleness_logger --every-min 5 --hours 48
```
- It **appends** to `gate_output/staleness_log.csv` and writes the header only if the file is new, so
  earlier rows are kept. The first poll after a restart counts every reading as "new"; ignore
  `new_readings_since_last_poll` for that one poll. `staleness_min` is unaffected.
- `--hours` counts from the restart. The 3 Oct run logged 10 polls (01:57–02:38 UTC, 2 failed);
  a fresh `--hours 48` gives the full two days.
- Keep the laptop awake and plugged in: Windows sleep pauses the loop and leaves a gap. Use its own
  PowerShell window. Rows with an `error` (USGS hiccups) are expected now and then and are skipped in analysis.
- It reads `config/candidate_gauges.csv` at start, so it now polls the 38 gauges.

### Why do I run data steps on my laptop and not in the build environment?
The build environment cannot reach USGS or NOAA (its proxy blocks them). Anything that downloads
real data — `fetch_sample`, `find_gauges`, `gate_check`, `staleness_logger`, the notebooks — runs on
your laptop or in Colab/Kaggle.
