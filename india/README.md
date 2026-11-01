# India data study (workstream `ml`): results for the paper

All numbers are measured by the commands named, on replayed data (mode SIMULATION). Real Indian station data are not
yet in the analysis: they arrive through `python -m india.indiawris_api --fetch` on a laptop with an Indian connection
(the India-WRIS site is not reachable from the cloud session). Plan and questions: `docs/INDIA_STUDY.md`.

## Step A, data audit (Table 1: `india/figures/table1_us_vs_india.md`, `python -m india.table1`)
On the US side, the 38 USGS gauges report every 5 min (median; 5 to 15 min across gauges), with a median of 0.8 %
of readings missing, a median longest gap of 120 h, 310 official minor-stage crossings in 2015-2025, and a rise time
longer than the reporting interval at all 38 gauges. Official flood categories are machine-readable for all 38 through
the NWPS API. On the Indian side, the two India-WRIS API responses obtained so far (Godavari basin, Pranhita, CWC
station Aheri (Gudem Bridge); Kerala, Ernakulam, CWC station NEELEESWARAM on the Periyar) describe the readings as
manual staff-gauge observations at 00:00 or 00:00 and 08:00, i.e. about daily; the per-station interval, gaps and
event counts are measured by the same audit once the history is fetched. Warning and danger levels were found only
in CWC web pages and bulletins.

## Step B, Indian data through the pipeline (Table 2: `python -m india.replay_india`)
The one-command replay (loader -> replay messages with mode SIMULATION and source INDIA-WRIS -> the stream's hourly
windowing -> 3 h rise-rate -> alerts against the CWC danger level) and the failure-log table are built and tested on
a hand-made fixture. On that fixture of daily readings, 96 % of the hourly windows are zero-filled and the rule misses
the one danger crossing; real Table 2 numbers wait for the fetched data.

## Step C, degradation experiment on real US data (`india/figures/degradation.png`, `python -m india.degrade`)
Taking the real readings of all 38 US gauges over the 2024-2026 test period (64 official minor crossings) and
degrading one property at a time, the share of floods the 3 h rise-rate rule detects falls from 0.52 at the US
reporting interval to 0.17 with hourly readings, 0.14 with 3-hourly and 0.05 with one reading a day at 08:00 IST.
A feed delay of 1 h lowers it to 0.12, 6 h to 0.05 and 24 h to 0.02. Missing readings matter less: 10 % missing gives
0.48 and 30 % gives 0.39. Without official thresholds, a level estimated from the gauge's own record (99.9th
percentile) gives 0.41 with 2.82 instead of 1.95 false alarms per gauge-year, and lower percentiles detect fewer
floods (0.23 to 0.25) with almost three times the false alarms. The trained forecaster s3_v0 (0.45 at baseline)
raises no alert at all on hourly or coarser readings, because it learned from 5- to 15-minute windows.

## Step D, what an Indian station would need (`india/figures/ideal_india_requirements.md`, `python -m india.ideal_india`)
To keep the US numbers (recall at least 90 % of the US value, false alarms at most 10 % higher), a station needs
readings at the US interval (5 to 15 min; hourly is already too coarse), a feed delay below 1 h (the smallest delay
tested, 1 h, already loses most detections), at most about 10 % missing readings, and official machine-readable
thresholds. A synthetic replay of one real US flood (BDAT2, 4 July 2025; site ids SYN-IND-..., source SYNTHETIC)
shows the same pipeline running on both reporting styles: with 15-minute readings both the rise-rate rule and s3_v0
alert 1.5 h before the crossing; with one reading per day at 08:00 IST, 368 of 384 hourly windows are zero-filled and
neither alerts. This is a real US hydrograph with Indian-like reporting, not Indian data and not evidence of forecast
skill in India.
