# India data study (workstream `ml`): results for the paper

**Scope decision (team, 1 Nov 2026).** The aim was to try the pipeline on Indian data in order to collect the pain
points. The pain points are listed in detail in `india/PAIN_POINTS.md`, so the pipeline is **not** run on Indian
data. The replay tool built for that run (`india/replay_india.py`, step B) stays in the repository as a tool and has
only been tested on a hand-made fixture. All numbers below are measured by the commands named, on replayed US data
(mode SIMULATION), or observed in India-WRIS API responses from the user's laptop.

## Data sources and their US parallels
We chose, for each US source the pipeline uses, the Indian open source that plays the same role:

| Role in the pipeline | US source (used) | Indian parallel (chosen) | How far it is a parallel (details: PAIN_POINTS.md) |
|---|---|---|---|
| River stage, live and history | USGS Instantaneous Values (gage height 00065), 5 to 15 min, telemetry | India-WRIS "River Water Level" (CWC and state agencies) | about hourly at most CWC river stations in July 2022, 3 to 24 h at dams, manual and telemetry mixed, two datums; history only, no live feed |
| Data access | NWIS REST API (JSON), no key, worldwide | India-WRIS POST API (OpenAPI spec, no key) | undocumented response, no station list, errors inside HTTP 200, reachable only from an Indian connection |
| Official flood levels (the labels) | NWPS API: action, minor, moderate, major stages | CWC warning level, danger level, highest flood level | in web pages and daily bulletins only, copied by hand; warning -> action, danger -> minor |
| Rainfall | MRMS radar QPE, 1 km, 2 min, hourly products minutes after the hour, public S3 archive | IMD gridded rainfall (0.25 degree, daily), India-WRIS rainfall, GPM IMERG (0.1 degree, half-hourly, hours of latency) | far coarser in space and time; latencies not measured here |
| Real-time forecasts (context) | NWS / NWPS forecasts | CWC flood forecasts | human-readable products, not data feeds |

## Step A, data audit (Table 1: `india/figures/table1_us_vs_india.md`, `python -m india.table1`)
On the US side, the 38 USGS gauges report every 5 min (median; 5 to 15 min across gauges), with a median of 0.8 %
of readings missing, a median longest gap of 120 h, 310 official minor-stage crossings in 2015-2025, and a rise time
longer than the reporting interval at all 38 gauges. Official flood categories are machine-readable for all 38 through
the NWPS API. On the Indian side, a scan of India-WRIS for July 2022 (agency CWC, 41 basin/tributary and state/district
combinations, 83 stations; `python -m india.indiawris_api --list-stations` on the user's laptop) found most river
stations reporting about every hour (744 records in 31 days), dams and reservoirs every 3 to 24 h, some stations only
12 to 32 records in the month, and three measurement methods (manual staff gauge on a local zero, manual staff gauge
on mean sea level, and INSAT telemetry). Off-season intervals were not measured. Warning and danger levels were found
only in CWC web pages and bulletins. The per-station audit was not run, by the scope decision above.

## Step B, Indian data through the pipeline (tool only, not run by decision; `python -m india.replay_india`)
The one-command replay (loader -> replay messages with mode SIMULATION and source INDIA-WRIS -> the stream's hourly
windowing -> 3 h rise-rate -> alerts against the CWC danger level) and the failure-log table are built and tested on
a hand-made fixture. On that fixture of daily readings, 96 % of the hourly windows are zero-filled and the rule misses
the one danger crossing. These are fixture numbers, not findings; Table 2 is replaced by `india/PAIN_POINTS.md`.

## Step C, degradation experiment on real US data (`india/figures/degradation.png`, `python -m india.degrade`)
Taking the real readings of all 38 US gauges over the 2024-2026 test period (64 official minor crossings) and
degrading one property at a time, the share of floods the 3 h rise-rate rule detects falls from 0.52 at the US
reporting interval to 0.17 with hourly readings (what most CWC river stations reported in July 2022), 0.14 with
3-hourly and 0.05 with one reading a day (the dams/off-season case).
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
shows the same pipeline running on three reporting styles: with 15-minute readings both the rise-rate rule and
s3_v0 alert 1.5 h before the crossing; with hourly readings (the CWC monsoon case) rise-rate alerts 0.5 h before and
s3_v0 does not alert; with one reading per day (the dams/off-season case), 368 of 384 hourly windows are zero-filled and
neither alerts. This is a real US hydrograph with Indian-like reporting, not Indian data and not evidence of forecast
skill in India.
