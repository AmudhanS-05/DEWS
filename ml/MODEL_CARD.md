# Model card: flood-crossing forecaster (workstream `ml`)

Status (31 Oct 2026): code for v0 (stage only) and v1 (stage + MRMS rain) is written and tested on synthetic data
only. **No model has been trained on real data yet and no skill is claimed.** Results go in "Measured results" below
only after a real run, with the command that produced them.

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

## Measured results
None yet (see `docs/breadcrumbs/ml.md` for the state of each run).

## Limits
- Not compared with, and never claimed better than, NSSL FLASH, GloFAS or NWS forecasts.
- Live MRMS products differ from the training product (live: radar-only or Pass 1, minutes after the hour;
  training: Pass 2, about 1 h after the hour).
