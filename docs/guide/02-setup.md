# 02 Setup

The team runs DEWS on Windows laptops, so this guide is Windows-first (PowerShell). Linux and macOS differences are
at the end. Plan for about 30 minutes, most of it downloads.

## What you need

| | Version | Why |
|---|---|---|
| Python | **3.12** (tested) | faust-streaming 0.11.3 and confluent-kafka 2.5.3 may not install on 3.13 |
| Docker Desktop | recent, WSL 2 backend on Windows, **at least 4 GB of memory** for Docker | 3 Kafka brokers + PostgreSQL |
| Git | any | |
| Internet | USGS, NOAA and AWS reachable | live data and history downloads. Replay works offline once history is cached |
| Optional | Power BI Desktop (Windows only), a Gmail account, a Groq API key | dashboard, email, LLM agents |

Keep the repo **outside a OneDrive-synced folder** (for example `C:\dev\dews\`). OneDrive syncing `.git` leaves
stale lock files and can corrupt the repository.

## 1. Clone and install

```powershell
git clone https://github.com/AmudhanS-05/DEWS.git C:\dev\dews\DEWS
cd C:\dev\dews\DEWS
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1                 # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -U pip setuptools wheel
pip install -r requirements-dev.txt
pytest -q
```

`requirements-dev.txt` is the single entry point. It pulls in `requirements.txt` (pipeline), `requirements-ml.txt` (ML
and India study) and `requirements-agents.txt` (agents), plus pytest. Upgrade `setuptools` first: an old one fails to
build faust-streaming's `opentracing` dependency.

**Expected test result:** everything passes. Tests that need PostgreSQL are skipped unless `AGENTS_TEST_DSN` is set.
On 31 Oct 2026, on Python 3.12 / Linux: 184 passed and 20 skipped without a database, and 204 passed with one. Those
tests create and drop their own throwaway schema, so pointing them at the Docker database is safe:

```powershell
$env:AGENTS_TEST_DSN = "postgresql://spa_user:spa_pass@127.0.0.1:5432/disaster_warning"; pytest -q
```

## 2. Start Kafka and PostgreSQL

```powershell
cd docker
docker compose up -d
docker compose ps                         # kafka-1/2/3 and postgres "healthy"; topic-init exited 0
docker compose logs topic-init            # 9 topics, PartitionCount 3, ReplicationFactor 3
cd ..
```

Brokers can take 1 to 2 minutes to become healthy on Windows. The compose file creates 9 topics. On a **new, empty**
database volume it also loads `storage/schema.sql` (tables) and `storage/views.sql` (the views that the console's
approval panel and Power BI need). If your volume was created before the views were added to the compose file, apply
them once by hand. The command is idempotent:

```powershell
Get-Content storage\views.sql | docker exec -i flood-postgres psql -U spa_user -d disaster_warning
```

(In bash or cmd: `docker exec -i flood-postgres psql -U spa_user -d disaster_warning < storage/views.sql`.)

| Service | Address | Credentials |
|---|---|---|
| Kafka brokers | `127.0.0.1:9092`, `:9093`, `:9094` | none |
| PostgreSQL 16 | `127.0.0.1:5432`, database `disaster_warning` | `spa_user` / `spa_pass` |

**The database credentials are local demo defaults, for local use only.** They are public in this repository, and the
ports are published on your machine. Before you expose the database or run DEWS anywhere shared, change them in three
places:

1. `POSTGRES_USER` and `POSTGRES_PASSWORD` under the `postgres` service in `docker/docker-compose.yml` (and
   `docker-compose.single.yml`). These apply only when the volume is created, so either recreate it
   (`docker compose down -v`, which wipes data) or change the password inside the running database:
   `docker exec -it flood-postgres psql -U spa_user -d disaster_warning -c "ALTER USER spa_user PASSWORD 'new-password'"`.
2. `PG_DSN` in every terminal that runs DEWS:
   `$env:PG_DSN = "postgresql://spa_user:new-password@127.0.0.1:5432/disaster_warning"`.
3. The Power BI connection credentials.

Also bind the ports to `127.0.0.1` in the compose file (for example `"127.0.0.1:5432:5432"`) if other machines on
your network should not reach them. Kafka here has no authentication at all.

**Laptop too small for 3 brokers?** Use the single-broker file. You lose the broker fault-tolerance demo, and you must
not claim it:

```powershell
cd docker; docker compose -f docker-compose.single.yml up -d; cd ..
$env:KAFKA_BOOTSTRAP = "127.0.0.1:9092"; $env:TOPIC_REPLICATION_FACTOR = "1"   # in every terminal
# the PostgreSQL container is then called flood-postgres-single
```

**Schema changes** only load into an empty volume. `docker compose down -v` wipes all data. On an existing volume,
apply additions with `psql`, as with the views above.

## 3. Create `.env`

```powershell
copy .env.example .env
notepad .env
```

`.env` is git-ignored. **Never commit it, paste it into an issue or print it.** The code reads it but never prints
values. Everything in it is optional: the pipeline, console and replay run without it. You need it for email and
for LLM agents.

### Email (notifier)

| Variable | Example | Meaning |
|---|---|---|
| `NOTIFY_TO` | `team@example.com,officer@example.com` | recipients, comma separated |
| `SMTP_HOST` | `smtp.gmail.com` | mail server |
| `SMTP_PORT` | `587` | 587 = STARTTLS; 465 = SSL |
| `SMTP_USER` | `yourteam@gmail.com` | the sending account |
| `SMTP_PASSWORD` | 16-character app password | **not** your Gmail password |

**Gmail app password:** turn on 2-Step Verification for the Google account, then open
<https://myaccount.google.com/apppasswords>, create a password named "DEWS" and paste the 16 characters (without spaces)
into `SMTP_PASSWORD`. Revoke it there when the project ends. Test without sending anything:
`python -m alerting.notifier --dry-run`.

### Agents (LLM)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq`, `gemini`, `ollama`, or `none`. `none` means no LLM: deterministic text, used by all tests |
| `GROQ_API_KEY` | | free key from <https://console.groq.com> (API Keys) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | any chat model your Groq account lists. See [Troubleshooting](09-troubleshooting.md#groq-model-choice) |
| `LLM_FALLBACK` | `gemini` | tried once if the primary call fails, times out or is rate limited: `gemini`, `groq` or `none`. If that also fails, the template advisory (v1) stays |
| `GEMINI_API_KEY` | | free key from Google AI Studio (<https://aistudio.google.com/apikey>) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | |
| `OLLAMA_MODEL` | `qwen2.5:7b` | only with `LLM_PROVIDER=ollama`: install Ollama, then `ollama pull qwen2.5:7b` |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | not in `.env.example`; add it only if Ollama runs elsewhere |

Every LLM call has a 30 s timeout. A failure never blocks the pipeline: the advisory falls back to the template.

### Tracing (optional)

| Variable | Default | Meaning |
|---|---|---|
| `LANGSMITH_TRACING` | `false` | `true` to send agent traces to LangSmith (free Developer plan) |
| `LANGSMITH_API_KEY` | | from <https://smith.langchain.com> → Settings → API Keys. Tracing is forced off when it is missing |
| `LANGSMITH_PROJECT` | `dews-agents` | not in `.env.example`; add it to use another project name |

Traces contain the prompts and the advisory text, so they leave your machine. Leave tracing off if that matters.

### Settings that are *not* read from `.env`

The core settings in `common/config.py` come from the shell environment only. Set them in each terminal
(`$env:NAME = "value"`) when you need to change them:

| Variable | Default | When to change |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `127.0.0.1:9092,127.0.0.1:9093,127.0.0.1:9094` | single-broker stack: `127.0.0.1:9092` |
| `TOPIC_REPLICATION_FACTOR` | `3` | single-broker stack: `1` |
| `PG_DSN` | `postgresql://spa_user:spa_pass@127.0.0.1:5432/disaster_warning` | another database |
| `DATA_DIR` | `data` | where cached USGS history lives (`data/raw/usgs/`) |
| `FORECAST_HORIZON_H` | `3` | forecaster horizon in hours |
| `AGENTS_TEST_DSN` | unset | enables the PostgreSQL tests |

## 4. Check it works

```powershell
python -m dashboard.flow_monitor            # open http://127.0.0.1:8765
```

The page opens on a landing screen. Scroll down to enter the globe. The header chips should show green
dots for *Kafka*, *brokers 3/3* and *PG*. Every pipeline node shows "not running" or "waiting" until you start
something, and that is expected. Then follow
[03 Run a demo](03-run-a-demo.md).

To see the console without Docker: `python -m dashboard.flow_monitor --demo` (scripted data, marked DEMO DATA).

**One-command start (Windows):** `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1` starts the Docker
stack, three Faust workers, the sink, forecaster, notifier, live producer and console in the background, with logs in
`gate_output\`. `scripts\stop_demo.ps1` stops them (`-KeepDocker`, `-KeepConsole`). **Note:** the scripts currently
look for the team's venv at `..\spaenv` (next to the repo folder), not `.venv`. Until that is configurable, create the
venv there or start the parts by hand as in [03](03-run-a-demo.md).

## Linux and macOS

- Python: `python3.12 -m venv .venv && source .venv/bin/activate`, then the same `pip` and `pytest` lines.
- Docker: Docker Engine with the compose plugin, or Docker Desktop on macOS. Give it at least 4 GB.
- Views: `docker exec -i flood-postgres psql -U spa_user -d disaster_warning < storage/views.sql`.
- Environment variables: `export KAFKA_BOOTSTRAP=127.0.0.1:9092`.
- `eccodes` (MRMS GRIB2 decoding) installs from wheels on Linux x86-64 and macOS. If it fails, only the MRMS tools
  are affected.
- Power BI Desktop is Windows only. Use the console, or any PostgreSQL client, on other systems.

## Getting history for replays

Replays read cached yearly files from `data/raw/usgs/<site>_00065_<year>.json`. Fetch just what you need:

```powershell
python -m ml.fetch_history --gauges-only DRWT2 --start-year 2019 --end-year 2019    # the demo flood, a few MB
python -m ml.fetch_history --start-year 2015                                         # everything: 38 gauges, about 1 GB
```

`python -m tools.gate_check --years 10` also fills this cache (all candidate gauges, 10 years) and writes the gate
summary. History older than about 120 days is served by `nwis.waterservices.usgs.gov` through a redirect. If your
network blocks that host, the fetch stops with a clear message
([Troubleshooting](09-troubleshooting.md#usgs-history-redirect)).
