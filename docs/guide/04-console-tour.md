# 04 Console tour

The console is the operations view: what is flowing, where each gauge stands against its official levels, the approval
desk, and live metrics. Power BI is the analytics view ([05](05-power-bi.md)). Both read the same PostgreSQL tables,
so their numbers agree. The code is a Python standard-library web server (`dashboard/flow_monitor.py`) plus
`dashboard/flow.html` and `dashboard/static/`, with no framework.

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
approval panel. **Restart it after pulling new code.** Tabs can be opened directly by URL: `#flow`, `#map`, `#data`,
`#metrics`, `#agents`.

## Landing page

The first screen: what the system is, four live numbers (monitored areas, gauges, gauges reporting live, advisories
awaiting approval). Scroll down (or press Enter) to move into the globe. The DEWS logo in the header brings you back.
A small line at the bottom reads "Prototype, not an official warning".

## Header

Tabs, then status chips with a coloured dot. Green means good, amber degraded, red down.

| Chip | Meaning |
|---|---|
| Kafka | the console can reach the brokers ("Kafka down" otherwise, plus a red banner: check `docker compose ps`) |
| brokers n/3 | brokers up |
| URP | under-replicated partitions, and offline ones if any |
| DLQ | messages in `deadletter.flood` (failed parsing or sending) |
| PG | PostgreSQL reachable |

The sun/moon switch changes between light and dark themes. When an alert is open, a line under the header names it,
with a **Review** button that jumps to the approval panel.

## Map

A WebGL globe of the monitored areas that flies down to a regional map.

- **Globe / Flat map** (the sphere and map icons): the 3D globe, or a flat map that zooms out to the whole world.
- **Location**: the monitored area. Today that is *Texas Hill Country* (38 USGS gauges with official NWS stages).
  *India* is shown as a hollow, **planned** marker with no gauges connected. Areas come from `config/regions.json`;
  adding one needs no code change (`dashboard/README.md`).
- **Live / Replay (SIMULATION)** switches between `gauge.usgs` and `replay.gauge`. Replay is labelled everywhere.
- **Gauges** are pillars. **Height** is the newest stage relative to that gauge's own official *major* level, and ticks
  mark its official stages. **Colour** is the official category it is in now: below action, action, minor, moderate,
  major, or grey for no recent reading (the key is in the left panel). A dashed red beam marks an open alert. Click a
  pillar for its card; the list on the right is sorted by severity.
- Icons: **compass** (tilt, or back to north up), **terrain** (real elevation at any zoom), **satellite** imagery, **clock** (timeline scrubber). *Data layers* switch gauges, alerts, the
  radar sweep, range rings, graticule and labels on or off. **Auto-rotate** is for
  presenting. Clicking the alert line flies to that gauge.
- The top bar shows the time and an overall **Status** (NORMAL until a gauge reaches a category). *Scene brief*
  summarises what is on screen.

Basemap, satellite imagery (Esri, NASA night lights) and elevation tiles need internet. Offline, the map says the tiles
are unavailable and draws plain ground, and the gauges still show. Gauge positions need `config/gauge_locations.csv`
(`python -m tools.gauge_locations`).

## Pipeline flow

The architecture diagram, driven by live Kafka metrics. Each node shows two lines of numbers:

| Node kind | Lines | Colours |
|---|---|---|
| Producer (USGS live, MRMS live, Replay) | last message age, msg/s | judged by what it publishes, not by its process |
| Topic | msg/s, total, age of newest message | "empty · waiting" until something arrives |
| Consumer group (Faust, Forecaster, Sink, Agent, Notifier) | instances (Faust expects 3), lag | amber = missing instance or lag above `--lag-warn` |
| PostgreSQL | row counts | |
| Human approval, Power BI | information only | |

Green means running or receiving, grey-blue alive with nothing flowing, amber degraded, red stopped or silent, and a
plain dot means no process yet. Moving green dashes show messages flowing. A **red** edge means data is arriving but its
consumer is gone. *MRMS live → Faust* is marked "planned".

**Status changes** (below the diagram) logs every node state change with a timestamp. Stop a Faust worker or a broker
and read the recovery time off this table; `tools.fault_drill` reads the same log.

## Readings & alerts

Filled from PostgreSQL once the sink runs; newest rows first.

- **Advisories for approval**: one card per alert with its current advisory: gauge, category, mode, version
  (**v1 (template)** or **v2 (agents)**), status and the reviewer verdict. The text is editable.
  - **Save edit** stores your text (the original is kept).
  - **Approve** needs your name in *Approver* and a second, in-page confirmation. It marks the advisory approved and
    publishes it to `alerts.approved`. If the publish fails, the database change is rolled back.
  - **Reject** marks it rejected and sends nothing.
  - The email the notifier then sends is a bulletin: facts from the alert plus exactly the approved text.
- **Latest windows**: gauge, window start, mode, number of readings (0 = zero-filled), stage, rise rate, 3 h rain (empty
  until rain is joined into windows in S2).
- **Alerts**: raised time, gauge, mode, category, probability (empty for the baseline, which gives none), horizon,
  status, delivered time.

## Metrics

- **Throughput**: per topic, messages/s with a sparkline since the page opened, total, and age of the newest message.
- **Consumer lag**: per group, instances, state, lag and its trend. During a replay the lag rises, then drains.
- **Latency waterfall**: from the `latency_waterfall` view. For each pipeline stage it shows the median, the 95th
  percentile and the number of traces measured: feed delay, ingest, windowing, forecast, alert, human approval and
  delivery. **Live and replay are separate**, and totals are shown **gross and net** of the feed delay. A stage with no
  timestamps yet reads "not measured yet", never zero. Bars use a log scale, so read the numbers.

## Agents

- **Agent graph**: coordinator → evidence ∥ context → reconcile → writer → reviewer → save, as in
  [07](07-agents.md). The header line names the last run: alert, mode, duration, review rounds, last verdict and
  disagreements. With no run yet it shows the empty graph and says how to start one.
- **Time per agent, last run**: starts, time, tool calls, tokens and verdict per agent.
- **Events, last run**: the `agent_events` rows, newest first, without the writer's token stream.

## Planned

**Story mode** (S5): one replayed flood narrated end to end, from the gauge on the map through the pipeline, the agents
and the approval gate, with the latency waterfall filling in. An animated live Agents view with a timeline scrubber is
planned for S4.
