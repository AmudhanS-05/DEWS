# Power BI guide (runbook step 12)

**Project file:** `DEWS.pbip` (written by `python -m tools.make_pbip`) holds the semantic model: every table and view in
DirectQuery, the relationships and the measures. Its one report page is empty: the visuals below need a person. Open it
in Power BI Desktop; the password is the local one in `docker/docker-compose.yml`, and it is not stored in these files.
If the project does not open, connect by hand as below: the pages stay the same. When page 1's check passes, tell the core
session so step 12 can be closed.

**On every page:** a slicer on `mode` (`data_source` for `evaluation_runs`), single select, with no "all" state. Live and
replayed data must never be shown merged. Show the selected mode in the page title.

## Connect

1. Start the stack and the sink: `scripts\start_demo.ps1` (or `cd docker; docker compose up -d`, then
   `python -m storage.sink`).
2. Power BI Desktop → **Get Data** → **PostgreSQL database**.
3. Server `localhost:5432`, database `disaster_warning`, Data Connectivity mode **DirectQuery** (a new row must show on
   refresh; an import would be a static copy). Power BI may ask you to install the Npgsql driver the first time.
4. Credentials: the user and password in the `postgres` service of `docker/docker-compose.yml`. They are local demo
   values; do not reuse them anywhere else, and do not save them into a file in this repo.

## Load for page 1

| Object | Kind | Use |
|---|---|---|
| `gauge_readings` | table | newest readings; this is what proves the page is live |
| `windowed_aggregates` | table | stage over time per gauge |
| `risk_scores` | table | forecast stage over time per gauge |
| `alert_overview` | view (`storage/views.sql`) | one row per alert with its current advisory, raise to approval and approval to delivery seconds |
| `gauges` | table | gauge names and official NWS levels |

Relate `gauges[site_no]` one-to-many to the others.

## Page 1: one flood, end to end

- **Slicer on `mode` with no "all" state** (single select, default `SIMULATION`). Live and replayed data must never be
  shown merged. Put the selected mode in the page title.
- **Slicer on `site_no`.**
- **Line chart:** `windowed_aggregates[stage_last]` and `risk_scores[forecast_stage]` over `window_start`. Reference
  lines at the gauge's official action, minor, moderate and major stages only, never invented thresholds.
- **Table:** `alert_overview`, newest `raised_at` first: category, alert status, advisory version, raise to approval,
  approval to delivery.
- **Card:** newest `gauge_readings[ingested_at]`.

Wording on the page: "forecast of crossing an official category", never "predicts a flood".

## Check (pass condition of step 12)

```powershell
python -m producers.replay_producer --site 08158700 --start 2019-06-06 --end 2019-06-07 --speed 3600
```

Press **Refresh** in Power BI while it runs. The card must move forward and a new replayed reading must appear within
one page refresh.

## Page 2: pipeline health

| Visual | Fields |
|---|---|
| Slicer | `latency_waterfall[mode]` |
| Bar chart: median seconds per stage | measures `Feed delay`, `Ingest`, `Windowing`, `Forecast`, `Alert`, `Human approval`, `Delivery`, each `, median (s)`. Filter `instrumented` = true: older traces have no full timing |
| Cards | `Total net of feed delay, median (s)` and `Total gross, median (s)`, always side by side. A replay's gross runs from when the replay sent the reading; feed delay is live only |
| Table: trace detail | `latency_trace`: `site_no`, `reading_time`, `fetched_at`, `ingest_time`, `window_close_time`, `forecast_time`, `alert_time`, `approved_time`, `delivered_time` |
| Line chart: feed staleness (live page only) | `feed_staleness[staleness_min]` over `reading_time`, by `site_no` |
| Table: fault drills | `evaluation_runs` filtered to `run_label` starting `fault_drill:`: `run_label`, `status`, `recovery_time_s`, `failure_reason`, `created_at`. Failed runs stay in the table. Cards: `Drill recovery, median (s)`, `Drills failed` |

## Page 3: forecast evaluation

Source: `evaluation_runs`, filtered to rows that are not `fault_drill:`. Slicers: `data_source`, `run_label`, `model`, `horizon_h`, `site_no`.
Rows with `site_no` `POOLED` or `POOLED-SLOW` are summaries and have no row in `gauges`: never add them to the per-gauge rows.

| Visual | Fields |
|---|---|
| Clustered bar by model | `precision`, `recall`, `f1` at the selected horizon, `site_no` = `POOLED` |
| Line by horizon | `f1` over `horizon_h`, one line per `model`, `site_no` = `POOLED` |
| Table by gauge | `site_no`, `model`, `horizon_h`, `precision`, `recall`, `f1`, `false_alarms`, `median_lead_time_h`, `notes` |
| Cards | `False alarms`, `Lead time, median of rows (h)` |

`notes` holds the lead time gross and net of the feed delay: quote both. Wording on the page: "forecast of crossing an official
category". The model's measured limits are in `ml/MODEL_CARD.md`.

## Page 4: agents

| Visual | Fields |
|---|---|
| Slicer | `agent_run_metrics[mode]` (a native query in the model: tokens sit inside `agent_events[detail]`) |
| Cards | `Agent runs`, `Tokens per run`, `Agent duration, median (s)` |
| Table: runs | `agent_run_metrics`: `run_id`, `alert_id`, `duration_s`, `tokens`, `tool_calls`, `revisions`, `disagreements`, with `agent_run_summary[last_verdict]` and `review_rounds` joined on `run_id` |
| Stacked bar: events per agent | `agent_events`: `agent` on the axis, count of `event_id`, `kind` as legend; filter out `token_chunk` |
| Table: drafts | `advisories`: `alert_id`, `version`, `source`, `status`, `confidence`, `reviewer_verdict`, `created_at`, `approved_at`, `delivered_at`; hide `text` from the table to keep it readable |

Rows with mode `no alert row` are seed or test runs. Do not count them with replay or live runs.

## Page 5: India and the US

**Get Data → Text/CSV** → `india/figures/table1_us_vs_india.csv`, **Import** (it is a file; the rest stays DirectQuery). A table of
`property`, `us_usgs`, `us_provenance`, `india_measured`, `india_literature`, `india_provenance`. Keep both provenance columns, and keep
measured and literature values in separate columns: a cell reading "pending" stays "pending". This page has no live or replay data, so
it needs no mode slicer; say so in its title.

## If Power BI is not available on demo day

The console (`python -m dashboard.flow_monitor`) shows the same data. State the substitution and the reason in the
report; the brief names Power BI.
