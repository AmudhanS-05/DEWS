# Contributing

Thanks for looking at DEWS. Issues and pull requests are welcome. A few rules keep the project honest and coherent.

## Before you change anything

- Read [`docs/guide/`](docs/guide/README.md) (what the system does) and [`docs/CONTRACTS.md`](docs/CONTRACTS.md) (the
  record fields, tables and function signatures between the parts).
- The code is split into three parts that meet only at those contracts: the **pipeline** (`common/`, `producers/`,
  `streams/`, `storage/`, `alerting/`, `dashboard/`, `docker/`, `tools/`, `scripts/`), **ML** (`ml/`, `notebooks/`)
  and the **agents** (`agentic_ai/`). See the [repo map](docs/guide/09-repo-map.md).
- **Interfaces change only through `docs/CONTRACTS.md`**: update it in the same change, and update every part that
  reads the interface.

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
2. Dependencies: add them to the part's file (`requirements.txt`, `requirements-ml.txt` or
   `requirements-agents.txt`) with a pinned version. `requirements-dev.txt` pulls them all in. Check that
   `pip install -r requirements-dev.txt` still resolves on Python 3.12.
3. Write a commit message that says what was tested and how, including the measured numbers.
4. If you change a command, flag or module path, update the guide page that shows it (`docs/guide/`).

## Reporting a problem

Open an issue with the command you ran, the full error and your OS and Python version. Remove anything from `.env`
first.
