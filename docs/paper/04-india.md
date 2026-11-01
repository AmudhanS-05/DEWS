# 4. Why the live pipeline runs on US data: pain points of open Indian data

**Scope (team decision, 1 Nov 2026).** The study stops at a pain-point analysis; by decision, Indian data is not run
through the pipeline [`india/PAIN_POINTS.md`, `india/README.md`, commit `834e50b`]. For each US source the pipeline
uses, we chose the Indian open source that plays the same role and recorded what we met when we tried to use it. We
then measured on real US data what each shortfall would cost.

**Evidence and its strength.** *Observed*: India-WRIS API responses fetched from an Indian connection, including a
station scan for July 2022, agency CWC, 41 basin/tributary and state/district combinations, 83 stations (the station
list is not in the repository). *Measured*: a degradation experiment on 38 USGS gauges, 2024-2026, 64 official minor
crossings. *Unverified*: statements from documentation, marked as such in `india/PAIN_POINTS.md`.

## 4.1 Data sources and their US parallels (Table 1)

[`india/README.md`; `india/figures/table1_us_vs_india.md`]

| Role | US (used, measured) | Indian parallel (observed) |
|---|---|---|
| River stage | USGS IV: median 5 min (5 to 15), telemetry, median 0.8 % missing, 11 years | India-WRIS River Water Level: **about 1 h at most CWC river stations in July 2022** (744 records in 31 days); dams and reservoirs every 3 to 24 h; some stations 12 to 32 records in the month; manual staff gauge on two datums (local zero, MSL) and INSAT telemetry. Off-season intervals not measured |
| Access | REST API, no key, worldwide | POST API, undocumented response, no station catalogue, errors inside HTTP 200, reachable only from India |
| Official levels | NWPS API, all 38 gauges | CWC warning / danger / highest flood level in web pages and daily bulletins, copied by hand |
| Rainfall | MRMS radar, 1 km, minutes after the hour | IMD 0.25° daily; GPM IMERG 0.1° half-hourly with hours of latency (latencies not measured here) |
| Live feed | NWIS IV, 24 to 69 min after measurement (one sample) | none found; CWC forecasts are human-readable products |

## 4.2 What each shortfall costs (Table 6, real US data)

3 h rise-rate rule; detection is the share of the 64 crossings caught [`india/figures/degradation.csv`].

| Scenario | Detection | False alarms per gauge-year |
|---|---|---|
| US data as recorded | **0.52** (s3_v0: 0.45) | 1.95 |
| hourly readings | 0.17 (s3_v0: 0, no alert) | 2.08 |
| 3-hourly | 0.14 | 2.16 |
| daily (dams / off-season case) | 0.05 | 1.18 |
| feed delay 1 h / 6 h / 24 h | 0.12 / 0.05 / 0.02 | 2.19 / 2.24 / 2.25 |
| 10 % / 30 % readings missing | 0.48 / 0.39 | 1.83 / 1.47 |
| threshold estimated from the record (99.9th / 99.5th / 99th percentile) | 0.41 / 0.23 / 0.25 | 2.82 / 5.42 / 5.56 |

The interval and the feed delay dominate; missing readings matter far less. The trained s3_v0 learned from 5 to 15 min
windows and raises no alert at all on hourly or coarser data. A synthetic replay of one real US flood (BDAT2, 4 Jul
2025, source SYNTHETIC) gives a rise-rate alert 1.5 h ahead at 15 min, 0.5 h at hourly, and none at daily
[`india/figures/ideal_india_replay.csv`]. This uses a US hydrograph with Indian-like reporting: it is **not Indian data
and not evidence of forecast skill in India**.

## 4.3 Pain points

Twelve pain points, each with the observation, the US counterpart, why it is not comparable, and the effect
[`india/PAIN_POINTS.md`]:

1. no live feed;
2. hourly-at-best interval, 3 to 24 h at dams;
3. manual readings mixed with telemetry, with no provisional/approved flag;
4. two vertical datums;
5. thresholds not machine-readable;
6. no radar-rain equivalent;
7. no station catalogue;
8. undocumented API with errors inside HTTP 200;
9. geo-restricted access;
10. regulated reservoirs among the stations;
11. unclear licensing (unverified);
12. gaps in the monsoon month.

Points 1, 2 and 5 are binding. Points 3, 4 and 7 to 11 make the data hard to use before any forecast is made. Point 6
limits rain-driven methods, not the stage-based ones used here: in our own US evaluation, adding radar rain did not
improve the forecaster ([03-results](03-results.md)).

## 4.4 What an Indian station would need

To keep the US numbers (recall at least 90 % of the US value, false alarms at most 10 % higher), a station needs
readings at the US interval (hourly is already too coarse), a feed delay below 1 h (the smallest delay tested already
loses most detections), at most about 10 % missing readings, and official machine-readable thresholds
[`india/figures/ideal_india_requirements.md`].

TODO: final recommendation paragraph (an open, near-real-time API for CWC stations with machine-readable warning and
danger levels and a sub-hourly archive), worded as a recommendation that follows from 4.2 and 4.4, not as a finding.
