# Reference baselines: what the old FAQ bar was, why it was wrong, and the bar to beat

Corrected 31 Oct 2026. Every number below comes from a committed command and a committed CSV (listed under each
table). Data: USGS IV gage height (00065) cached by `python -m ml.fetch_history --start-year 2015` (summary
`ml/figures/usgs_fetch_summary.csv`), official NWPS minor stages from `config/gauge_locations.csv`.

## 1. The old numbers
FAQ.md ("What did the EDA find?") and docs/handoffs/ML_INDIA.md gave the 3 h rise-rate baseline as
**F1 0.65 at DRWT2, 0.26 at LCPT2, 0.64 at JNCT2, 0.41 at AHOT2**, from notebook 02, cell 27 (`baseline_scores`),
run over the 10 years ending early October 2026.

## 2. How they were produced, and the flaw
`baseline_scores` resampled each gauge to hourly means with `v.resample("h").mean()`. Pandas labels a bin by its
START by default, so the value stamped 06:00 is the mean of 06:00 to 06:59. The notebook then raised an alert "at
06:00" when `mean + 3 h x rate` reached minor stage, and scored it against crossings in (06:00, 09:00]. The alert
had already seen readings up to 06:59: **one hour of look-ahead**. Floods that cross within that hour are
"forecast" from data that already contains the crossing.

`ml/reference_check.py` re-implements the notebook on the cached data, with the events of `common/hydro.find_crossings`
(the same rule as notebook 02's `detect_events`: first reading at or above minor after a reading below it, 24 h
apart), the same 6 h cooldown and the same scoring (`ml/evaluate.score_alerts`). It changes one thing at a time:

| Variant | Hourly value | Label | Rate | Gaps |
|---|---|---|---|---|
| A (notebook as run) | mean | start of hour (look-ahead) | diff of hourly means | interpolate up to 3 h |
| B | mean | end of hour | diff of hourly means | interpolate up to 3 h |
| C | last reading | end of hour | diff of hourly last readings | interpolate up to 3 h |
| D (harness) | `streams/windowing.py`: last reading | end of window | in-window (last - first) / span | zero-fill, carry last stage forward |

Command: `python -m ml.reference_check --out ml/figures/reference_check.csv` (1 min 30 s).

## 3. Results on the notebook's window (2016-10-03 to 2026-10-02)
F1 (crossings / alerts / detected crossings); source `ml/figures/reference_check.csv`.

| Gauge | Old FAQ | A notebook (look-ahead) | B mean, end label | C last, end label | D harness |
|---|---|---|---|---|---|
| DRWT2 | 0.65 | **0.65** (16 / 21 / 12) | 0.05 (16 / 21 / 1) | 0.18 (16 / 18 / 3) | 0.16 (16 / 22 / 3) |
| LCPT2 | 0.26 | **0.26** (15 / 38 / 7) | 0.34 (15 / 38 / 9) | 0.33 (15 / 40 / 9) | 0.33 (15 / 39 / 9) |
| JNCT2 | 0.64 | **0.64** (9 / 19 / 9) | 0.29 (9 / 19 / 4) | 0.62 (9 / 20 / 9) | 0.64 (9 / 19 / 9) |
| AHOT2 | 0.41 | **0.41** (38 / 115 / 31) | 0.17 (38 / 115 / 13) | 0.25 (38 / 139 / 22) | 0.25 (38 / 147 / 23) |

What each step shows:
- **A reproduces all four old numbers exactly**, and the event counts (16, 15, 9, 38) are the ones the FAQ quotes,
  so the re-implementation is faithful.
- **A to B (remove the look-ahead):** the alert COUNT is identical (21, 38, 19, 115: the same hourly series, only
  stamped one hour later), but detected crossings fall from 12 to 1 at DRWT2, 9 to 4 at JNCT2 and 31 to 13 at AHOT2.
  Those detections existed only because the alert was stamped before data it had already used. LCPT2 rises over
  3 to 8 h, so the extra hour hardly matters there (7 to 9 detected).
- **B to C (hourly mean to last reading):** an hourly mean lags a fast rise (it averages the low start of the hour
  with the high end), so with honest labels the mean under-reads the current stage. Using the last reading recovers
  part of it (DRWT2 1 to 3 detected, JNCT2 4 to 9, AHOT2 13 to 22). The stream uses the last reading.
- **C to D (rate definition and gap handling):** F1 moves by at most 0.03 (DRWT2 0.18 to 0.16, JNCT2 0.62 to 0.64,
  AHOT2 0.25 to 0.25, LCPT2 0.33 to 0.33). D has more hourly rows (87,647 vs 85,621 to 87,561) because the stream
  emits zero-filled hours instead of dropping them. This residual is the in-window rise rate and carry-forward of
  `streams/windowing.py`, i.e. what the live forecaster actually sees.
- Same events, same cooldown, same scoring throughout: the differences above are entirely the hourly value, its
  time stamp and the rate.

## 4. Date range: the evaluation protocol (2015-01-01 to 2025-12-31)

| Gauge | D harness, notebook window | D harness, 2015-2025 (M1.4) | crossings (nb window / 2015-2025) |
|---|---|---|---|
| DRWT2 | 0.16 | 0.25 | 16 / 26 |
| LCPT2 | 0.33 | 0.42 | 15 / 24 |
| JNCT2 | 0.64 | 0.76 | 9 / 8 |
| AHOT2 | 0.25 | 0.25 | 38 / 46 |

Sources: `ml/figures/reference_check.csv` (both windows), equal to `ml/figures/baselines_ref_nbrange.csv`
(`python -m ml.harness --gauges-only DRWT2,LCPT2,JNCT2,AHOT2 --horizons 3 --start 2016-10-03 --end 2026-10-02
--label diag-nb-range --out ml/figures/baselines_ref_nbrange.csv`) and `ml/figures/baselines.csv` (M1.4 command
below). 2015 adds many floods (DRWT2 26 vs 16 crossings) and 2026 is outside the protocol. With 8 to 46 crossings
per gauge, single-gauge F1 moves by about 0.1 between windows, which is why the pooled number is the headline bar.

## 5. The bar to beat
**Rise-rate extrapolation, 3 h, scored by `ml/harness.py` on 38 gauges, 2015-01-01 to 2025-12-31:**
**POOLED F1 0.28** (precision 0.18, recall 0.61, 856 false alarms, 310 minor crossings, 98 storms, median lead time
0.83 h gross / -0.17 h net of the 1 h feed delay); **POOLED-SLOW F1 0.21**; on the four reference gauges
**DRWT2 0.25, LCPT2 0.42, JNCT2 0.76, AHOT2 0.25**. Other horizons: `ml/figures/baselines.csv` (h = 1 to 6).

Command: `python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv`.

Why this one:
1. **Same window code as the stream** (`streams/windowing.py` via `ml/backtest.py`): the baseline sees exactly the
   features the live forecaster sees, so a model compared against it is compared on the deployed footing.
2. **No look-ahead:** every alert at window end t uses only readings at or before t.
3. **The evaluation protocol's period and gauges:** the same 38 gauges and 2015-2025 range as M1.1/M1.4.
4. **Pooled** over 310 crossings rather than one gauge's 8 to 46, so it is not dominated by a single storm.

When a model is evaluated on a different period or row set (for example the M2.2 rain rows, Oct 2020 onwards),
the rise-rate baseline must be rescored on exactly those rows, as `ml/rain_model.py` does; the 0.28 above is the
bar for the full-protocol evaluation only.

## 6. Where the old values appeared
Corrected: FAQ.md (EDA findings, "Baselines"), docs/handoffs/ML_INDIA.md ("Baselines to beat"), and ml/harness.py
(the comparison table now prints the old values labelled as such). Not corrected here (outside ml's files or
historical output): docs/EDA_WALKTHROUGH.md lines 872 and 1095, and the saved outputs of
notebooks/02_flood_specific_eda.ipynb cell 27 (the notebook code still uses start-labelled means; rerun it with
`resample("h", label="right", closed="left")` to remove the look-ahead).
