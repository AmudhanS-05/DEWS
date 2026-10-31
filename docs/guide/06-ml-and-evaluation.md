# 06 ML and evaluation

> **Status (31 Oct 2026):** the forecaster in the pipeline is the **rise-rate baseline**. The gradient-boosting models
> (stage only, and stage + radar rain) are written and tested on **synthetic data only**. **No trained model and no
> real-data skill are claimed yet.** Results go into `ml/MODEL_CARD.md` only after a real run, together with the command
> that produced them.

## What is predicted

For each hourly window and each horizon h = 1 to 6 h, per gauge:

- `p_minor`: the probability that the stage, now below the official **minor** flood stage, reaches it within h hours;
- `forecast_stage`: the stage at t + h, in feet.

An alert fires when `p >= tau` while the stage is below minor. The pipeline's forecaster turns the forecast into
category crossings for all four official categories. Labels come **only** from the official NWPS categories in
`config/gauge_locations.csv`.

What this cannot tell you: flooded area, damage, anything at ungauged creeks, or warning beyond the river's rise time.

## Models

| Model | Inputs | State |
|---|---|---|
| persistence | stage now = stage later | baseline; never warns (recall 0 in the EDA) |
| `rise_rate` | extrapolate the last 3 h rise | baseline; **live in the pipeline** via `ml/predict.py` |
| `gbm_v0_stage` | stage relative to minor/action, fraction to minor, rise rate, range, 1/3/6 h changes | code done (`ml/rain_model.py`), not trained on real data |
| `gbm_v1_stage_rain` | v0 + MRMS radar rain over a 10 km circle (1, 3, 6, 24 h) | code done, not trained on real data |

Both GBMs use scikit-learn `HistGradientBoostingClassifier` for the probability and `HistGradientBoostingRegressor` for
the stage. **One pooled model** covers all gauges. Stage is expressed relative to each gauge's own minor stage so that
gauges are comparable. The full card, including hyper-parameters and the rebuild commands, is in
[`ml/MODEL_CARD.md`](../../ml/MODEL_CARD.md).

**The contract with the pipeline:** `ml.predict.predict(features) -> {forecast_stage, p_action, p_minor, p_moderate,
p_major, horizon_h, model}`. It must answer in under 50 ms and **never raise**; on any error it returns the rise-rate
baseline. The forecaster records the model name on every forecast.

**Same features online and offline.** Training windows are built by `streams/windowing.py`, the same code Faust runs, so
a model sees the same features in replay, evaluation and live.

## How it is scored

- **Events, not rows.** Floods are 0.05 to 0.3% of readings, so accuracy means nothing. Scores: precision, recall,
  F1, false-alarm count, lead time and stage MAE/RMSE, plus precision-recall curves.
- **Lead time gross and net:** net subtracts the roughly 1 h feed delay.
- **Split by time, never at random.** Random splits leak one storm into train and test. Both sides must hold floods;
  `ml.rain_model` refuses a split with no crossings on one side. The threshold `tau` is chosen on the last 25% of the
  training period, and the test period is never used for a choice.
- **Pooled, per gauge and slow-only.** Only 2 of 38 gauges have 10 or more floods, so the evaluation pools events from
  all gauges (accepted by faculty) and reports per gauge, POOLED, and POOLED-SLOW (rise of 3 h or more, where lead time
  is possible). Each gauge gets equal total weight, so fast urban creeks do not dominate (AHOT2 alone has 38 of 213
  floods). The number of **distinct storms** is reported too, because one storm floods several gauges.
- **Live and replay are never merged.**
- **Case-control caveat (v0/v1):** rain history exists only around floods plus matched dry periods, so precision and
  false-alarm counts from those rows are not those of a year of live operation.

## Numbers that do exist

| What | Value | Source |
|---|---|---|
| Gauges / history | 38 gauges, 10 years, about 26 million stage readings, 213 minor crossings, 123 slow | gate check, 3 Oct 2026 (`FAQ.md`) |
| Rise-rate baseline, 3 h, F1 | DRWT2 0.65, LCPT2 0.26, JNCT2 0.64, AHOT2 0.41, with under 1 to 2 h of warning | EDA notebook 02, real data, 3 Oct 2026 |
| Baseline in the pipeline, May 2019 DRWT2 replay | alert only **after** the minor crossing (no lead time) | core run, 31 Oct 2026 |

The baseline numbers are the bar to beat. DEWS makes no comparison with NWS, NSSL FLASH or GloFAS forecasts, and claims
nothing against them.

## Reproduce

Run from the repo root with `pip install -r requirements-dev.txt` done. Data goes to `data/` (git-ignored).

```powershell
python -m ml.fetch_history --start-year 2015                                 # USGS history, about 1 GB
python -m ml.harness --gauges-only DRWT2,LCPT2,JNCT2,AHOT2 --horizons 3 --out ml/figures/baselines_ref.csv
python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv
python -m ml.dataset --end 2026-10-31 --out ml/figures/pooled_dataset.parquet
python -m ml.mrms_history --radii 5,10,25 --workers 4 --max-hours 20         # smoke test first; resumable
python -m ml.rain_model --split 2024-01-01 --horizons 1,2,3,4,5,6 --label m2.2-rain-v1 --out ml/figures/model_v1_vs_v0.csv
```

Add `--insert` to `ml.harness` or `ml.rain_model` (with the database up) to write rows into `evaluation_runs`, which
Power BI page 3 reads. The MRMS archive starts on 2020-10-14, so v1 cannot use the 51 floods of 2018. `ml/backtest.py`
scores one gauge and date range against the baselines
(`python -m ml.backtest --site 08158700 --start 2019-05-01 --end 2019-05-10 --thresholds 5.5,5.5,7.0,13.0`; `--lid DRWT2` fetches the levels from NWPS instead).

## Planned (S3 to S5)

Final split and evaluation, model v2 with a quantile range, a radius sensitivity test (5/10/25 km), an Isolation Forest
for sensor faults (spikes, flat lines; not for floods), and the forecaster hot-swapping to the trained model with a
fallback to the baseline.
