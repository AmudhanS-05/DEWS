# Real-Time Flood & Flash-Flood Forecasting and Alerting (Apache Kafka)

Batch 1 – Topic 2 ("Real-Time Natural Disaster Early Warning Using Apache Kafka").
Joint project for **AI372IA Stream Processing and Analytics** and
**AI373TA Agentic Artificial Intelligence**, RVCE.

**Status: proposed design (rev 3), Phases 0–4 (offline) built.** The hazard choice
is not final until the Phase 0 gate check passes on real data and faculty confirm scope.
Where we are and what's next: `BREADCRUMBS.md`.

## What it does

Live USGS river-gauge readings stream through Kafka; Faust builds event-time windows
per gauge; a forecaster estimates the stage a few hours ahead and whether it will
cross an **official NWPS flood category**; an agent drafts an advisory; a person
approves it; only then does the notifier send it. Every step is timestamped, so
end-to-end latency (including the feed's own delay) is measured, not assumed.

```
USGS IV (live) ┐
MRMS rain*     ├─▶ producers ─▶ gauge.usgs / rain.mrms ─▶ Faust windows ─▶ aggregates.window ─▶ forecaster* ─▶ Postgres ─▶ Power BI*
replay (hist.) ┘                                                                         │
                                       alerts.flood ─▶ agent triage* ─▶ human approval* ─▶ advisories.approved ─▶ notifier*
```
`*` = not built yet. It never says a flood "will occur"; it forecasts a category crossing
with a horizon and probability, and reports lead time net of feed delay.

## Repo layout

| Path | What | Phase |
|---|---|---|
| `tools/gate_check.py`, `tools/staleness_logger.py`, `tools/fetch_sample.py` | decide if a gauge is usable; measure feed delay; save real samples | 0 |
| `docker/` | 3-broker KRaft Kafka + topic init + PostgreSQL (single-broker fallback) | 1 |
| `storage/schema.sql` | tables for readings, windows, scores, alerts, latency, evaluation | 1 |
| `producers/` | live USGS producer, replay producer, dedupe + envelope | 2 |
| `streams/` | event-time windowing logic + Faust app | 3 |
| `ml/` | baselines, metrics, backtest | 4 |
| `agentic_ai/`, `dashboard/` | agent triage + approval; Power BI / Streamlit | 6–7 |
| `common/` | config, USGS/NWPS parsers, hydrology helpers, Kafka producer | all |
| `tests/` | `pytest` suite (32 tests) | all |
| `docs/BUILD_PHASES.md` | how to run and test each phase | |
| `docs/MANUAL_CHECKS.md` | checks to do by hand so nothing bites us later | |
| `PROJECT_LOG.md` / `BREADCRUMBS.md` | decisions and numbers / progress trail | |

## Quick start (Windows, PowerShell)

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
pip install -r requirements-dev.txt
pytest -q
python -m tools.gate_check --years 10      # after filling config/candidate_gauges.csv
```
Then follow `docs/BUILD_PHASES.md`.
