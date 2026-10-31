# ML workstream breadcrumbs (tracked; core merges these into BREADCRUMBS.md)

[ml] M1.3 done: `ml/predict.py` baseline stub (rise_rate, 3 h, probabilities None, never raises); 5 contract tests in tests/ml/test_predict.py pass, <50 ms per call (cloud run, synthetic inputs only).
[ml] M1.1 code done (not yet run on real data): `ml/dataset.py` pooled builder (windows via streams/windowing.py unchanged, stage relative to minor, y_cross_h / y_stage_h for h=1..6, event ids, slow flag, per-gauge-equal event weights, time_split). 6 tests on a synthetic generator (tests/ml/synth.py) pass; no results claimed.
[ml] M1.2 code done: `ml/harness.py` writes evaluation_runs-shaped rows per gauge, POOLED and POOLED-SLOW (slow = rise >= 3 h), event metrics, lead time gross and net of 1 h feed delay, distinct storm count, optional INSERT. 5 tests on synthetic data pass; no results claimed.
[ml] M1.4 NOT run (needs cached USGS history on the laptop): see "Run locally" below.
[ml] I1.1 US-side code done: `india/audit.py` (interval, archive length, missing %, longest gap, events, median rise, rise resolvable); 3 tests pass on synthetic series. Real run and the India-WRIS station list need the laptop / manual downloads, see below.

## Run locally (laptop, venv `..\spaenv`, repo root, cached data in data/raw/usgs/)
1. `pytest -q` (expect tests/ml and tests/india green).
2. M1.4 baseline numbers, all gauges, h = 1..6, written to ml/figures/baselines.csv:
   `python -m ml.harness --horizons 1,2,3,4,5,6 --label m1.4-baselines --out ml/figures/baselines.csv`
   Add `--insert` (Docker DB up) to write the rows into `evaluation_runs`. Check the 3 h rows against the handoff
   baselines (DRWT2 F1 0.65, LCPT2 0.26, JNCT2 0.64, AHOT2 0.41); if they differ, report the difference, do not tune.
   Note: gauges in config/gauge_locations.csv with an equal action/minor stage or no cached data are skipped with a message.
3. M1.1 on real data (optional check, writes the pooled table the S3 models will train on):
   `python -c "from ml import dataset; from producers.replay_producer import load_readings as L; import csv; g=list(csv.DictReader(open('config/gauge_locations.csv'))); r=dataset.build_pooled({x['site_no']:L(x['site_no'],'2015-01-01','2025-12-31') for x in g}, {x['site_no']:{k:float(x[k]) for k in ('action','minor','moderate','major')} for x in g}); dataset.to_csv(r,'ml/figures/pooled_dataset.csv'); print(len(r))"`
   (sites without cached data will raise SystemExit: drop them from the list first.)
4. I1.1 US-side audit: `python -m india.audit --out india/figures/audit_us.csv`
5. I1.1 India side (manual, India-WRIS is browser-only): pick 3 to 5 free stations with known floods (Godavari, Krishna,
   Mahanadi or Periyar), download 2015-2025 (one year per Excel file), copy warning/danger levels from CWC bulletins
   into india/thresholds.csv. India-WRIS loader is step B and is not built yet.
