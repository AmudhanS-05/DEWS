# Model card: flood-crossing forecaster (workstream `ml`)

Status (31 Oct 2026): v0 (stage only) and v1 (stage + MRMS rain) trained once on real replayed data (cloud session).
**Neither beats the rise-rate baseline consistently on events; no skill is claimed.** See "Measured results".

## What the models predict
For each hourly window (built by `streams/windowing.py`, the same code the stream uses) and each horizon h = 1..6 h:
- `p_minor`: probability that the stage, now below the official NWPS **minor** flood stage, reaches it within h hours
  ("forecast of crossing an official category", never "predicts a flood");
- `forecast_stage`: the stage at t + h (ft), via a regressor on the stage relative to minor.
One pooled model over all gauges (stage relative to each gauge's minor stage). An alert is raised when p >= tau while
the stage is below minor, with a 6 h cooldown (same rule as `ml/harness.py`).

| Model | Features | File (git-ignored) |
|---|---|---|
| `gbm_v0_stage` | rel_minor, rel_action, frac_to_minor, rise_rate, stage_range, d1h, d3h, d6h | `ml/models/gbm_v0_stage_h<h>.joblib` |
| `gbm_v1_stage_rain` | v0 + rain_1h, rain_3h, rain_6h, rain_24h (MRMS Pass 2, mean depth over a 10 km circle) | `ml/models/gbm_v1_stage_rain_h<h>.joblib` |

Learner: scikit-learn `HistGradientBoostingClassifier` / `Regressor` (300 iterations, learning rate 0.05,
15 leaves, L2 1.0, random_state 0). Weights: equal total weight per gauge, equal weight per event within a gauge.
tau: chosen on the last 25 % (by time) of the training period after fitting on the first 75 %; the test period is
never used for a choice. Each `.joblib` holds `{clf, reg, tau, features, horizon_h, trained_on, radius_km, rain_lag_h}`.

## Data
- Stage: USGS IV gage height (00065), 38 gauges in `config/gauge_locations.csv`, cached per gauge and year under
  `data/raw/usgs/` by `python -m ml.fetch_history`.
- Labels: official NWPS categories from `config/gauge_locations.csv` only.
- Rain: MRMS `MultiSensor_QPE_01H_Pass2` hourly files from the public `noaa-mrms-pds` bucket, decoded in memory,
  mean depth over 5 / 10 / 25 km circles (10 km is the decided radius) for event hours (48 h before to 12 h after each
  minor crossing) and one matched dry window per event; `data/rain/mrms_circle.parquet` from `python -m ml.mrms_history`.
  **The archive starts 2020-10-14**: v1 can only use floods from then on (not the 51 floods of 2018).
- All of this is replayed history: `data_source = replay`, mode SIMULATION downstream.

## Rebuild (exact commands, repo root, Python 3.12/3.13, `pip install -r requirements-ml.txt`)
```
python -m ml.fetch_history --start-year 2015                       # USGS history -> data/raw/usgs/ (~1 GB JSON)
python -m ml.mrms_history --radii 5,10,25 --workers 4              # MRMS rain   -> data/rain/mrms_circle.parquet
python -m ml.rain_model --split 2024-01-01 --horizons 1,2,3,4,5,6 --radius-km 10 \
    --label m2.2-rain-v1 --out ml/figures/model_v1_vs_v0.csv       # trains v0 + v1, writes ml/models/*.joblib
```
`--split` must leave minor crossings on both sides; the command stops with an error if a side has none.
Add `--insert` (Docker DB up) to write the rows into `evaluation_runs`.

## Evaluation
Event-based (`ml/evaluate.py`): precision, recall, F1, false alarms, median lead time gross and net of the 1 h feed
delay, MAE/RMSE of stage; per gauge, POOLED, POOLED-SLOW (rise >= 3 h); distinct storms; row-level average precision
and the score file `ml/figures/model_v1_vs_v0_scores.parquet` for precision-recall curves. The rise-rate baseline
is scored on the same rows.
Caveat: v0/v1 rows are a case-control sample (flood windows + matched dry windows), not the continuous stream, so
precision and false-alarm counts are not those of a year of live operation.

## Measured results (31 Oct 2026, replay, commands above; rows in ml/figures/model_v1_vs_v0*.csv)
Rows: 38 gauges, hourly windows with 24 h of rain, Oct 2020 to Oct 2026 (102 minor crossings + 102 dry windows).
POOLED F1 by horizon (rise-rate = the same rows):

| split (train / test crossings) | model | 1 h | 2 h | 3 h | 4 h | 5 h | 6 h |
|---|---|---|---|---|---|---|---|
| 2024-01-01 (39 / 63) | v0 stage | 0.07 | 0.10 | 0.25 | NA | 0.22 | 0.21 |
| | v1 stage + rain | 0.10 | NA | 0.22 | 0.26 | NA | 0.27 |
| | rise-rate | 0.34 | 0.31 | 0.27 | 0.26 | 0.23 | 0.23 |
| 2025-01-01 (55 / 47) | v0 stage | 0.19 | 0.10 | NA | 0.31 | 0.19 | 0.23 |
| | v1 stage + rain | 0.16 | 0.12 | 0.27 | 0.25 | 0.30 | 0.28 |
| | rise-rate | 0.34 | 0.32 | 0.26 | 0.27 | 0.24 | 0.24 |

NA = no alert raised at the chosen tau. Row-level average precision (y_cross_h, base rate 0.07 to 0.4 %): v1 above
v0 at 5 of 6 horizons (2024 split) and 4 of 6 (2025 split). Median lead time about 0.4 to 1.2 h gross, about -0.6 to
+0.2 h net of the 1 h feed delay. Only `--split 2024-01-01` models are kept in ml/models/.
Open issues: tau chosen on a validation slice with few crossings is unstable; the 3 h rise-rate bar from FAQ.md
contains a one-hour look-ahead (see docs/breadcrumbs/ml.md), use ml/figures/baselines.csv instead.

## Limits
- Not compared with, and never claimed better than, NSSL FLASH, GloFAS or NWS forecasts.
- Live MRMS products differ from the training product (live: radar-only or Pass 1, minutes after the hour;
  training: Pass 2, about 1 h after the hour).
