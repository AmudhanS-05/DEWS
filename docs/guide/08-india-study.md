# 08 India study: why the live pipeline runs on US data

The team is in India, but the live pipeline streams Texas gauges. The India study explains why, with evidence. For
each US source the pipeline uses, it finds the Indian open source that plays the same role, then documents what stands
in the way.

> **Scope (team decision, 1 Nov 2026):** the study stops at a detailed **pain-point analysis**. By decision, Indian
> data is **not** run through the pipeline. The replay tool built for that (`india/replay_india.py`) stays in the
> repository but has only been tested on a hand-made fixture. Full analysis: [`india/PAIN_POINTS.md`](../../india/PAIN_POINTS.md);
> paper paragraphs: [`india/README.md`](../../india/README.md).

## Evidence, and how strong it is

| Kind | What | Where |
|---|---|---|
| **Observed** | India-WRIS API responses fetched from the user's laptop on an Indian connection: `--discover` (31 Oct 2026) and `--list-stations` for **July 2022, agency CWC: 41 basin/tributary and state/district combinations, 83 stations** (1 Nov 2026) | `india/PAIN_POINTS.md`, Table 1. The station list itself stays on the laptop, not in git |
| **Measured** | a degradation experiment on **real US data**: 38 USGS gauges, 2024-2026, 64 official minor crossings, one property changed at a time (step C) | `india/figures/degradation.csv`, `python -m india.degrade` |
| **Unverified** | statements from documentation, marked as such | `india/PAIN_POINTS.md` |

## Data sources and their US parallels

| Role in the pipeline | US source (used) | Indian parallel | How far it is a parallel |
|---|---|---|---|
| River stage, live and history | USGS Instantaneous Values, 5 to 15 min, telemetry | India-WRIS "River Water Level" (CWC and state agencies) | **about hourly at most CWC river stations in July 2022** (744 records in 31 days); dams and reservoirs every 3 to 24 h; some stations only 12 to 32 records that month; manual staff gauge and INSAT telemetry mixed; two datums; history only, no live feed |
| Data access | NWIS REST API, no key, reachable worldwide | India-WRIS POST API (OpenAPI spec, no key) | undocumented response, no station list, errors inside HTTP 200, reachable only from an Indian connection |
| Official flood levels (the labels) | NWPS API: action, minor, moderate, major | CWC warning, danger, highest flood level | web pages and daily bulletins only, copied by hand (warning → action, danger → minor) |
| Rainfall | MRMS radar, 1 km, hourly products minutes after the hour | IMD gridded rainfall (0.25°, daily), GPM IMERG (0.1°, half-hourly, hours of latency) | far coarser in space and time; latencies not measured here |

Source: `india/README.md`, `india/figures/table1_us_vs_india.md`. Off-season reporting intervals were not measured.

## What each shortfall costs (measured on real US data)

The share of the 64 minor crossings that the 3 h rise-rate rule detects at full US data quality is **0.52**
(`india/figures/degradation.csv`):

| Change, one at a time | Detection | Note |
|---|---|---|
| readings every hour (most CWC river stations, July 2022) | 0.17 | the trained s3_v0 raises **no alert** on hourly or coarser data |
| readings every 3 h | 0.14 | |
| one reading a day (dams / off-season case) | 0.05 | |
| feed delay 1 h / 6 h / 24 h | 0.12 / 0.05 / 0.02 | |
| 10 % / 30 % of readings missing | 0.48 / 0.39 | smaller than interval or delay |
| no official threshold: 99.9th percentile of the gauge's own record instead | 0.41, with 2.82 instead of 1.95 false alarms per gauge-year | an estimate is not an official level |

These are US hydrographs with Indian-like reporting, not Indian results. A synthetic replay of one real US flood
(BDAT2, 4 Jul 2025, source SYNTHETIC) shows the same thing on one event: the rise-rate alert comes 1.5 h before the
crossing at 15 min, 0.5 h at hourly, and not at all at one reading a day (`india/figures/ideal_india_replay.csv`).

## The pain points in one line each

From [`india/PAIN_POINTS.md`](../../india/PAIN_POINTS.md), where each has the observation, the US counterpart, why it
is not comparable, and the measured or expected effect:

1. no live data feed (history and PDF bulletins only);
2. reporting interval: hourly at best, 3 to 24 h at dams, varying by station and season;
3. manual readings mixed with telemetry, with no provisional/approved flag;
4. two vertical datums in one dataset;
5. flood thresholds not machine-readable;
6. no radar-rain equivalent;
7. no station catalogue (stations found by guessing names);
8. API errors hidden inside HTTP 200, undocumented schema;
9. reachable only from India;
10. many listed stations are regulated reservoirs;
11. licensing terms unclear (unverified);
12. gaps in the monsoon month.

**Binding ones:** 1, 2 and 5. To match the US numbers, a station would need readings as frequent as the US gauges
(hourly is already too coarse), a feed delay under 1 h, at most about 10 % missing, and official machine-readable
thresholds (`india/figures/ideal_india_requirements.md`).

## Run it

```powershell
python -m india.audit --out india/figures/audit_us.csv   # US side of Table 1 (needs the cached USGS history)
python -m india.table1                                  # Table 1
python -m india.degrade                                 # step C on real US data -> india/figures/degradation.csv
python -m india.ideal_india                             # requirements + synthetic BDAT2 replay
python -m india.indiawris_api --list-stations           # India-WRIS catalogue scan (Indian connection only)
```
