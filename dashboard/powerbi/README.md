# Power BI guide, page 1 (runbook step 12)

No report file exists yet. Building it needs a person with Power BI Desktop (Windows). These are the steps; when the
check at the end passes, tell the core session so step 12 can be closed.

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

## If Power BI is not available on demo day

The console (`python -m dashboard.flow_monitor`) shows the same data. State the substitution and the reason in the
report; the brief names Power BI.
