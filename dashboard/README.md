> **Flood update (1 Oct 2026):** the old weather prototype `streamlit_app.py` was removed;
> it will be rebuilt in Phase 7 against the flood schema (`gauge_readings`,
> `windowed_aggregates`, `risk_scores`, `alerts`, `feed_staleness`). The Power BI steps
> below still apply; table names in step 5 change accordingly.

# Live flow view (works now)

`python -m dashboard.flow_monitor` serves the architecture diagram as a live canvas at
http://127.0.0.1:8765: per-topic msg/s and totals, per-consumer-group instances and lag,
producer heartbeat, broker / under-replicated partition counts and PostgreSQL row counts.
Dashed green lines move while messages flow. Stop a producer, a Faust worker or a broker and
the node changes colour and the change is logged with a timestamp (read recovery time off it).
`--demo` runs it on scripted data without Kafka. Read-only: it never commits offsets.
Producers are judged by what they publish (their topic's newest message), not by their process.

# Dashboard: prototype (here) vs. Power BI (submission)

## Why two dashboards
The project brief specifically asks for **Power BI**. Power BI Desktop only
runs on Windows and needs a live connection to a running database — it can't
run inside this Linux workspace. So:

- **`streamlit_app.py`** here is a quick prototype for day-to-day development —
  run it locally next to the pipeline to sanity-check that data is flowing,
  without needing Power BI installed.
- **Power BI Desktop** (on your own Windows machine) is the actual dashboard
  for demo/submission, connected directly to the same Postgres tables.

## Power BI setup (on your Windows machine, once the pipeline is running)

1. Open Power BI Desktop → **Get Data** → **PostgreSQL database**.
2. Server: `localhost`, Database: `disaster_warning`.
3. Data Connectivity mode: **DirectQuery** (so the dashboard updates as new
   rows land, rather than a one-time import) — needs the PostgreSQL ODBC/Npgsql
   driver installed; Power BI prompts for this the first time.
4. Credentials: user `spa_user`, password `spa_pass` (from `docker-compose.yml`
   — change both before anything beyond the class demo).
5. Load these tables/views:
   - `windowed_aggregates` — trend charts (avg/min/max over time per station)
   - `risk_scores` — a risk-score-over-time line, colored by `is_anomaly`
   - `alerts` — a table/card visual for "active alerts", filtered on
     `acknowledged = false`
   - `evaluation_runs` — for the Phase II results slide (precision/recall/
     latency numbers)
6. Suggested visuals:
   - Line chart: `avg_value` over `window_start`, one line per `station_id`,
     small multiples by `sensor_type`
   - Card: count of unacknowledged CRITICAL/HIGH alerts (this is your
     "early-warning alert mechanism")
   - Table: latest `alerts`, sorted by `raised_at` desc
7. Set the report to auto-refresh (Power BI Service, if publishing) or just
   hit **Refresh** during the live demo to show it's really DirectQuery, not
   a static import — this is the detail examiners tend to check for in the
   "real-time" requirement.

## If Power BI truly isn't available on demo day
Fall back to `streamlit_app.py` and say so explicitly in the report — it's a
legitimate real-time dashboard, just not the specific tool named in the brief.
Note the substitution and the reason in `PROJECT_LOG.md` so it's not a surprise
in the viva.
