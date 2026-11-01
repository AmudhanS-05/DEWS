# 2. Data

| Dataset | Use | Access |
|---|---|---|
| USGS Instantaneous Values, gage height `00065` | live stream, replay, training and evaluation | REST API (JSON), public domain |
| NOAA NWPS gauge metadata | official action / minor / moderate / major stages: the **only** labels | REST API |
| NOAA MRMS QPE (hourly, ~1 km) | radar rain over a circle around each gauge | AWS Open Data `noaa-mrms-pds`; archive starts 2020-10-14 |
| India-WRIS (CWC and state agencies) | India study only | API from an Indian connection, or manual Excel download |

## 2.1 Gauge selection

| Step | Count | Source |
|---|---|---|
| NWS forecast gauges in the Texas Hill Country search box | 134 | `FAQ.md` ("How many gauges"), `tools/find_gauges.py` |
| with a USGS site number and official action + minor stages | 49 | same |
| written as candidates and gate-checked | 40 | `config/candidate_gauges.csv` |
| after dropping two reservoir-level gauges (MDLT2, MSDT2) | **38** | `config/gauge_locations.csv` |

## 2.2 History

- **USGS:** 38 gauges, 2015-01-01 to 2026-10-31, **30,847,419 stage readings**, 0 failed gauge-years; median
  reporting interval 5 min (range 5 to 15) [`ml/figures/usgs_fetch_summary.csv`; `india/figures/table1_us_vs_india.md`].
- **Missing data:** median 0.8 % per station (range 0.0 to 36.6 %); longest gap median 120 h (range 2 to 3276 h)
  [`india/figures/table1_us_vs_india.md`].
- **Minor-flood crossings:** 310 in 2015 to 2025 across 38 gauges [`ml/figures/baselines.csv`, POOLED 3 h notes]; 331
  in 2015 to 31 Oct 2026, of which 229 fall in the fit period, 39 in validation and **63 in test (23 storms, 36 slow)**
  [`ml/MODEL_CARD.md` §1].
- **Event definition:** first reading at or above minor after a reading below it, with events at least 24 h apart
  (`common/hydro.find_crossings`). Slow event: rise of 3 h or more.

## 2.3 Radar rain

- Decoded for every minor crossing since 2020-10-14 (48 h before to 12 h after) plus one matched dry window per event:
  8,551 hours requested, 8,535 decoded, 16 missing in the archive [commit `3df53f7`; `ml/figures/mrms_hours_summary.csv`].
- 10 km circle around a gauge: 294 grid cells at DRWT2 [commit `3df53f7`].
- Total rain over the window (10 km): median **100.9 mm before slow floods**, 57.3 mm before fast floods, **0.07 mm in
  dry windows** [`ml/figures/mrms_event_vs_dry.csv`].
- Publication delay after the hour, measured 31 Oct to 1 Nov 2026: RadarOnly about 3 min, Pass 1 about 15 min,
  Pass 2 about 57 min [`docs/breadcrumbs/rain.md` on branch `claude/rain`, commits `b9d2821`, `c395d7c`].
- **No radar rain exists for floods before October 2020**, including the May 2019 demo flood.

## 2.4 Labels and splits

Labels are crossings of the official NWPS **minor** stage only, for "will the stage reach minor within h hours". The
split is by time: fit 2015-2020, validation 2021-2023 (every design and threshold choice), test 2024-01-01 to
2026-10-31, used once [`ml/MODEL_CARD.md` §1]. All of it is replayed history, labelled SIMULATION downstream.

## 2.5 Live data

TODO: the 48 h staleness log (`python -m tools.staleness_logger --every-min 5 --hours 48`, `docs/RUNBOOK.md` step 8)
has not been run yet. Until it is, the live feed delay rests on one sample (24 to 69 min).
