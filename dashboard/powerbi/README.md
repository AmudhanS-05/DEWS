# Power BI guide

**Project file:** `DEWS.pbip` holds the semantic model and the report pages, and the saved project is the source of truth:
`tools.make_pbip` refuses to overwrite it. Open it in Power BI Desktop; the password is the local one in
`docker/docker-compose.yml`, and it is not stored in these files. Desktop asks once to approve each native query: all are
read-only `SELECT`s. If the files change on disk while Desktop is open, click **Apply external changes** and do not press
Save first: Save writes what Desktop has in memory over them.

**On every page:** a slicer on `mode_list[mode]` (`data_source` for `evaluation_runs`), single select, with no "all" state.
Live and replayed data must never be shown merged. The selected mode shows in the page title.

**Model tables added for the pages** (native queries in DirectQuery; `storage/` is unchanged):

| Table | Why |
|---|---|
| `mode_list` | one mode selection filters every table with a mode column |
| `gauge_levels` | official levels for all 38 gauges, copied from `config/gauge_locations.csv`: the `gauges` table holds only DRWT2, so it cannot drive the gauge slicer or the reference lines. Update the query if that file changes |
| `flood_chart` | stage and the newest forecast per gauge, window and mode, so both chart lines share one axis |
| `latency_stage` | the stages as an axis for the latency bar chart, through the measure `Stage median (s)` |

`alerts[details]` and `agent_events[detail]` are removed from the model: joins to a table with a `jsonb` column fail in
DirectQuery ("Local evaluation of Table.Join"), and no page reads them. Times show in the laptop's local time.

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

## Check that page 1 is live

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

Source: `evaluation_runs`, filtered to the labels `m3.1-s3` and `m3.1-s3-rainrows`, which excludes `fault_drill:`. Slicers: `data_source`, `run_label`, `model`, `horizon_h`, `site_no`.
The page opens on `m3.1-s3`, which has `rise_rate` and `s3_v0`. The v1 models (`s3_v1_joint_r10` at 2 to 5 h, `s3_v1_stack_r10` at 1 and 6 h) are only in `m3.1-s3-rainrows`,
a smaller set of test rows, so compare models only within one label. The `site_no` slicer drives the table by gauge only; cards and charts stay on `POOLED`.
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

## If Power BI is not available

Power BI Desktop runs on Windows only. The console (`python -m dashboard.flow_monitor`) shows the same data on any
platform.
