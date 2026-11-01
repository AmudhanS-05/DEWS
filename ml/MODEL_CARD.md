# Model card: workstream `ml` (31 Oct 2026)

Two parts: the **flood-crossing forecaster** (S3 v0 stage-only, active at 3 h; v1 stage + rain) and the
**sensor-fault rules** (physical rules; the Isolation Forest is reported as a negative result). All numbers are measured on replayed USGS history (`data_source = replay`, mode
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

### Fix of 1 Nov 2026: stage regressor and alert rule
- **Bug:** `forecast_stage` collapsed towards the minor stage during floods (DRWT2, May 2019 replay: 25.67 ft now ->
  4.21 ft forecast on the user's laptop; 22.69 ft -> about 5.4 ft reproduced here). Cause: the regressor predicted the
  stage relative to minor and was fitted on the classifier's event-weighted rows, which are almost all below minor.
  Fixed by refitting only the regressors (`python -m ml.s3_model refit-reg`): target = change from the current
  stage, uniform 15 % sample of all 2015-2020 windows, no event weights. Test 2024-2026 stage MAE at 3 h: 0.056 ft
  (persistence 0.062, rise-rate 0.077); above minor stage 4.7 ft (persistence 8.6, rise-rate 10.4)
  (`ml/figures/s3_stage_mae.csv`). The same replay now gives 25.67 -> 25.39 ft.
- **Unaffected:** every detection number above (they use p_minor and tau, which did not change). The `mae_ft` /
  `rmse_ft` columns of `ml/figures/s3_eval.csv` came from the old regressor; use `s3_stage_mae.csv` instead.
- **Alert rule:** the detection results assume an alert when p_minor >= tau (3 h: tau 0.827) while below minor.
  `predict()` now returns `tau` and `alert_by_model`; the stream should alert on `alert_by_model` (docs/CONTRACTS.md).
  Alerts raised from `forecast_stage` crossing minor are not what was evaluated.

### Radius sensitivity (M3.3, ml/figures/s3_radius.csv)
Same v1 design and protocol at 5, 10 and 25 km, identical rows. Row-level AP at 3 h: 0.065 / 0.063 / 0.058; event F1
at 3 h 0.25 / 0.24 / no alert. No consistent effect of the radius; 10 km stays.

### Use in the stream
**Active since 31 Oct 2026 (team decision): `s3_v0` at the 3 h horizon only** (`ml/models/active.json`:
`{"name": "s3_v0", "horizons": [3]}`); every other horizon, and any failure, uses the rise-rate rule. The evidence
for that choice, in the words to use: **comparable detection with about a third fewer false alarms on the 2024-26
test period** (29 vs 33 of 63 crossings detected, 150 vs 224 false alarms, no lead-time gain). Not "better
forecasts". `predict()` returns `model = "s3_v0"`, `p_minor`, `forecast_stage`, `horizon_h = 3`; p_action, p_moderate,
p_major stay None. Measured in the cloud: 8.4 ms per call, first call ~1.5 s (file load), one OpenMP thread per call.
To switch back: delete `ml/models/active.json`.

## 2. Sensor-fault rules (ml/fault_rules.py; team decision 31 Oct 2026)

Physical rules on raw readings, with nearby gauges (within 60 km) and MRMS rain (10 km, where known) as independent
evidence: `no_readings` (>= 3 h without readings), `spike` (jump >= a*s returning within 1-2 readings), `stuck`
(identical value >= N h while a nearby gauge moves >= 0.1 ft or >= 5 mm rain fell), `step` (jump >= d*s that holds
flat, with rain < 1 mm or quiet nearby gauges as evidence). s = the gauge's 99th percentile of |hourly change|
(2015-2023). Settings chosen on validation years 2021-2023 as the most sensitive with flood-window false flags <= 1 %
and <= 5 clean episodes per gauge-year: a = 2, N = 24 h, d = 1 (`ml/figures/fault_rules_validation.csv`,
`fault_rules_settings.json`). Test period 2024-2026 used once (`ml/figures/fault_rules_test.csv`; faults injected into
real series, 10 per type per gauge, counted only while active and not flagged in the clean copy):

| rule (fault) | detected | median time to flag | flood-window false flags | clean episodes / gauge-year | shipped |
|---|---|---|---|---|---|
| no_readings (6 h dropout) | 100 % | 2.9 h | 0.49 % (6 of 64 floods touched) | 1.19 | yes |
| step (2 s offset) | 81.4 % | 0.33 h | 0 % | 0.10 | yes |
| stuck (24 h flat) | 29.5 % | 23.3 h | 0 % | 1.69 | yes |
| spike (3 s, one reading) | 100 % | 0.08 h | **1.07 %** (7 of 64 floods) | 0.36 | **no** (> 1 %) |
| slow drift (6 s over 72 h) | 0 % | - | - | - | no rule |

Stuck sensors are caught only when something independent moves (a neighbour or rain); at quiet low flow a stuck
sensor is indistinguishable from real flat water (flat runs >= 24 h occur ~10 times per gauge-year). The spike flags
inside flood windows may be real sensor glitches during floods or genuine one-reading jumps; they cannot be told
apart here, so the team rule excludes it. Slow drift is not detectable from stage alone.
Shipped through `check_gauge()` (contract proposal in docs/CONTRACTS.md), config `ml/models/fault_rules.json`.

### Measured negative result: Isolation Forest (ml/anomaly.py, not shipped)
Isolation Forest on hourly window features, trained 2015-2023, same injection protocol on 2024-2026: spike 17.6 %,
step 82.4 %, dropout 100 % (only through the added gap rule), drift 1.1 %, stuck 0.3 %; **11.2 % of real flood
windows flagged** (first feature set: 30 %) and 10.4 flag episodes per gauge-year (`ml/figures/anomaly_eval*.csv`,
`anomaly_validation_v2*.csv`). Detection swung strongly between validation and test (spike 37 % -> 18 %, step
15 % -> 82 %). Hourly aggregates hide the reading-level shape of a fault, and rarity is not the same as a fault:
gaps and flat runs are common in normal data while floods are rare, so a rarity detector flags floods. The physical
rules above use the readings and independent evidence instead.

## Results for the paper (one paragraph each, measured numbers only)
**Forecaster.** On the 2024-2026 test period (63 official minor-stage crossings at 38 gauges, 23 storms), the pooled
stage-only model s3_v0 at a 3 h horizon detected 29 crossings with 150 false alarms, against 33 with 224 for the
rise-rate rule: comparable detection with about a third fewer false alarms. It gave no extra warning (median 0.42 h
vs 0.50 h before the crossing) and did not improve on rise-rate at the other horizons. Adding MRMS radar rain (v1)
did not help on the rain-era rows (3 h F1 0.24 vs 0.31 for v0), and the rain radius (5, 10, 25 km) made no consistent
difference. The model is active at 3 h only; every other horizon uses the rise-rate rule.

**Sensor faults.** Physical rules on the raw readings, with neighbouring gauges and radar rain as independent
evidence, caught 100 % of injected dropouts, 81 % of step offsets and 30 % of stuck sensors while flagging at most
0.49 % of real flood windows; a spike rule caught every injected spike but flagged 1.07 % of flood windows and was
not shipped. An Isolation Forest on hourly features flagged 11 % of flood windows and is reported as a negative
result. Slow drift was not detectable from stage alone.

**Earlier baseline correction.** The 3 h rise-rate F1 values first quoted (0.65 / 0.26 / 0.64 / 0.41) contained a 1 h
look-ahead; the bar used throughout is the harness value, pooled F1 0.28 over 2015-2025 (ml/REFERENCE_BASELINES.md).

## Files and rebuild
Committed (3.5 MB, `git add -f`): `ml/models/s3_v0_h{1..6}.joblib`, `ml/models/s3_v1_r10_h{1..6}.joblib`,
`ml/models/anomaly_if.joblib` (negative result, kept for reproduction), `ml/models/fault_rules.json`, checksums in
`ml/models/MANIFEST.sha256`. Trained with Python 3.13.18,
scikit-learn 1.9.1 (pinned in requirements-ml.txt), numpy 2.5.3, joblib 1.6.0, pandas 3.0.5; a different
scikit-learn may not load them, then rebuild:
```
python -m ml.fetch_history --start-year 2015          # skip if data/raw/usgs/ is cached
python -m ml.s3_model build --end 2026-10-31          # uses data/rain/ or the committed ml/rain_cache/ (8 min)
python -m ml.s3_model train                           # v0 + v1, h=1..6 -> ml/models/, ml/figures/s3_eval* (8 min)
python -m ml.s3_model radius                          # M3.3 -> ml/figures/s3_radius.csv (6 min)
python -m ml.plot_s3                                  # ml/figures/s3_pr_3h.png
python -m ml.anomaly train && python -m ml.anomaly evaluate          # negative result: anomaly_if.joblib, anomaly_eval*.csv
ANOMALY_MODEL_PATH=/tmp/if_val.joblib python -m ml.anomaly train --train-end 2021-01-01   # scales from 2015-2020
python -m ml.fault_rules evaluate --period validation --scales-model /tmp/if_val.joblib    # grid, settings json
python -m ml.fault_rules evaluate --period test                       # ml/figures/fault_rules_test.csv (4 min)
python -m ml.demo_floods                                              # ml/figures/demo_flood_candidates.csv
```
Evaluation rows (`evaluation_runs` shape): `python -m ml.import_runs ml/figures/s3_eval.csv ml/figures/s3_radius.csv`.

## Earlier: M2.2 (rain-era only, superseded)
`ml/rain_model.py`, rows Oct 2020 onwards only, split 2024/2025: no consistent gain from rain or over rise-rate
(`ml/figures/model_v1_vs_v0*.csv`). Superseded by S3, which trains v0 on the full 2015-2020 history.

## Limits
Not compared with, and never claimed better than, NSSL FLASH, GloFAS or NWS forecasts. Live MRMS products differ
from Pass 2 used in training. 63 test crossings in 23 storms: differences of a few detections are within noise.
