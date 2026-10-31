# Model card: workstream `ml` (31 Oct 2026)

Two models: the **flood-crossing forecaster** (S3 v0 stage-only, v1 stage + rain) and the **sensor-fault detector**
(Isolation Forest + one rule). All numbers are measured on replayed USGS history (`data_source = replay`, mode
SIMULATION); none is a live result. **Neither forecaster beats the rise-rate rule consistently; no forecast skill is
claimed.** The bar is defined in `ml/REFERENCE_BASELINES.md`.

## 1. Forecaster (ml/s3_model.py)

### What it predicts
For each hourly window (`streams/windowing.py`, the code the stream uses) and horizon h = 1..6 h: `p_minor`, the
probability that the stage, now below the official NWPS **minor** stage, reaches it within h hours ("forecast of
crossing an official category"), and `forecast_stage` (stage at t + h, regressor on stage relative to minor). Only
the minor category was trained: `p_action`, `p_moderate`, `p_major` are None. Alert rule (as `ml/harness.py`):
p >= tau while the stage is below minor, 6 h cooldown.

### Data and protocol (fixed before any test number was looked at)
- Rows: 3,895,185 hourly windows, 38 gauges, 2015-01-01 to 2026-10-31, 331 minor crossings (`python -m ml.s3_model
  build`). Rain: MRMS Pass 2, mean depth over 5/10/25 km circles, only in the M2.1 event and dry hours (Oct 2020
  onwards; `ml/rain_cache/mrms_circle.parquet`).
- Split by time: fit 2015-2020 (229 crossings), validate 2021-2023 (39), **test 2024-01-01 to 2026-10-31 (63
  crossings, 23 storms; 36 slow)**. Every design and threshold choice is made on the validation years.
- v0: HistGradientBoosting classifier (300 iterations, lr 0.05, 15 leaves, L2 1.0, seed 0) per horizon; features
  `rel_minor, rel_action, frac_to_minor, rise_rate, stage_range, d1h, d3h, d6h` (+ `d12h, d24h` when that set wins on
  validation: chosen for h = 1, 3, 4). Training rows: all positives and event rows plus a fixed 20 % sample of the
  rest (weight x5); equal weight per gauge and per event.
- v1: v0's score plus rain_1h/3h/6h (the rain fields `aggregates.window` carries live). Two designs, chosen per
  horizon by leave-one-year-out over 2021/2022/2023: `stack` (logistic regression on logit p_v0 + log1p rain) or
  `joint` (gradient boosting on v0 features + rain). Final v1 = mean of the three fold models.
- Threshold tau (false-alarm budget): the most detected crossings whose validation false alarms do not exceed the
  rise-rate rule's on the same validation rows.
- Protocol correction (disclosed): a first run refitted v0 on 2015-2023 and reused the validation tau; the refit's
  probabilities ran higher and test false alarms broke the budget (3 h: 378 vs rise-rate 224). It is kept as
  `ml/figures/s3_eval_run1_refit*`; the final protocol keeps each tau with the model it was chosen on.

### Measured results, TEST period (ml/figures/s3_eval.csv; rise-rate rescored on the same rows)
All test windows, POOLED:

| h | v0 F1 | v0 P / R | v0 false alarms | rise-rate F1 | rise-rate P / R | rise-rate false alarms |
|---|---|---|---|---|---|---|
| 1 | 0.29 | 0.29 / 0.29 | 44 | 0.30 | 0.25 / 0.38 | 72 |
| 2 | 0.24 | 0.18 / 0.38 | 109 | 0.26 | 0.18 / 0.49 | 145 |
| 3 | **0.24** | 0.16 / 0.46 | **150** | **0.21** | 0.13 / 0.52 | **224** |
| 4 | 0.19 | 0.12 / 0.41 | 189 | 0.19 | 0.11 / 0.60 | 297 |
| 5 | no alert | - | 0 | 0.17 | 0.10 / 0.62 | 362 |
| 6 | 0.17 | 0.10 / 0.60 | 340 | 0.16 | 0.09 / 0.68 | 425 |

At 3 h, v0 raises a third fewer false alarms (150 vs 224) but detects fewer crossings (29 vs 33 of 63); median lead
time 0.42 h gross / -0.58 h net of the 1 h feed delay vs 0.50 / -0.50 for rise-rate: **no lead-time gain**.
POOLED-SLOW 3 h: v0 0.16, rise-rate 0.16. At 5 h no threshold met the budget with any detection on validation
(tied probabilities), so v0 never alerts there. Per gauge (3 h) the 63 crossings spread over 26 gauges (1 to 9 each),
too few for per-gauge conclusions.

Rain-era test windows (event + dry hours only; a case-control sample, so precision is not a live figure), 3 h
POOLED: v0 F1 0.31 (FA 97), v1 (joint) 0.24 (FA 48), rise-rate 0.26 (FA 156). v1 is below v0 at 1, 2 and 3 h
(0.28 / 0.21 / 0.24 vs 0.31 / 0.30 / 0.31) and raises no alert at 4, 5 and 6 h (its validation threshold admits
none on test); it is never above rise-rate. Rain did not help.
Row-level precision-recall at 3 h: `ml/figures/s3_pr_3h.png` (`python -m ml.plot_s3`), points in
`ml/figures/s3_eval_pr_curve.csv`.

### Radius sensitivity (M3.3, ml/figures/s3_radius.csv)
Same v1 design and protocol at 5, 10 and 25 km, identical rows. Row-level AP at 3 h: 0.065 / 0.063 / 0.058; event F1
at 3 h 0.25 / 0.24 / no alert. No consistent effect of the radius; 10 km stays.

### Use in the stream
`ml/predict.py` uses a model only when `ml/models/active.json` exists (`{"name": "s3_v0"}` or `{"name": "s3_v1_r10"}`;
see `active.json.example`). **Not activated**: the evidence does not show a clear gain over rise-rate (decision for
the team). Measured in the cloud: 8.4 ms per call (v0), 17 ms (v1), first call ~1.5 s (file load); one OpenMP thread
per call. Any failure returns the rise-rate record.

## 2. Sensor-fault detector (ml/anomaly.py)

`score_window(features) -> {anomaly_score, suspect, reason}` on the same inputs as `predict()`. Isolation Forest
(200 trees, 4096 samples per tree, seed 0) on seven fault-SHAPE features (excursion, reversal, jump_then_flat, flat_h,
n_ratio, gap_h, creep; raw rise size is not a feature), threshold = top 0.1 % of clean training windows, plus a rule:
>= 3 h without readings -> suspect ("no_readings"). Trained on 2015-2023 windows (25 % sample, 737,471 windows).

Evaluation: faults injected into held-out real series (2024-2026, 10 per type per gauge, quiet periods below
action stage), counted only when flagged while the fault is active and not flagged in the clean copy
(`ml/figures/anomaly_eval.csv`, per gauge `anomaly_eval_per_gauge.csv`):

| fault | injected | detected | median time to flag |
|---|---|---|---|
| spike (one reading +-3 s, min 0.5 ft) | 370 | 17.6 % | 0.8 h |
| step offset (2 s, min 0.3 ft, 48 h) | 370 | 82.4 % | 1.5 h |
| dropout (6 h without readings) | 370 | 100 % (rule) | 3.4 h |
| slow drift (6 s over 72 h) | 370 | 1.1 % | - |
| stuck / flat-lined sensor (24 h) | 370 | 0.3 % | - |

Clean test data: 10.4 flag episodes per gauge-year (1.1 of them no-readings gaps); flagged windows 3.7 % (long
outages flag every hour). **Real flood windows (12 h before to 6 h after a minor crossing): 11.2 % flagged** (136 of
1,211), so the detector does NOT yet meet "never flag a genuine flood rise"; a flag must never suppress or raise an
alert by itself.
Design history (disclosed): a first feature set (with raw change size) was evaluated on the test period and flagged
30 % of flood windows; the redesign was then done on validation years only (fit 2015-2020, inject 2021-2023:
`ml/figures/anomaly_validation_v2*.csv`; spike 37 %, step 15 %, flood windows 7.3 %), and the test period was used
once for the table above. The large validation-to-test swings (spike 37 % -> 18 %, step 15 % -> 82 %) show the
rates are not stable. Stuck sensors at low flow are not detectable from stage alone: flat runs >= 24 h occur about
10 times per gauge-year in clean data.

## Files and rebuild
Committed (3.5 MB, `git add -f`): `ml/models/s3_v0_h{1..6}.joblib`, `ml/models/s3_v1_r10_h{1..6}.joblib`,
`ml/models/anomaly_if.joblib`, checksums in `ml/models/MANIFEST.sha256`. Trained with Python 3.13.18,
scikit-learn 1.9.1 (pinned in requirements-ml.txt), numpy 2.5.3, joblib 1.6.0, pandas 3.0.5; a different
scikit-learn may not load them, then rebuild:
```
python -m ml.fetch_history --start-year 2015          # skip if data/raw/usgs/ is cached
python -m ml.s3_model build --end 2026-10-31          # uses data/rain/ or the committed ml/rain_cache/ (8 min)
python -m ml.s3_model train                           # v0 + v1, h=1..6 -> ml/models/, ml/figures/s3_eval* (8 min)
python -m ml.s3_model radius                          # M3.3 -> ml/figures/s3_radius.csv (6 min)
python -m ml.plot_s3                                  # ml/figures/s3_pr_3h.png
python -m ml.anomaly train && python -m ml.anomaly evaluate          # ml/models/anomaly_if.joblib, anomaly_eval*.csv
```
Evaluation rows (`evaluation_runs` shape): `python -m ml.import_runs ml/figures/s3_eval.csv ml/figures/s3_radius.csv`.

## Earlier: M2.2 (rain-era only, superseded)
`ml/rain_model.py`, rows Oct 2020 onwards only, split 2024/2025: no consistent gain from rain or over rise-rate
(`ml/figures/model_v1_vs_v0*.csv`). Superseded by S3, which trains v0 on the full 2015-2020 history.

## Limits
Not compared with, and never claimed better than, NSSL FLASH, GloFAS or NWS forecasts. Live MRMS products differ
from Pass 2 used in training. 63 test crossings in 23 storms: differences of a few detections are within noise.
