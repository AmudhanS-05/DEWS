# Manual checks (things that can bite us later)

Each item: what to check, why it matters, and when. Tick them in `BREADCRUMBS.md`.
Items marked *unverified* are concerns I could not confirm from a source.

## Before Phase I (5–8 Oct)
- [ ] **What Phase I requires.** Ask faculty: proposal only, or a running demo?
      Decides how much of Phases 1–3 must work by then.
- [ ] **Faust instead of "Kafka Streams"**: get written confirmation.
- [ ] **Hazard scope**: floods with a US live pipeline, India as a retrospective test,
      and the wording "forecast of crossing an official flood category" rather than
      "predicts a flood". Get it confirmed.
- [ ] **Human-approval notification design** accepted for the Agentic AI course.

## Data sources
- [ ] **Run `tools.fetch_sample` and `pytest`** on your machine before trusting any
      parser; the committed fixtures have real values but a reconstructed layout.
- [ ] **USGS service changes** *(unverified)*: USGS has been modernising its water
      data APIs; check waterservices.usgs.gov for retirement notices for the legacy IV
      service before the demo, so it doesn't vanish mid-project.
- [ ] **Backfill time format** *(unverified)*: after downtime the producer sends
      `startDT=YYYY-MM-DDTHH:MM+0000`. Test once by stopping the producer > 6 h.
- [ ] **NWPS gauge ids and categories**: many gauges have no flood categories. The gate
      check reports this; pick gauges that have at least action and minor stages.
- [ ] **Polling etiquette**: keep polling at 5 min or slower; read USGS usage guidance.
- [ ] **MRMS**: choose the live product, confirm its delay, and find a catchment
      boundary source *(unverified)* before building Phase 2b.

## Machine and environment
- [ ] **Python version**: use 3.10–3.12 in a fresh venv; upgrade pip/setuptools first.
      (Seen here: faust-streaming's `opentracing` dependency fails to build with an
      old setuptools.)
- [ ] **Docker Desktop memory**: 3 brokers (512 MB heap each) + Postgres. Give Docker
      at least 4 GB; otherwise use `docker-compose.single.yml` and drop the broker
      fault-tolerance claim.
- [ ] **Line endings**: `.gitattributes` forces LF for .sh/.yml/.sql. If the repo was
      cloned before this commit, run `git add --renormalize .`.
- [ ] **PC clock sync**: latency numbers depend on your clock.
      `w32tm /query /status` should show a recent sync.
- [ ] **OneDrive / sync on the Documents folder**: the repo had stale `.git` lock
      files. If Documents is synced by OneDrive, move the repo out of the synced
      folder; syncing `.git` can corrupt it.
- [ ] **Power BI**: confirm the PostgreSQL connector works in DirectQuery on the
      presenting machine (it may need the Npgsql provider, depending on version).
      The on-premises gateway is only needed for Power BI Service, not Desktop.

## Demo day
- [ ] **Campus network**: test that USGS, NWPS and AWS are reachable from the room.
      If not, run the replay producer from cached data (SIMULATION stamped).
- [ ] **Cached history present** under `data/raw/usgs/` for the demo gauge and event.
- [ ] **Secrets**: Telegram bot token and any passwords in a local `.env`, never in git.

## Honesty checks for the report
- [ ] Live and replay results reported separately, never merged.
- [ ] Lead time reported gross **and** net of feed staleness.
- [ ] False-alarm counts carry the caveat that "no report" is not "no flood".
- [ ] Broker fault tolerance claimed only if the 3-broker stack was actually used.
- [ ] No claim of better forecasts than NSSL FLASH, GloFAS or NWS.
