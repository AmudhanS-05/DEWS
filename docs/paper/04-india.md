# 4. Why the live pipeline runs on US data, and what India would need

The team is in India, but DEWS streams Texas gauges. This section tests that choice with the same code and metrics
instead of asserting it. Plan: `docs/INDIA_STUDY.md` (steps A to D).

## 4.1 Table 1: the data side by side (step A)

Source: `india/figures/table1_us_vs_india.md` (`python -m india.audit`, `python -m india.table1`; US side measured on
38 gauges, 2015 to 2025).

| Property | US (USGS IV), measured | India, as observed or reported |
|---|---|---|
| Reporting interval | median 5 min (5 to 15) | **manual staff-gauge readings at 00:00 / 08:00, about daily**, at the two stations probed: Aheri (Gudem Bridge, Pranhita, CWC) and NEELEESWARAM (Periyar) |
| Publication delay | 24 to 69 min (one live sample) | no live feed documented; daily values |
| Archive length | 11.0 years (9.2 to 11.0) | TODO: measured per station once fetched |
| Missing data | median 0.8 % (0 to 36.6) | TODO |
| Official thresholds machine-readable | yes, NWPS API, 38 of 38 gauges | warning / danger / HFL in CWC web pages and PDF bulletins; no open API found |
| Access | REST API, no key | POST API from an Indian connection (OpenAPI spec `docs/indiawris_api.json`), or manual Excel, one year per file |
| Free stations | all USGS IV data public | 645 of 4,824 listed gauges (Peninsular) [CAMELS-IND, ESSD 2025] |
| Events above threshold | 310 minor crossings, median 5 per station | TODO: needs downloads + `india/thresholds.csv` |
| Rise time resolvable (interval < median rise) | 38 of 38 | expected no with daily data. TODO: confirm |

The measurement method comes from India-WRIS's own `description` field, "MANUAL-Water Level by Staff Gauge", in API
responses fetched from the user's laptop on 31 Oct 2026 [commit `9ec5a7d`, `docs/breadcrumbs/ml.md`]. **Two stations
are not a survey**; the catalogue scan (`python -m india.indiawris_api --list-stations`) is the next step.

## 4.2 Steps B to D

| Step | Question | State |
|---|---|---|
| B: pipeline on Indian data | which stage fails, and why? | loader and API client ready (`india/indiawris_loader.py`, `india/indiawris_api.py`). **TODO: run 3 to 5 stations through replay → windows → forecaster; Table 2 failure log** |
| C: degradation of real US data | what does each shortfall cost? | **TODO (planned S4):** F1 and net lead time against resolution (15 min → daily), delay (1 h → 24 h), gaps and missing thresholds |
| D: synthetic "ideal India" | does the pipeline work on Indian-regime data of US quality? | **TODO (planned S5):** feasibility only, and the text must say it shows no forecast skill in India |

## 4.3 What can already be said

- With US data, the stage-only forecaster's median net lead time at 3 h is already about −0.5 h (Table 3). A daily,
  manually read series cannot resolve a rise of a few hours. TODO: replace this argument with step C's measured curve.
- Machine-readable official thresholds exist for every US gauge used. For the Indian stations they are copied by hand
  from bulletins (`india/thresholds.csv` is a header-only template).

TODO: recommendation paragraph (an open real-time API for CWC forecast stations with machine-readable warning and
danger levels and a sub-hourly archive), worded as a recommendation, not a finding.
