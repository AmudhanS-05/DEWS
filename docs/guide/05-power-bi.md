# 05 Power BI

Power BI Desktop is the analytics view for the project. It connects to the same PostgreSQL as the console in
**DirectQuery** mode, so pages show current rows instead of an imported copy.

> **Status (31 Oct 2026): the report is not built yet.** The step-by-step guide per page will live in
> `dashboard/powerbi/README.md`, which the core workstream writes (pages 1 to 4 across slices S1 to S5). This page
> says what to connect to and how to read the planned pages; it does not duplicate that guide.

## Connect

1. Install Power BI Desktop (Windows only, free).
2. **Get Data → PostgreSQL database**. Server `localhost`, database `disaster_warning`, Data Connectivity mode
   **DirectQuery**. The first time, Power BI may ask you to install the Npgsql provider.
3. Credentials: Database, user `spa_user`, password `spa_pass` (local class-demo values from
   `docker/docker-compose.yml`).
4. Turn on **automatic page refresh** for each page (Format → Page refresh) so the metrics update while a replay runs.

Embedding Power BI in the console would need Power BI Service, a gateway and a licence, so the two are shown side by
side instead.

## What to load

| Table / view | One row per | Use |
|---|---|---|
| `gauges` | gauge | names, location, official levels |
| `gauge_readings` | raw reading | stage trend lines |
| `windowed_aggregates` | gauge × hour | stage min/max/last, rise rate, rain (S2) |
| `risk_scores` | forecast | forecast stage against official levels, model name |
| `alerts`, `alert_overview` | alert | category, probability, status, `raise_to_approval_s`, `approval_to_delivery_s` |
| `current_advisory` | alert | the advisory a person sees (v2 replaces v1) |
| `latency_waterfall` | traced record | seconds per stage: feed delay, ingest, windowing, forecast, alert, human, delivery; `net_total_s` and `gross_total_s` |
| `evaluation_runs` | evaluation row | precision, recall, F1, false alarms, lead time, recovery times |
| `agent_run_summary` | agent run | duration, events, tool calls, review rounds, verdict |
| `feed_staleness` | gauge | age of the newest reading |

The views come from `storage/views.sql` ([02 Setup](02-setup.md#2-start-kafka-and-postgresql)).

## Planned pages and how to read them

| Page | Slice | Shows | Read it as |
|---|---|---|---|
| 1 Gauge status and alerts | S1 | a table of gauges coloured by official category; open alerts and their advisory status | the same picture as the console, with history |
| 2 Rain and stage | S2 | per gauge: radar rain bars and stage line with the official levels drawn as horizontal lines | rain should come before the rise; when it does not, see "uncertain" advisories |
| 3 Evaluation | S3 | detection metrics per gauge, pooled and slow-only; lead time gross and net | always check the **mode** filter: live and replay are never mixed |
| 4 Pipeline health | S5 | latency percentiles per stage, throughput, recovery times from the fault drills | `net_total_s` is what an officer experiences after the feed delay |

**Filter on `mode`** on every page. `SIMULATION` rows are replays and synthetic data, and they must not be read as live
performance.
