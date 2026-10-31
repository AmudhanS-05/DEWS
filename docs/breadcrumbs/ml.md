# ML workstream breadcrumbs (tracked; core merges these into BREADCRUMBS.md)

[ml] M1.3 done: `ml/predict.py` baseline stub (rise_rate, 3 h, probabilities None, never raises); 5 contract tests in tests/ml/test_predict.py pass, <50 ms per call (cloud run, synthetic inputs only).
[ml] M1.1 code done (not yet run on real data): `ml/dataset.py` pooled builder (windows via streams/windowing.py unchanged, stage relative to minor, y_cross_h / y_stage_h for h=1..6, event ids, slow flag, per-gauge-equal event weights, time_split). 6 tests on a synthetic generator (tests/ml/synth.py) pass; no results claimed.
[ml] M1.2 code done: `ml/harness.py` writes evaluation_runs-shaped rows per gauge, POOLED and POOLED-SLOW (slow = rise >= 3 h), event metrics, lead time gross and net of 1 h feed delay, distinct storm count, optional INSERT. 5 tests on synthetic data pass; no results claimed.
[ml] M1.4 NOT run (needs cached USGS history on the laptop): see "Run locally" below.
[ml] I1.1 US-side code done: `india/audit.py` (interval, archive length, missing %, longest gap, events, median rise, rise resolvable); 3 tests pass on synthetic series. Real run and the India-WRIS station list need the laptop / manual downloads, see below.

[ml] Round 2 (coordinator request): harness/dataset vectorised, windows built once per gauge; 3 synthetic gauges x 10 y x 15 min: harness 7 s for 6 horizons x 2 models, dataset 5 s (extrapolated ~95 s / ~70 s for 38 gauges; unmeasured on real data). Equal action/minor stage was never a skip condition in the code (skips are only: categories missing/non-numeric, no cached data); a test now pins that DRWT2-style gauges are evaluated. Dataset default output is Parquet. ml/figures/*.csv and *.parquet are git-ignored; only ml/figures/baselines.csv (small) is meant to be tracked (`git add -f`).
[ml] Not done, awaiting the user's go-ahead (S2 scope, beyond the S1 rows assigned): I2.2 India-WRIS loader, M2.1 MRMS download, M2.2 model v1 vs v0.

[ml] 31 Oct cloud session, real-data runs BLOCKED: USGS IV requests older than ~120 days are 301-redirected to nwis.waterservices.usgs.gov, which this environment's network policy refuses (waterservices.usgs.gov itself is allowed). So M1.4, M1.1, I1.1 and M2.1/M2.2 have NOT been run on real data; no real-data numbers exist from this session. Allow nwis.waterservices.usgs.gov, then run the commands below.
[ml] History fetcher done: `ml/fetch_history.py` writes data/raw/usgs/<site>_00065_<year>.json (the layout producers/replay_producer.load_readings and tools/gate_check read), minified, resumable, exponential backoff, stops at once with a clear message if the host is blocked; per-gauge rows/gaps + ml/figures/usgs_fetch_summary.csv. 4 tests (mocked HTTP, round trip through load_readings).
[ml] M2.1 code done: `ml/mrms_history.py` (events = minor crossings from 2020-10-14, the start of the noaa-mrms-pds archive; 48 h before to 12 h after; one matched dry window per event, same gauge, below action, quiet +-3 days; every hourly Pass 2 file decoded in memory, never written; mean depth over 5/10/25 km circles for all 38 gauges at once, so S3 radius sensitivity needs no new download). Measured on real files: 2024-06-01 03Z-06Z, 10 km circle = 294 cells (area predicts ~293), 1-3 s per hour; DRWT2 10 km mean 7.56 mm in the hour ending 03Z before its 06:40Z crossing. 5 tests. Limit: no radar rain for floods before Oct 2020 (incl. the 51 of 2018).
[ml] M2.2 code done: `ml/rain_model.py` v0 (stage) vs v1 (stage + rain_1/3/6/24h) HistGradientBoosting, same rows (windows with 24 h of rain), time split (--split, fails if a side has no crossing), tau from an inner time split of train only, equal gauge/event weights, harness-shaped rows per gauge/POOLED/POOLED-SLOW + rise-rate on the same rows; models to ml/models/*.joblib (ignored), command in ml/MODEL_CARD.md. 5 tests incl. a synthetic end-to-end smoke run (no findings from synthetic data).
[ml] I2.2 done: `india/indiawris_loader.py` (header-row detection, IST -> UTC, date-only rows at 08:00 IST, SIMULATION envelopes with source INDIA-WRIS, warning -> action, danger -> minor), hand-made fixture tests/india/fixtures/indiawris_sample.xlsx (assumed layout, fictitious station; verify against the first real download), `india/thresholds.csv` header-only template. 6 tests.
[ml] I2.1 code done: `india/table1.py` builds Table 1 (CSV + Markdown) from audit_us.csv; India cells read "pending (India-WRIS downloads)" until exports are under data/raw/indiawris/; literature facts in their own column with the source. 2 tests. pytest -q: 97 passed.


## Run locally (or in a cloud session that allows nwis.waterservices.usgs.gov and noaa-mrms-pds.s3.amazonaws.com)
Repo root, `pip install -r requirements-ml.txt` (plus `pytest`; on the laptop the venv `..\spaenv`). All CLIs take
`--data-dir <folder containing raw/usgs>` (default: DATA_DIR env, then ./data), print a start line, one line per
gauge, a progress bar and a final `done ... in N min` line.
1. `pytest -q`
2. History (skip on the laptop, which has the cache): `python -m ml.fetch_history --start-year 2015`
   (resumable; writes ml/figures/usgs_fetch_summary.csv with rows and gaps per gauge).
3. Quick check on the four reference gauges (expect `ok` x4, then ref / measured / diff for the 3 h rise-rate F1):
   `python -m ml.harness --gauges-only DRWT2,LCPT2,JNCT2,AHOT2 --horizons 3 --out ml/figures/baselines_ref.csv`
4. M1.4 all gauges, h = 1..6: `python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv`
   (`--insert` with the Docker DB up writes evaluation_runs). Report differences from the reference F1s, do not tune;
   commit with `git add -f ml/figures/baselines.csv`.
5. M1.1: `python -m ml.dataset --end 2026-10-31 --out ml/figures/pooled_dataset.parquet`
6. I1.1 US: `python -m india.audit --out india/figures/audit_us.csv`, then Table 1: `python -m india.table1`
7. M2.1: `python -m ml.mrms_history --radii 5,10,25 --workers 4` (try `--max-hours 20` first; resumable)
8. M2.2: `python -m ml.rain_model --split 2024-01-01 --horizons 1,2,3,4,5,6 --label m2.2-rain-v1 --out ml/figures/model_v1_vs_v0.csv`
   (pick --split so both sides hold floods; the command prints the flood years per side and refuses an empty side).
9. India (manual, India-WRIS is browser-only): 3 to 5 free stations with known floods (Godavari, Krishna, Mahanadi or
   Periyar), 2015-2025, one year per Excel file, into data/raw/indiawris/; warning/danger levels from CWC bulletins
   into india/thresholds.csv. Check the first file with `python -m india.indiawris_loader data/raw/indiawris`, then
   rerun `python -m india.table1`.
