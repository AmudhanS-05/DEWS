# 04 Console tour

The console ("Orbital Flood Watch") is the operations view: what is flowing, where each gauge stands against its
official levels, and the approval desk. Power BI is the analytics view ([05](05-power-bi.md)). Both read the same
PostgreSQL tables, so their numbers agree.

```powershell
python -m dashboard.flow_monitor                       # http://127.0.0.1:8765
python -m dashboard.flow_monitor --demo                # scripted data, no Kafka needed (shows a DEMO DATA chip)
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | 8765 | HTTP port |
| `--interval` | 2.0 | seconds between Kafka probes |
| `--live-stale-s` | 900 | a live producer with no message for this long turns red |
| `--lag-warn` | 500 | consumer lag that turns a node amber |

The console only reads Kafka: it uses throwaway consumers and never commits offsets. Its one write path is the
approval panel. **Restart it after pulling new code**; a banner tells you when the page is newer than the server.

## Header chips

*Kafka reachable / unreachable*, *brokers n/3*, *under-replicated · offline* partitions, *dead letters* (records in
`deadletter.flood`) and *PostgreSQL up / down*. A red banner under the tabs means Kafka cannot be reached. Run
`docker compose ps` to check the stack.

## Orbit (default tab)

A 3D view of the 38 gauges on a latitude/longitude grid. The ground is a grid, not terrain. **Pillar height** is the
newest stage relative to that gauge's own official *major* level, and ticks on the pillar mark its official stages.
**Colour** shows the official category it is in now: teal below action, yellow action, orange minor, red moderate,
purple major, grey no recent reading. A dashed red beam marks an open alert.

- **Live / Replay (SIMULATION)** switches between `gauge.usgs` and `replay.gauge`. Replay is labelled everywhere.
- Drag to orbit, use the wheel to zoom, click a pillar for its card. The buttons set the camera.
- The top-left line counts gauges reporting and gauges at or above minor.

## Pipeline flow

The architecture diagram, driven by live Kafka metrics. Each node shows two lines of numbers:

| Node kind | Lines | Colours |
|---|---|---|
| Producer (USGS live, MRMS live, Replay) | last message age, msg/s | judged by what it publishes, not by its process |
| Topic | msg/s, total, age of newest message | "empty · waiting" until something arrives |
| Consumer group (Faust, Forecaster, Sink, Agent, Notifier) | instances (Faust expects 3), lag | amber = missing instance or lag above `--lag-warn` |
| PostgreSQL | row counts | |
| Human approval, Power BI | information only | |

Green means running or receiving, grey-blue means alive with nothing flowing, amber means degraded, red means stopped
or silent, and a plain dot means no process yet (not built or not started). Moving green dashes show messages flowing.
A **red** edge means data is arriving but its consumer is gone. *MRMS live → Faust* is marked "planned".

**Status changes** (below the diagram) logs every node state change with a timestamp. Stop a Faust worker or a
broker and read the recovery time off this table.

## Flat map

The same gauges on a Leaflet map (tiles bundled, no CDN), with a gauge list on the right, sorted by severity. *Map /
Offline view*: without internet the tiles cannot load, so it switches to a plain position plot. Live gauges with no
reading for 90 minutes show as stale. Needs `config/gauge_locations.csv` (`python -m tools.gauge_locations`).

## Readings & alerts

Filled from PostgreSQL once the sink runs; newest rows first.

- **Advisories for approval**: one card per alert with its current advisory: gauge, category, mode, version
  (**v1 (template)** or **v2 (agents)**), status and the reviewer verdict. The text is editable.
  - **Save edit** stores your text (the original is kept).
  - **Approve** needs your name in *Approver* and a second, in-page confirmation. It marks the advisory approved and
    publishes it to `alerts.approved`. If the publish fails, the database change is rolled back.
  - **Reject** marks it rejected and sends nothing.
- **Latest windows**: gauge, window start, mode, number of readings (0 = zero-filled), stage, rise rate, 3 h rain (empty
  until rain is joined in S2).
- **Alerts**: raised time, gauge, mode, category, probability (empty for the baseline, which gives none), horizon,
  status, delivered time.

## Not built yet

From `docs/SLICES.md`: a **Metrics** tab (throughput, lag, latency waterfall, detection and agent metrics), an
**Agents** tab (the agent graph animated from `agent_events`), and **Story mode** (one replayed flood narrated end to
end). Until they exist, `latency_waterfall` and `agent_run_summary` in PostgreSQL hold the same numbers.
