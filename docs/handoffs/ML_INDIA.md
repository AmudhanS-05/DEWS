# Handoff: ML forecaster and India data study (workstream `ml`)

## Mission
1. The supervised forecaster: from windowed features, estimate the stage `h` hours ahead (h = 1 to 6) and the
   probability of crossing each official NWPS category; plus a small Isolation Forest that flags abnormal sensor
   readings. Both must beat the baselines and run inside the streaming forecaster through `ml/predict.py`.
2. The India data study, all four steps (`docs/INDIA_STUDY.md`): audit, pipeline run on Indian data, degradation
   experiment on real US data, synthetic "ideal India" dataset. Paper section and figures.

## Read first
`CLAUDE.md`, `docs/SLICES.md` (your rows: M1.1 to M3.3 and I1.1 to I5.1), `docs/CONTRACTS.md` (the `predict()`
contract, `evaluation_runs`), `docs/INDIA_STUDY.md`, `FAQ.md` (pooled evaluation, what the models predict),
`docs/EDA_WALKTHROUGH.md`.

## Data you can use now (offline, no Kafka)
Cached history `data/raw/usgs/` (10 years, 38 gauges; in the main repo folder, linked into your worktree), official
stages `config/gauge_locations.csv`, gate events `gate_output/gate_events.csv`. Rainfall history comes in S2 (MRMS
download around the 123 slow floods plus matched dry periods, 10 km circle, mean depth): train stage-only first and
report the gain from rain as a result.

## Must follow
- **Same window code as the stream:** `streams/windowing.py` (via `ml/backtest.py`), so offline and live features
  match. Read it; never copy and change it.
- **Labels are official categories only** (action, minor, moderate, major).
- **Split by time**, flood years on both sides (2018: 51 floods; 2022 to 2023: 8; DRWT2 and LCPT2 had no minor
  flood in 2024 to 2026). Rolling-origin evaluation is acceptable. Never split at random.
- **Pooled model** (decided with faculty), stage relative to each gauge's minor stage; report per gauge, slow-only
  (rise ≥ 3 h) and the number of distinct storms; weight events so fast urban creeks (AHOT2: 38 of 213) do not dominate.
- **Event-based metrics:** precision, recall, F1, false-alarm count, lead time gross and net of the ~1 h feed delay,
  MAE and RMSE of stage; precision-recall curves, never accuracy.
- **Baselines to beat** (rise-rate, 3 h, `ml/harness.py`, 38 gauges, 2015-2025): pooled F1 0.28 (slow-only 0.21);
  DRWT2 0.25, LCPT2 0.42, JNCT2 0.76, AHOT2 0.25 (`ml/figures/baselines.csv`). Rescore the baseline on the same rows
  when a model uses a different period. Corrected 31 Oct 2026: earlier values included a 1 h look-ahead; see
  `ml/REFERENCE_BASELINES.md`.
- **Radius sensitivity** (S3): rerun with rain over 5, 10 and 25 km circles.
- **India steps:** step C carries the quantitative argument (real data); step D shows feasibility only and says so.
- Never claim better forecasts than NSSL FLASH, GloFAS or NWS.

## Candidate models (in this order)
1. Gradient boosting (scikit-learn `HistGradientBoosting`; LightGBM if installed): classifier for "crosses minor
   within h" and a stage regressor. 2. Quantile version (a range for the advisory). 3. One sequence model (LSTM/GRU) as
   a comparison only. 4. Isolation Forest on window features, scored on injected faults (spikes, flat-lines).

## Output contract
`ml/predict.py` (exists as a baseline stub from S1 day one, then the real model), `ml/models/<name>.joblib`
(git-ignored; keep the training command in the model card so anyone can rebuild it), `ml/MODEL_CARD.md`, rows in
`evaluation_runs`, figures under `ml/figures/` and `india/figures/`.

## You own
`ml/` (except `streams/forecaster.py`, which core owns), `india/`, `notebooks/`, `requirements-ml.txt`, tests under
`tests/ml/` and `tests/india/`.

## Starter prompt (paste into Claude Code in the ml worktree)
> Read CLAUDE.md and docs/handoffs/ML_INDIA.md. You are the ml workstream on branch `ml`. First do M1.3 (the
> contract-correct `ml/predict.py` baseline stub with tests) so core can use it, then M1.1, M1.2, M1.4 and I1.1 from
> docs/SLICES.md. Run everything offline on the cached data. Add a [ml] line to the BREADCRUMBS.md in the main repo
> folder per finished task with the numbers, commit on your branch, and stop with a summary when S1's ML rows are
> done.
