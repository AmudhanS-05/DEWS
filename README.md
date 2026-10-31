# DEWS: real-time flood and flash-flood forecasting and alerting on Apache Kafka

A student project (RVCE, 7th semester) for **AI372IA Stream Processing and Analytics** and **AI373TA Agentic AI**.
Team: Aaditey Chalva, Amudhan S, Dhaksha Muthukumaran. Mentors: Dr. Vijayalakshmi M. N., Dr. Anupama Kumar.

> **Status: work in progress (October 2026).** The streaming pipeline runs end to end on replayed history. The
> forecaster is still a simple baseline, and **no forecast skill is claimed yet**. See [Status](#status) for what was
> measured.

## What it does, in plain words

Flash floods rise in hours. A river gauge records the water level every 5 to 15 minutes, but the public feed sends
those readings about once an hour, so part of any warning is gone before the data even arrives. DEWS does four things:

1. **Streams** live river-gauge readings (USGS) and, later, radar rainfall (NOAA MRMS) through Apache Kafka as soon as
   they are published.
2. **Builds hourly windows** per gauge in Faust (a Python stream processor). It uses the reading's own timestamp,
   fills silent hours and handles late revisions.
3. **Forecasts** the river stage a few hours ahead. When the forecast reaches one of the gauge's **official NWS flood
   categories** (action, minor, moderate, major), it raises an alert. We never set our own thresholds.
4. **Drafts an advisory**, first from a fixed template within seconds and then from a team of LLM agents that check
   the evidence. **A person must approve it**, and only then does the notifier send the email. Every step is
   timestamped, so the end-to-end delay, including the feed's own delay, is measured, not assumed.

**Wording we keep to:** DEWS gives a *forecast that a gauge will cross an official flood category* within a horizon,
with a probability when the model has one. It does not "predict a flood", flooded areas or damage, and it does not
issue official warnings. It is a decision-support prototype for a duty officer.
[Concepts](docs/guide/01-concepts.md) explains why.

## Architecture

```mermaid
flowchart LR
  subgraph Sources
    USGS[USGS gauges<br/>live, every 5 min]
    MRMS[NOAA MRMS radar rain<br/>planned, S2]
    HIST[cached history<br/>replay = SIMULATION]
  end
  subgraph Kafka["Kafka: 3 brokers, 3 partitions, RF 3"]
    TG[(gauge.usgs)]
    TR[(rain.mrms)]
    TRP[(replay.gauge<br/>replay.rain)]
    TA[(aggregates.window)]
    TF[(forecasts.stage)]
    TAL[(alerts.flood)]
    TAP[(alerts.approved)]
  end
  USGS --> TG
  MRMS -.-> TR
  HIST --> TRP
  TG --> FA[Faust windows<br/>3 workers]
  TRP --> FA
  TR -.-> FA
  FA --> TA --> FC[Forecaster<br/>ml.predict]
  FC --> TF
  FC --> TAL
  TAL --> AG[LangGraph agents<br/>draft v2]
  TG & TA & TF & TAL --> SK[PostgreSQL sink]
  SK --> PG[(PostgreSQL)]
  AG --> PG
  PG --> CON[Console<br/>approve / edit / reject]
  PG --> BI[Power BI<br/>DirectQuery]
  CON -- human approval --> TAP --> NT[Notifier<br/>email + delivery time]
```

Dashed lines are planned. The sink also writes the template advisory (v1) the moment an alert lands. The agents never
publish to Kafka and never send anything. Only the notifier sends, and it reads only `alerts.approved`.

## Real data vs SIMULATION

| Data | Where it comes from | Label |
|---|---|---|
| Live gauge readings, 38 Texas Hill Country gauges | USGS Instantaneous Values, polled every 5 min | `mode = LIVE` |
| Official flood categories per gauge | NOAA NWPS (`config/gauge_locations.csv`) | real |
| Past floods replayed for the demo and evaluation (e.g. Onion Creek, May 2019) | cached USGS history | `mode = SIMULATION`, own topics |
| Seeded agent test alert (`python -m agentic_ai.seed`) | hand-made shape around the 2019 flood; official levels real, stage values illustrative | `mode = SIMULATION` |
| India study inputs (India-WRIS exports, synthetic "ideal India") | manual downloads / generator | `mode = SIMULATION` |

Replayed and synthetic data are labelled SIMULATION all the way through: in topics, tables, alerts and the advisory
text. Live and replay results are always reported separately.

## Status

Only measured results are listed. Source: commit messages and `docs/breadcrumbs/`. Slice plan: `docs/SLICES.md`.

| Part | State | Measured |
|---|---|---|
| Kafka stack | done | 3 brokers (KRaft), 9 topics, RF 3, min ISR 2; one broker stopped, topics stayed available |
| USGS live producer | done | dedupe by sensor series, heartbeats, backfill after a 34 h outage |
| Faust windows (3 workers) | done | May 2019 replay: 119 hourly windows, no window emitted twice; one worker killed mid-replay, recovered in 35.6 s |
| PostgreSQL sink | done | killed mid-replay and restarted: 4233/4233 readings, 0 gaps, 0 duplicates |
| Forecaster (rise-rate baseline) | done | 3903 windows → 3903 forecasts. On the 2019 DRWT2 replay the baseline alerted **only after** the minor crossing, so it gave no lead time there |
| Template advisory, console approval, notifier | code done | tested against PostgreSQL; **no email sent yet** |
| Console | done (Orbit, Pipeline flow, Flat map, Readings & alerts) | Metrics and Agents tabs not built yet |
| Agents (LangGraph) | S1 + S2 code done | 74 agent tests with PostgreSQL, `LLM_PROVIDER=none`; live Groq run not yet re-measured after the reviewer fix |
| ML model (gradient boosting, stage + rain) | code done, synthetic tests only | **no real-data results yet** |
| MRMS rain producer, rain in windows | not built (S2) | MRMS decode checked on real files (10 km circle = 294 cells) |
| India data study | loader, audit and Table 1 code done | no real-data numbers yet |
| Power BI | not built yet | |
| Tests | `pytest -q` | 184 passed + 20 skipped without a test database; 204 passed with PostgreSQL 16 (Python 3.12, 31 Oct 2026) |

The bar any model must beat is the rise-rate baseline from the EDA. At 3 h its F1 is 0.65 at DRWT2, 0.26 at LCPT2,
0.64 at JNCT2 and 0.41 at AHOT2, with under 1 to 2 h of warning (`FAQ.md`). DEWS is not compared with, and makes no
claim against, NWS, NSSL FLASH or GloFAS forecasts.

## Quick start

Windows, PowerShell, from the repo root. Docker Desktop and Python 3.12 are needed. Full steps, including Linux and
macOS, are in [Setup](docs/guide/02-setup.md).

```powershell
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
pip install -r requirements-dev.txt          # everything: pipeline, ML, agents, tests
pytest -q                                    # all green; database tests skip without AGENTS_TEST_DSN
copy .env.example .env                       # then fill in; never commit .env
cd docker; docker compose up -d; cd ..       # 3 Kafka brokers + PostgreSQL
Get-Content storage\views.sql | docker exec -i flood-postgres psql -U spa_user -d disaster_warning
python -m dashboard.flow_monitor             # console at http://127.0.0.1:8765
```

No Docker yet? `python -m dashboard.flow_monitor --demo` shows the console on scripted data. Next, replay the 2019
flood end to end: [Run a demo](docs/guide/03-run-a-demo.md).

## Guide

| | |
|---|---|
| [01 Concepts](docs/guide/01-concepts.md) | Kafka topics, partitions and consumer groups as used here; windows; official flood categories |
| [02 Setup](docs/guide/02-setup.md) | Python, Docker, `.env` (every variable), Linux/macOS notes |
| [03 Run a demo](docs/guide/03-run-a-demo.md) | replay the May 2019 Onion Creek flood and approve an alert |
| [04 Console tour](docs/guide/04-console-tour.md) | what each tab shows and how to read it |
| [05 Power BI](docs/guide/05-power-bi.md) | the analytics report on the same PostgreSQL |
| [06 ML and evaluation](docs/guide/06-ml-and-evaluation.md) | baselines, the model, how it is scored honestly |
| [07 Agents](docs/guide/07-agents.md) | the LangGraph multi-agent advisory writer and its reviewer |
| [08 India study](docs/guide/08-india-study.md) | why the live pipeline uses US data, and what India would need |
| [09 Troubleshooting](docs/guide/09-troubleshooting.md) | known issues and fixes |
| [10 Repo map](docs/guide/10-repo-map.md) | folder by folder |

Team and design documents: `docs/SLICES.md` (plan), `docs/CONTRACTS.md` (interfaces between workstreams),
`docs/BUILD_PHASES.md` (decisions), `docs/RUNBOOK.md` (open steps), `docs/DEMO.md` (demo script), `FAQ.md` (why things
are the way they are). How to contribute: [CONTRIBUTING.md](CONTRIBUTING.md).

## Data sources and credit

USGS Water Services (Instantaneous Values), NOAA National Water Prediction Service (flood categories), NOAA MRMS on
the AWS Open Data registry (`noaa-mrms-pds`), India-WRIS / CWC for the India study. DEWS is not affiliated with or
endorsed by any of them. USGS provisional data may be revised.

Licence: the team has not chosen one yet. Until a `LICENSE` file is added, the default applies (all rights reserved
by the authors); please ask before reusing the code.
