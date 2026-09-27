# Agentic AI layer — Phase 2

Not started yet (see PROJECT_LOG.md / the SPA-vs-Agentic sequencing note in the
main reply). This folder is scaffolded so the interface is decided early:

**Contract with the SPA pipeline:** the agentic layer reads from the `alerts`
and `risk_scores` Postgres tables (or, if we want it fully event-driven, it can
instead subscribe to a Kafka topic — `alerts.disaster` is already reserved for
this in `docker/README.md`). It does not touch `sensor_readings` or
`windowed_aggregates` directly — those are internal to the SPA side.

## Planned shape (maps to AI373TA units)

- **Tool use / function calling** (Unit II-III): tools = query Postgres for
  alert history, look up a station's recent trend, (optionally) call a
  real weather API to corroborate a sensor reading before escalating.
- **Coordinator–Worker–Delegator** (Unit III-IV): a Coordinator agent watches
  new alerts; on a HIGH/CRITICAL alert it delegates to Worker agents —
  e.g. one Worker drafts a human-readable warning message, another checks
  whether nearby stations corroborate the anomaly (reduces false alarms —
  ties directly into the evaluation metric).
- **Memory** (Unit IV): short-term = current incident context; long-term =
  history of past alerts/outcomes for a station, to avoid re-alerting on the
  same ongoing event every window.
- **MCP** (Unit V): if time allows, expose the Postgres alert data as an MCP
  server/tool so the agent framework (LangGraph/CrewAI/AutoGen — pick one
  per Unit III) can call it directly instead of hand-rolled SQL tool code.

## Suggested first slice (once SPA pipeline is producing real alerts)
1. Single agent, polling `alerts` for new unacknowledged rows.
2. Tool: `corroborate(station_id, alert_type)` — checks 1-2 neighboring
   stations' recent risk_scores for the same window.
3. Action: mark the alert `acknowledged_by = 'agent'` with a written
   justification, OR escalate severity if corroborated.
4. Log every agent decision to a new `agent_decisions` table (add to
   `storage/schema.sql` when this phase starts) — this becomes the CO4
   "present the use of modern tools" evidence for the Agentic AI course.
