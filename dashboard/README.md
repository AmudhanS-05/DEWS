# Console and dashboards

Two front ends read the same pipeline:

| | What it is | Used for |
|---|---|---|
| Console (`dashboard/flow_monitor.py`) | Python standard-library HTTP server plus `dashboard/flow.html` and `dashboard/static/`, no framework | watching the pipeline, approving advisories, the demo |
| Power BI (`dashboard/powerbi/README.md`) | Power BI Desktop on the PostgreSQL views | the dashboard named in the brief |

## Console

```powershell
python -m dashboard.flow_monitor            # http://127.0.0.1:8765; restart it after every Python change
python -m dashboard.flow_monitor --demo     # scripted data, no Kafka needed
```

Options: `--port`, `--interval` (seconds between Kafka probes), `--live-stale-s` (age at which a live feed turns red),
`--lag-warn` (consumer lag that turns a node amber).

What it shows:

- **Pipeline flow:** the architecture as a live diagram with per-topic msg/s and totals, per-consumer-group instances
  and lag, producer heartbeat, broker and under-replicated partition counts, PostgreSQL row counts. Stop a producer, a
  Faust worker or a broker and the node changes colour and the change is logged with a timestamp, so a recovery time
  can be read off it. Producers are judged by what they publish (the newest message on their topic), not by their
  process.
- **Map:** every gauge against its own official NWS levels, live or replay, plus the monitored regions. Needs
  `python -m tools.gauge_locations` once; basemap, imagery and elevation tiles need internet.
- **Readings & alerts:** latest windows and alerts from PostgreSQL, and the advisory panel (Approve, Edit, Reject).
- **Metrics and Agents:** throughput, lag and latency waterfall; the agent graph and the last run from `agent_events`.

Reads Kafka without ever committing offsets. The one thing it writes is a person's decision on an advisory: approving
publishes to `alerts.approved`, which is the only topic the notifier sends from.

## Add a monitored region

Append an entry to `config/regions.json` (`id`, `name`, `provider`, `lat`, `lon`, `gauges_csv`, `enabled`) and provide
its gauge file with the same columns as `config/gauge_locations.csv` (official action, minor, moderate and major stages
per gauge). Readings for any gauge on the topics are matched by `site_no`, so a new area needs no code change.
