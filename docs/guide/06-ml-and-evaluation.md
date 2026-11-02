# 06 ML and evaluation

> **Status (1 Nov 2026):** at the 3 h horizon the forecaster uses **`s3_v0`**, a gradient-boosting model on stage
> features. Every other horizon, and any failure, uses the rise-rate rule. On the held-out 2024-26 test period it shows
> **comparable detection with about a third fewer false alarms** than the rise-rate rule, and no lead-time gain. That
> is not "better forecasts", and **no forecast skill is claimed**. All numbers are from replayed USGS history (mode
> SIMULATION); none is a live result. Source of truth: [`ml/MODEL_CARD.md`](../../ml/MODEL_CARD.md).

## What is predicted

For each hourly window (built by `streams/windowing.py`, the same code Faust runs) and horizon h = 1 to 6 h:

- `p_minor`: the probability that the stage, now below the official NWPS **minor** stage, reaches it within h hours;
- `forecast_stage`: the stage at t + h, in feet.

Only the minor category was trained, so `p_action`, `p_moderate` and `p_major` stay empty. An alert is raised when
`p >= tau` while the stage is below minor, with a 6 h cooldown. The pipeline's forecaster then turns forecasts into
alert episodes (new, upgrade, retract) against all four official categories.

It cannot tell you flooded area, damage, anything at ungauged creeks, or warning beyond the river's rise time.

## Protocol (fixed before any test number was looked at)

| | |
|---|---|
| Rows | 3,895,185 hourly windows, 38 gauges, 2015 to 31 Oct 2026, 331 minor crossings |
| Split by time | fit 2015-2020 (229 crossings) · validate 2021-2023 (39) · **test 2024-01-01 to 2026-10-31 (63 crossings, 23 storms, 36 slow)** |
| Threshold `tau` | false-alarm budget: the most detected crossings whose validation false alarms do not exceed the rise-rate rule's on the same rows |
| Scoring | per event: precision, recall, F1, false alarms, median lead time **gross and net** of the 1 h feed delay; POOLED and POOLED-SLOW (rise of 3 h or more); equal weight per gauge and per event; rise-rate rescored on the same rows |
| Disclosed correction | a first run refitted v0 and reused the validation tau, which broke the false-alarm budget (378 vs 224 at 3 h); kept as `ml/figures/s3_eval_run1_refit*`, and the final protocol keeps each tau with its model |

Accuracy is never used: flood windows are a tiny share of all windows. Splits are never random, because one storm
would leak into both sides.

## Models

| Model | Inputs | Result on the test period |
|---|---|---|
| rise-rate | extrapolate the window's rise for h hours | the bar to beat ([below](#the-bar-to-beat)) |
| `s3_v0` | stage relative to minor and action, fraction to minor, rise rate, range, 1/3/6 h changes (+12/24 h where chosen on validation) | **active at 3 h**; comparable detection, fewer false alarms |
| `s3_v1_r10` | v0 + MRMS radar rain over a 10 km circle (1/3/6 h) | below v0 on the rain rows at every horizon tried; never above rise-rate. **Rain did not help** |
| persistence | stage stays the same | never warns |

Learner: scikit-learn `HistGradientBoostingClassifier` and `Regressor`, pinned to scikit-learn 1.9.1 so the committed
models in `ml/models/` load. A call takes 8.4 ms, and `predict()` never raises: on any error it returns the rise-rate
result. To return to the rise-rate rule everywhere, delete `ml/models/active.json` and restart the forecaster.

## Results (test period 2024-26, all test windows, POOLED; `ml/figures/s3_eval.csv`)

| h | v0 F1 | v0 false alarms | rise-rate F1 | rise-rate false alarms |
|---|---|---|---|---|
| 1 | 0.29 | 44 | 0.30 | 72 |
| 2 | 0.24 | 109 | 0.26 | 145 |
| **3** | **0.24** (P 0.16, R 0.46) | **150** | **0.21** (P 0.13, R 0.52) | **224** |
| 4 | 0.19 | 189 | 0.19 | 297 |
| 5 | no alert | 0 | 0.17 | 362 |
| 6 | 0.17 | 340 | 0.16 | 425 |

At 3 h, v0 detects 29 of 63 crossings against rise-rate's 33, with 150 against 224 false alarms. Median lead time is
0.42 h gross / **−0.58 h net** for v0, and 0.50 / −0.50 h for rise-rate. Slow floods only: F1 0.16 for both. 63
crossings in 23 storms is a small sample, so a few detections either way are within noise.

**Radar rain radius** (5 / 10 / 25 km, `ml/figures/s3_radius.csv`): row-level average precision at 3 h of 0.065 /
0.063 / 0.058, so the radius has no consistent effect. 10 km stays.

### The bar to beat

Rise-rate extrapolation at 3 h, scored by `ml/harness.py` on stream windows (38 gauges, 2015 to 2025): pooled F1 0.28
(precision 0.18, recall 0.61, 310 minor crossings), slow floods only 0.21; per gauge DRWT2 0.25, LCPT2 0.42, JNCT2 0.76,
AHOT2 0.25; median warning 0.8 h gross, −0.2 h net of the 1 h feed delay (`ml/figures/baselines.csv`).
*Corrected 31 Oct 2026: earlier figures (0.65 / 0.26 / 0.64 / 0.41) included a 1 h look-ahead; evidence in
`ml/REFERENCE_BASELINES.md`.* This bar covers 2015-2025. The S3 table above rescored rise-rate on the 2024-26 test rows,
which is why its F1 differs.

## Sensor-fault rules (`ml/fault_rules.py`)

Physical rules on raw readings, with nearby gauges (within 60 km) and radar rain as independent evidence. Settings were
chosen on 2021-23 and tested once on 2024-26, with faults injected into real series, 10 per type per gauge
(`ml/figures/fault_rules_test.csv`):

| Rule (fault) | Detected | Flood windows falsely flagged | Shipped |
|---|---|---|---|
| no readings for 3 h or more (6 h dropout) | 100 % | 0.49 % | yes |
| step (offset that holds) | 81.4 % | 0 % | yes |
| stuck (24 h flat while a neighbour moves or rain falls) | 29.5 % | 0 % | yes |
| spike (one-reading jump) | 100 % | **1.07 %** | **no**: above the team's 1 % limit |
| slow drift | 0 % | n/a | no rule |

A stuck sensor at quiet low flow cannot be told apart from real flat water, so the stuck rule only fires when something
independent moves.

**Measured negative result:** an Isolation Forest on window features flagged **11.2 % of real flood windows** and
swung strongly between validation and test, so it is not shipped (`ml/figures/anomaly_eval*.csv`). A rarity detector
flags floods, because floods are rare.

## Reproduce

```powershell
python -m ml.fetch_history --start-year 2015          # USGS history (38 gauges, ~31 M readings); skip if cached
python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv
python -m ml.s3_model build --end 2026-10-31          # uses the committed ml/rain_cache/ for rain (8 min)
python -m ml.s3_model train                           # v0 + v1, h = 1..6 -> ml/models/, ml/figures/s3_eval* (8 min)
python -m ml.s3_model radius                          # ml/figures/s3_radius.csv
python -m ml.plot_s3                                  # ml/figures/s3_pr_3h.png
python -m ml.fault_rules evaluate --period test       # ml/figures/fault_rules_test.csv
python -m ml.demo_floods                              # ml/figures/demo_flood_candidates.csv
python -m ml.import_runs ml/figures/s3_eval.csv ml/figures/s3_radius.csv   # rows into evaluation_runs (DB up)
```

DEWS is not compared with, and makes no claim against, NWS, NSSL FLASH or GloFAS forecasts.
