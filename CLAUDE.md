# CLAUDE.md: rules for every session working on this repo

Project: real-time flood and flash-flood forecasting and alerting with Apache Kafka (RVCE, AI372IA Stream Processing
and Analytics + AI373TA Agentic AI). Team: Aaditey Chalva, Amudhan S, Dhaksha Muthukumaran.

Read first: `docs/BUILD_PHASES.md` (plan, slices, decisions), `docs/CONTRACTS.md` (interfaces), `docs/RUNBOOK.md`
(commands and what the console shows), the top of `BREADCRUMBS.md` (where we are), `FAQ.md` (why things are so).

## Rules
- **Stay in your workstream's folders** (table "Who works where" in `docs/BUILD_PHASES.md`). Interfaces change only
  through `docs/CONTRACTS.md` plus a tagged breadcrumb line.
- **Honesty:** never claim a result that was not measured; report live and replay separately; lead time gross and net
  of feed delay; "forecast of crossing an official category", never "predicts a flood".
- **Labels are official NWPS categories** (action, minor, moderate, major), never invented thresholds.
- **Replayed and synthetic data carry `mode = SIMULATION`** everywhere downstream.
- **Nothing is sent without human approval.** Agents draft and check; only the notifier sends, only from
  `alerts.approved`.
- **Keep `pytest -q` green**; add tests for pure logic.
- Secrets live in `.env` (git-ignored); never print or commit them.
- `BREADCRUMBS.md` and `PROJECT_LOG.md` are local notes (git-ignored): add one line per finished step, tagged
  `[core]`, `[ml]`, `[agents]` or `[paper]`.
- Commit only your own folders (`git add <folder>`), with a message that says what was tested and how.

## Environment (Windows laptop)
- Python venv: `..\spaenv` (Python 3.12). Install: `pip install -r requirements-dev.txt`.
- Kafka + PostgreSQL: `cd docker; docker compose up -d` (3 brokers, 9 topics, 9 tables after S1).
- Console: `python -m dashboard.flow_monitor` → http://127.0.0.1:8765 (restart it after code changes).
- Known pins: `aiokafka==0.12.0` and `cramjam` (lz4) for faust-streaming 0.11.3.
