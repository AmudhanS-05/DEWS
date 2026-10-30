# Handoff: core pipeline and integration (workstream `core`)

## Mission
Own the Kafka pipeline end to end (producers, Faust windows, forecaster consumer, sink, alerting, console, Power BI
guides, fault drills) and integration: at the end of every slice, merge the `ml` and `agents` branches, run
`python -m tools.verify_slice S<n>`, and update `docs/DEMO.md` with the new scene.

## Read first
`CLAUDE.md`, `docs/SLICES.md` (your rows: C1.1 to C5.3), `docs/CONTRACTS.md`, `docs/BUILD_PHASES.md` (phase details
and decisions), `docs/RUNBOOK.md` (pending steps only), top of `BREADCRUMBS.md`.

## Where things stand (30 Oct 2026)
- Done and verified on the laptop: 3-broker Kafka (RF 3, ISR 2, broker-stop test), PostgreSQL schema (8 tables),
  USGS live producer (dedupe, heartbeats, 34 h backfill), replay producer → `replay.gauge`, gauge locations (38),
  console with Pipeline flow, Map (Leaflet bundled) and Readings tabs.
- Code ready, not yet verified: Faust app (needs `pip install "aiokafka[lz4]==0.12.0" cramjam`; it started, joined
  the group, created changelogs, then failed only on the lz4 codec). `forecasts.stage` topic in compose (needs
  `docker compose up -d --force-recreate topic-init`).
- 51 tests pass. Plan B for Faust (plain consumer group + same windowing code) if it is still failing on 31 Oct.

## You own
`common/`, `producers/`, `streams/`, `storage/`, `alerting/`, `dashboard/`, `docker/`, `tools/`, `scripts/`, the
tests for those, and `docs/RUNBOOK.md`, `docs/DEMO.md`, `docs/CONTRACTS.md` (changes announced in breadcrumbs).

## How to work
- Run everything yourself on the laptop: `docker compose`, the producers, three Faust workers, the console, replays,
  `pytest -q`. Use `python -m tools.verify_slice S<n>` as the definition of done (build it in C1.8 and extend it per
  slice).
- After confirming a RUNBOOK step, delete it from `docs/RUNBOOK.md` (that file lists only what is left) and add a
  breadcrumb line with the measured result.
- Ask the user only for the **YOU** items in `docs/SLICES.md` (email password, Power BI clicks, approvals in demos).
- Merge order at slice end: `git merge ml`, `git merge agents`, `pytest -q`, `verify_slice`, DEMO.md scene, commit.

## Starter prompt (paste into Claude Code in the main repo folder)
> Read CLAUDE.md and docs/handoffs/CORE.md. You are the core workstream. If .claude/commands is missing, copy
> docs/claude-commands/*.md into it. Work through slice S1 tasks C1.1 to C1.9 in
> docs/SLICES.md in order. For each: implement, add tests, run it against the local Kafka stack, fix until it passes,
> update docs/RUNBOOK.md (remove what is confirmed), add a [core] breadcrumb with the measured result, commit. Stop
> and summarise when `python -m tools.verify_slice S1` passes or when you need one of the YOU items.
