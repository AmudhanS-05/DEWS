# India data study: what fails with Indian data, what it costs, and what would make it work

Purpose (paper section "Why the live pipeline runs on US data, and what India would need"): show, with the same
pipeline and the same metrics, where open Indian data falls short for real-time flood warning, how much each
shortfall costs, and that the pipeline works on Indian-character data once those shortfalls are removed.

## Research questions
1. **What open Indian data exists** for gauge-level, real-time flood warning, and how does it compare with the US
   sources on the properties the pipeline needs?
2. **What happens when the same pipeline runs on Indian data?** Which stage fails, and why?
3. **How much does each shortfall cost** in detection (F1, false alarms) and net lead time?
4. **With data of US quality but an Indian monsoon regime**, does the pipeline produce timely alerts?

## What we already know (verify in step A)
| Source | What it gives | Known limits |
|---|---|---|
| India-WRIS (CWC + state agencies) | river water level and discharge | daily; downloads capped at one year per request (Excel); only 645 of 4,824 listed gauges free (Peninsular); transboundary basins classified; metadata (locations, catchment areas) sometimes wrong (CAMELS-IND paper, ESSD 2025) |
| CWC Flood Forecasting site and daily flood bulletins | current levels at forecast stations with warning level, danger level, highest flood level | web pages and daily PDF bulletins; no documented open API or machine-readable archive found yet |
| CAMELS-IND (Zenodo) | 472 catchments, streamflow 1980 to 2020 + meteorological forcings | daily; ends 2020 |
| IMD gridded rainfall | 0.25° daily rainfall, long record | daily |
| NASA GPM IMERG | 0.1° half-hourly rainfall, global incl. India; Early, Late and Final runs (latency hours to months) | satellite estimate, coarser than MRMS |
| GloFAS | modelled daily discharge | a model, not observations |

## Step A: data audit (same script for US and India) · ~1 day
`india/audit.py` computes, per source and station: reporting interval, publication delay, archive length, missing
data (%, longest gap), whether official thresholds are machine-readable, access method (API / bulk / manual / PDF),
share of stations free to use, metadata checks (coordinates inside the basin, units, datum), number of events above
the danger level, and whether rise time can be resolved (interval shorter than the median rise).
**Output:** Table 1 of the section (US vs India, one row per property) and a completeness heatmap.
**Your part:** India-WRIS downloads are manual (browser, one year per file). Pick 3 to 5 free stations with known
floods (e.g. in the Godavari, Krishna, Mahanadi or Periyar basins), download 2015 to 2025, and record their warning
and danger levels from CWC bulletins.

## Step B: run the pipeline on Indian data · 1 to 2 days
- `india/indiawris_loader.py` converts the Excel files into our reading envelope (`site_no` = CWC station code,
  `mode = SIMULATION`, `source = INDIA-WRIS`), and `india/thresholds.csv` holds the warning and danger levels as the
  categories (`warning` ≈ action, `danger` ≈ minor, HFL for context).
- Replay through `replay.gauge` → Faust windows → forecaster (baseline) → alerts → evaluation, unchanged; also run
  `tools.gate_check` on it.
- **Output:** Table 2, a failure log: for each pipeline stage, what happened with Indian data and the measured
  number. Expected (to be confirmed by the run, not assumed): no live API for the producer; 23 of 24 hourly windows
  zero-filled with daily data; rise time not resolvable; alerts with zero or negative net lead time because a daily
  value is published after the peak; thresholds only in PDFs; gaps and metadata errors.

## Step C: degradation experiment on real US data · ~1 day (the main quantitative evidence)
Take the US replay data (38 gauges, 10 years) and degrade one property at a time to the Indian levels measured in
step A, then rerun the same evaluation:
- resolution 15 min → 1 h → 3 h → daily;
- publication delay 1 h → 6 h → 24 h;
- missing data at the Indian rate and gap pattern;
- thresholds removed for the share of stations without machine-readable levels.
**Output:** curves of F1 and net lead time against each property (Figure). Because the input is real data, nothing
here depends on a synthetic generator. It answers question 3 directly: "daily data costs X hours of lead time".

## Step D: synthetic "ideal India" dataset · 2 to 3 days
Goal: data with the Indian monsoon character but US-like resolution, latency and machine-readable thresholds.
1. **Rain:** real IMERG half-hourly rainfall over the chosen Indian basin (so the rain is real and Indian).
2. **Runoff:** a simple conceptual model (linear reservoir cascade or Nash unit hydrograph) driven by that rain and
   calibrated so its daily means match the station's observed daily series (India-WRIS or CAMELS-IND), scored with
   KGE/NSE at the daily scale.
3. **Stage:** a rating curve fitted to the station's observed daily level and discharge pairs, applied at 15 min.
4. **Thresholds:** the station's CWC warning and danger levels.
5. **Realism:** sensor noise, a provisional flag, 15-min reporting delivered about hourly (like the US feed).
6. **Validation before use:** daily aggregates match observations; the count of danger-level crossings matches the
   bulletins; monsoon seasonality matches.
Then replay it (`source = SYNTHETIC`) through the unchanged pipeline and evaluate.
**Output:** Figure: the same flood as daily real data (step B: late or missed) and as ideal synthetic data (alert
with lead time).

**Caveats (state them in the paper):** synthetic results show the pipeline is not US-specific and works when the data
has these properties; they do not show forecast skill in India, because a model scored on generator output partly
learns the generator. Step C, on real data, carries the quantitative argument.

## Paper outputs
Table 1 (audit), Table 2 (failure log), Figure (degradation curves), Figure (synthetic India demo), and a short
recommendation: an open real-time API for CWC forecast stations with machine-readable warning and danger levels and
a sub-hourly archive.

## Where it is built
New folder `india/`, owned by the `ml` workstream for all four steps (`docs/handoffs/ML_INDIA.md`); step B replays
the Indian data through the running core pipeline. Slice placement: A in S1 to S2, B in S2 to S3, C in S4, D in S5
(`docs/SLICES.md`).
