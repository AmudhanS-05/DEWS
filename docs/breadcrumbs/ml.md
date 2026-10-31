# ML workstream breadcrumbs (tracked; core merges these into BREADCRUMBS.md)

[ml] M1.3 done: `ml/predict.py` baseline stub (rise_rate, 3 h, probabilities None, never raises); 5 contract tests in tests/ml/test_predict.py pass, <50 ms per call (cloud run, synthetic inputs only).
[ml] M1.1 code done (not yet run on real data): `ml/dataset.py` pooled builder (windows via streams/windowing.py unchanged, stage relative to minor, y_cross_h / y_stage_h for h=1..6, event ids, slow flag, per-gauge-equal event weights, time_split). 6 tests on a synthetic generator (tests/ml/synth.py) pass; no results claimed.
