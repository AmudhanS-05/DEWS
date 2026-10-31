# Contributing

DEWS is a student project with three people and three parallel workstreams, so a few rules keep it coherent. The
authoritative version is [`CLAUDE.md`](CLAUDE.md), which every human and Claude Code session follows.

## Before you change anything

- Read [`docs/guide/`](docs/guide/README.md) (what the system does), `docs/SLICES.md` (what is planned) and
  `docs/CONTRACTS.md` (the interfaces).
- **Stay in your workstream's folders** ([repo map](docs/guide/10-repo-map.md)). Core owns the pipeline, console and
  tools. ML + India owns `ml/`, `india/` and `notebooks/`. Agents owns `agentic_ai/`. Docs owns `README.md`,
  `docs/guide/` and this file.
- **Interfaces change only through `docs/CONTRACTS.md`**, together with a note to every affected workstream.

## Rules that are not negotiable

- **Honesty.** Never claim a result that was not measured. Report live and replay separately. Give lead time gross and
  net of the feed delay. Write "forecast of crossing an official category", never "predicts a flood". Make no claim of
  beating NWS, NSSL FLASH or GloFAS.
- **Labels are the official NWPS categories** (action, minor, moderate, major), never invented thresholds.
- **Replayed and synthetic data carry `mode = SIMULATION`** everywhere downstream.
- **Nothing is sent without human approval.** Agents draft and check; only the notifier sends, and only from
  `alerts.approved`.
- **Secrets live in `.env`** (git-ignored). Never print, log or commit them.

## Making a change

1. Set up as in [02 Setup](docs/guide/02-setup.md) and run `pytest -q`; it must stay green. Add tests for pure logic.
   Tests never need a network or an LLM (`LLM_PROVIDER=none`). PostgreSQL tests run when `AGENTS_TEST_DSN` is set.
2. Dependencies: add them to your workstream's file (`requirements.txt`, `requirements-ml.txt` or
   `requirements-agents.txt`) with a pinned version. `requirements-dev.txt` pulls them all in. Check that
   `pip install -r requirements-dev.txt` still resolves on Python 3.12.
3. Commit only your own folders (`git add <folder>`), with a message that says what was tested and how, including the
   measured numbers.
4. If you change a command, flag or module path, update the guide page that shows it (`docs/guide/`), or tell the docs
   workstream.

## Reporting a problem

Open an issue with the command you ran, the full error and your OS and Python version. Remove anything from `.env`
first.
