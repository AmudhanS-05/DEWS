# ML track handoff (for a separate chat, working in parallel)

**Goal:** the supervised forecaster: from windowed features, estimate the stage `h` hours ahead (h = 1 to 6) and the probability of crossing each official NWPS category. Plus a small Isolation Forest that flags abnormal sensor readings. Both must beat the baselines below and run inside the streaming forecaster later.

**Can start now:** yes, offline, with no running Kafka. It needs only the cached history under `data/raw/usgs/` (10 years, 38 gauges) and the official stages in `config/candidate_gauges.csv` and `config/gauge_locations.csv`.

**Not available yet:** rainfall history. MRMS has no producer or archive download yet, so train stage-only first (stage, rise rate, last stage, time of year). Add 1, 3 and 6 h catchment rain once the MRMS archive tool exists, and report both versions: the gain from rain is a result.

## Must follow
- **Same window code as the stream:** `streams/windowing.py` (via `ml/backtest.py`), so offline and live features match.
- **Labels are official categories only** (action, minor, moderate, major), never invented thresholds.
- **Split by time**, with flood years on both sides (2018: 51 floods; 2022 to 2023: only 8). Never split at random.
- **Pooled model** across gauges, stage relative to each gauge's minor stage. Lead-time claims only on slow events (rise of 3 h or more).
- **Event-based metrics:** precision, recall, F1, false-alarm count, lead time gross and net of the ~1 h feed delay, plus MAE and RMSE of stage. Provisional (`P`) and approved (`A`) data kept visible.
- **Baselines to beat** (rise-rate extrapolation, 3 h horizon): DRWT2 F1 0.65, LCPT2 0.26, JNCT2 0.64, AHOT2 0.41; median warning 0.8 to 2.0 h. Persistence never warns before a crossing.
- **Never claim better forecasts than NSSL FLASH, GloFAS or NWS.**

## Candidate models (in this order)
1. Gradient boosting (scikit-learn `HistGradientBoosting`; LightGBM if installed): classifier for "crosses minor within h" and a stage regressor.
2. Quantile version, so the advisory can state a range.
3. One sequence model (LSTM or GRU) as a comparison, not first.
4. Isolation Forest on window features, evaluated on injected sensor faults (spikes, flat-lines).

## Output contract (what the streaming forecaster will import)
- `ml/models/<name>.joblib` + `ml/MODEL_CARD.md` (data, features, split, metrics, limits).
- `ml/predict.py`: `predict(features: dict) -> {"forecast_stage", "p_action", "p_minor", "p_moderate", "p_major", "horizon_h"}`.
- Results as rows for `evaluation_runs` (see `storage/schema.sql`), replay and live never merged.

## Rules for working in the same repo
Edit only `ml/`, `notebooks/` and a new `requirements-ml.txt`. Do not touch `common/`, `producers/`, `streams/`, `dashboard/`. Commit separately. Keep `pytest -q` green.
