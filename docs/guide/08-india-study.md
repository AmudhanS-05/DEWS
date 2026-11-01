# 08 India study: why the live pipeline runs on US data, and what India would need

The team is in India, but the live pipeline runs on Texas gauges. That is a data decision, not a preference. The
India study tests it with the same pipeline and the same metrics, so the gap is measured and not just asserted. Full
plan: [`docs/INDIA_STUDY.md`](../INDIA_STUDY.md). Code: `india/` (ML + India workstream).

> **Status (1 Nov 2026):** the **US side** of the audit (Table 1) has been run on real data (38 gauges). The India-WRIS
> API client works from an Indian connection (the user's laptop). The two stations probed so far, Aheri (Gudem Bridge,
> Pranhita, CWC) and NEELEESWARAM (Periyar), are described by India-WRIS itself as **"MANUAL-Water Level by Staff
> Gauge"**, with readings at 00:00 or 08:00: **manual, about daily** (`india/figures/table1_us_vs_india.md`,
> measurement-method row). That is two stations, not a survey. **No Indian series has been run through the pipeline
> yet, and no India result is claimed.** The console shows India as a *planned* area with no gauges.

## Why US data for the live pipeline

Of the open sources checked, only the US provides all of the following openly, free, and with long archives:

| Need | US | India (as known before the audit) |
|---|---|---|
| Live gauge readings every 5 to 15 min, by API | USGS Instantaneous Values | India-WRIS / CWC: mostly daily values, downloaded by hand one station-year at a time |
| Official flood levels per gauge, machine-readable | NOAA NWPS (action/minor/moderate/major) | CWC warning, danger and highest flood levels, mostly in web pages and PDF bulletins |
| Hourly ~1 km radar rainfall, with archive | NOAA MRMS on AWS | IMD gridded daily rainfall; NASA GPM IMERG half-hourly at 0.1° (satellite) |
| 10+ years of history | yes | varies; CAMELS-IND is daily and ends in 2020 |

India-WRIS also publishes an API specification (`docs/indiawris_api.json`, OpenAPI 3, endpoints such as
`/Dataset/River Water Level`), and `india/indiawris_api.py` is a client for it. It needs an Indian connection, because
the cloud sessions could not reach the site. The first responses are the manual, about-daily staff-gauge readings
described above. Whether any free station gives sub-daily, recent data is still part of the audit.

## The four steps

| Step | Question | How | Output | Code |
|---|---|---|---|---|
| A Audit | what exists, and how does it compare? | the same script measures interval, archive length, missing data, longest gap, events and resolvable rise time for US and Indian stations | Table 1 | `india/audit.py`, `india/table1.py` |
| B Pipeline on Indian data | which stage fails, and why? | India-WRIS exports → reading records (`mode = SIMULATION`, `source = INDIA-WRIS`) → replay → windows → forecaster → evaluation | Table 2, a failure log | `india/indiawris_loader.py`, `india/thresholds.csv` |
| C Degradation on real US data | what does each shortfall cost? | degrade the US data one property at a time (15 min → daily, 1 h → 24 h delay, gaps, missing thresholds) and rerun the evaluation | curves of F1 and net lead time | planned (S4) |
| D Synthetic "ideal India" | does the pipeline work with Indian-monsoon data of US quality? | real IMERG rain → simple runoff model calibrated to observed daily flow → rating curve → 15 min stage with CWC levels | feasibility figure | planned (S5) |

Step C carries the quantitative argument because its input is real data. Step D shows feasibility only: a model scored
on generator output partly learns the generator, so it shows no forecast skill in India, and the paper says so.

Category mapping for Indian stations: CWC **warning level → action** and **danger level → minor**. Moderate and major
stay empty. Highest flood level is kept for context.

## Run it

```powershell
python -m india.audit --out india/figures/audit_us.csv      # US side; needs the cached USGS history (see 06)
python -m india.table1                                     # Table 1 (CSV + Markdown); India cells say "pending" until exports exist
python -m india.indiawris_loader data/raw/indiawris        # check downloaded India-WRIS Excel files
python -m india.indiawris_api --probe                      # one small API request; prints status and the raw body
python -m india.indiawris_api --list-stations              # station catalogue -> data/raw/indiawris/api/stations.csv
python -m india.indiawris_api --fetch CW1PRA000084 --start 2015-01-01 --end 2025-12-31   # one station's history, resumable
```

The API client saves every raw response under `data/raw/indiawris/api/` and writes per-station CSVs that the loader and
the audit read unchanged. Run `--probe` first: the response layout is guessed from the spec.

**Manual part:** choose 3 to 5 free India-WRIS stations with known floods (for example in the Godavari, Krishna,
Mahanadi or Periyar basins). Download 2015 to 2025, one year per Excel file, into `data/raw/indiawris/`. Copy their
warning and danger levels from CWC bulletins into `india/thresholds.csv`, with the source and date. The loader's
column layout is an assumption, so check it against the first real file.

## What we expect to find (not yet measured)

Daily data fills 23 of 24 hourly windows with zero-fill, rise times cannot be resolved, and alerts come with zero or
negative net lead time because a daily value is published after the peak. These are hypotheses for step B to confirm
or refute, not findings.
