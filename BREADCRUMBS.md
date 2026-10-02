# Breadcrumbs — flood build

Short trail of where the build is, newest first. One line per step; details go in
`PROJECT_LOG.md`. Resume from the top entry's **Next**.

## Where things live
- Design: Claude Doc "Architecture — Floods & Flash Floods (rev 3, proposed)" (+ tab 2: diagram and real sample data)
- Phases and test steps: `docs/BUILD_PHASES.md`
- Things that can bite us: `docs/MANUAL_CHECKS.md`
- Decisions and numbers: `PROJECT_LOG.md`

## Status board

| Phase | State | Tested how |
|---|---|---|
| 0 Gate checks | built + gauge finder + EDA notebooks | real USGS layout confirmed 2 Oct; gate check **not yet run on real data** |
| 1 Infra | built | schema loads on PG16; compose YAML-validated; **not yet `docker compose up`** |
| 2 Producers | built (USGS live + replay); MRMS not built | unit tests with fake producer; **not run against Kafka** |
| 3 Windowing | built | window logic unit-tested; Faust app import-checked only |
| 4 Forecaster | offline backtest built | unit-tested on synthetic floods; ML model + streaming consumer not built |
| 5 Sink | not started | |
| 6 Alerts/agent/notifier | not started | |
| 7 Dashboard | not started | |
| 8 Fault injection/eval | not started | |

## Trail

- **2026-10-02 (night)** — Gate check, Texas Hill Country, 10 gauges, 10 years, minor stage:
  **0/10 pass per gauge**, but 76 events in total, 26 with rise >= 3 h across 9 gauges, so the
  **pooled set passes**. `gate_check` now prints a pooled verdict. Urban Austin creeks flood often
  but rise in ~1–1.5 h; slower rivers (Onion, Blanco, Barton) rise 4–29 h but flood rarely.
  Only 60 of 134 NWS gauges in the box were scanned; 8 more usable gauges not yet gate-checked.
  Deck v3 (`presentations/SPA_Phase1_v3.pptx`): literature rebuilt from 4 gaps (G1–G4, each mapped
  to an objective), 24 papers 2020–2026, newest first.
  **Next:** decide pooled evaluation; rerun with all usable gauges; run staleness_logger on the 5-min gauges.
- **2026-10-02** — Your run: 33 passed, 1 skipped after `fetch_sample` (real USGS layout confirmed:
  09510200 has 3 series incl. "[PT Backup]"). Added `tools/find_gauges.py` (NWPS bbox search /
  USGS→NWPS id lookup), 3 EDA notebooks (classic, flood-specific, MRMS) executed here against
  simulated services, `docs/REVIEW_WORDING.md` (deck wording = source of truth). Deck updated to
  `presentations/SPA_Phase1_updated.pptx`: 35 papers 2020–2026, summary slide removed, gaps rewritten.
  **Next (you):** `find_gauges --near-usgs 09510200` → `fetch_sample --nwps <lid>` → `pytest` (0 skipped);
  `find_gauges --bbox=... --write` → `gate_check`; run notebooks; fix the Isolation-Forest wording.
- **2026-10-01 (pm)** — Phase I review deck built from the NLP_Phase3 template (29 slides: intro,
  dataset + preliminary EDA, 32 verified papers, summary + gaps, objectives, methodology,
  ≈34% work completed, references). Saved to `presentations/` (git-ignored). Commit hashes in
  this file updated after the history rewrite (rebase on the laptop).
  **Next:** run the gate check, then replace the "Pending" EDA rows with real numbers.
- **2026-10-01** — Built Phases 0–4 (offline) and committed: `4009cf1` gate check, `eed143a` infra,
  `7c20ab1` producers + windowing, `f808b89` baselines/backtest, then docs. 32 tests pass.
  Found: faust-streaming needs a fresh venv with upgraded setuptools; Faust agent test mode
  hangs without a broker, so Phase 3 is verified only on a real broker.
  **Next (you):** fill `config/candidate_gauges.csv`, run `tools.fetch_sample` + `pytest`, then
  `tools.gate_check`. Start `tools.staleness_logger`. Ask faculty the Phase I questions.
  **Next (build):** Phase 4 ML model choice after baseline numbers, then Phase 5 sink.
- **2026-09-30** — Hazard pivot to floods/flash floods; architecture doc rewritten (rev 3,
  proposed) with real sample records; second same-timestamp sensor series found in USGS data.
- **2026-09-28** — Last seismic design (rev 2), commit `f54d255`; superseded.
