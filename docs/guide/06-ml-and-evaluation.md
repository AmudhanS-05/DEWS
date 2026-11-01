# 06 ML and evaluation

> **Status (31 Oct 2026):** the forecaster in the pipeline is the **rise-rate baseline**. The gradient-boosting models
> (stage only, and stage + radar rain) have been trained **once** on replayed real data (38 gauges, Oct 2020 to
> Oct 2026). **Neither beats the rise-rate baseline consistently on events, so no skill is claimed.** Every result,
> with the command that produced it, is in [`ml/MODEL_CARD.md`](../../ml/MODEL_CARD.md).

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
| `gbm_v0_stage` | stage relative to minor/action, fraction to minor, rise rate, range, 1/3/6 h changes | trained once (`ml/rain_model.py`); not better than rise-rate |
| `gbm_v1_stage_rain` | v0 + MRMS radar rain over a 10 km circle (1, 3, 6, 24 h) | trained once; better row-level ranking than v0 at most horizons, no consistent event-level gain |

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
| Rise-rate baseline, 3 h, scored by `ml/harness.py` on stream windows (38 gauges, 2015 to 2025, replay) | pooled F1 0.28 (precision 0.18, recall 0.61, 310 crossings), slow floods only 0.21; DRWT2 0.25, LCPT2 0.42, JNCT2 0.76, AHOT2 0.25; median warning 0.8 h gross, −0.2 h net of the 1 h feed delay | `ml/figures/baselines.csv`, `ml/REFERENCE_BASELINES.md` |
| Baseline in the pipeline, May 2019 DRWT2 replay | alert only **after** the minor crossing (no lead time) | core run, 31 Oct 2026 |
| v0 / v1 vs rise-rate, POOLED, h = 1 to 6 | no consistent event-level gain; median net lead time around zero | `ml/MODEL_CARD.md`, "Measured results" |

The rise-rate baseline is the bar to beat; the model comparison scores it on the same rows as v0/v1. *Corrected 31 Oct 2026: earlier figures (0.65 / 0.26 / 0.64 / 0.41) included a 1 h look-ahead; evidence in `ml/REFERENCE_BASELINES.md`.* DEWS makes no comparison with NWS, NSSL FLASH or GloFAS forecasts, and claims
nothing against them.

## Reproduce

Run from the repo root with `pip install -r requirements-dev.txt` done. Data goes to `data/` (git-ignored).

```powershell
python -m ml.fetch_history --start-year 2015                                 # USGS history (38 gauges, ~31 M readings)
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
