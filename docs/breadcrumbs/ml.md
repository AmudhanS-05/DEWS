# ML workstream breadcrumbs (tracked; core merges these into BREADCRUMBS.md)

[ml] M1.3 done: `ml/predict.py` baseline stub (rise_rate, 3 h, probabilities None, never raises); 5 contract tests in tests/ml/test_predict.py pass, <50 ms per call (cloud run, synthetic inputs only).
[ml] M1.1 code done (not yet run on real data): `ml/dataset.py` pooled builder (windows via streams/windowing.py unchanged, stage relative to minor, y_cross_h / y_stage_h for h=1..6, event ids, slow flag, per-gauge-equal event weights, time_split). 6 tests on a synthetic generator (tests/ml/synth.py) pass; no results claimed.
[ml] M1.2 code done: `ml/harness.py` writes evaluation_runs-shaped rows per gauge, POOLED and POOLED-SLOW (slow = rise >= 3 h), event metrics, lead time gross and net of 1 h feed delay, distinct storm count, optional INSERT. 5 tests on synthetic data pass; no results claimed.
[ml] M1.4 NOT run (needs cached USGS history on the laptop): see "Run locally" below.
[ml] I1.1 US-side code done: `india/audit.py` (interval, archive length, missing %, longest gap, events, median rise, rise resolvable); 3 tests pass on synthetic series. Real run and the India-WRIS station list need the laptop / manual downloads, see below.

[ml] Round 2 (coordinator request): harness/dataset vectorised, windows built once per gauge; 3 synthetic gauges x 10 y x 15 min: harness 7 s for 6 horizons x 2 models, dataset 5 s (extrapolated ~95 s / ~70 s for 38 gauges; unmeasured on real data). Equal action/minor stage was never a skip condition in the code (skips are only: categories missing/non-numeric, no cached data); a test now pins that DRWT2-style gauges are evaluated. Dataset default output is Parquet. ml/figures/*.csv and *.parquet are git-ignored; only ml/figures/baselines.csv (small) is meant to be tracked (`git add -f`).
[ml] Not done, awaiting the user's go-ahead (S2 scope, beyond the S1 rows assigned): I2.2 India-WRIS loader, M2.1 MRMS download, M2.2 model v1 vs v0.

## Run locally (laptop, venv `..\spaenv`, repo root; `pip install -r requirements-ml.txt` first)
All three CLIs take `--data-dir <folder containing raw/usgs>` (default: DATA_DIR env, then ./data), print a start line,
one `ok`/`skip <reason>` line per gauge, a progress bar and a final `done ... in N min` line.
1. `pytest -q`
2. Quick check on the four reference gauges (expect `ok` x4, then a table ref / measured / diff for the 3 h rise-rate F1):
   `python -m ml.harness --gauges-only DRWT2,LCPT2,JNCT2,AHOT2 --horizons 3 --out ml/figures/baselines_ref.csv`
3. M1.4 all gauges, h = 1..6 (expected a few minutes; windows are built once per gauge):
   `python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv`
   Add `--insert` (Docker DB up) to write the rows into `evaluation_runs`. Report differences from the reference F1s, do not tune.
4. M1.1 pooled dataset, Parquet (a `.csv` suffix on --out switches to CSV):
   `python -m ml.dataset --out ml/figures/pooled_dataset.parquet`
5. I1.1 US-side audit (exits with an error and writes nothing only if 0 gauges could be read, saying why):
   `python -m india.audit --out india/figures/audit_us.csv`
6. I1.1 India side (manual, India-WRIS is browser-only): pick 3 to 5 free stations with known floods (Godavari,
   Krishna, Mahanadi or Periyar), download 2015-2025 (one year per Excel file), copy warning/danger levels from CWC
   bulletins into india/thresholds.csv.
