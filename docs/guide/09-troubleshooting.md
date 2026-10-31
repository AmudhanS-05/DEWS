# 09 Troubleshooting

Known problems the team has hit, with the fix. Most are on Windows, which is where the project runs.

## Install

**Faust fails with `MetadataRequest_v1 ... has no attribute 'prepare'`.** faust-streaming 0.11.3 sends an old request
type that aiokafka 0.13+ removed. Keep `aiokafka[lz4]==0.12.0`; `requirements.txt` pins it. Something upgraded it if
`pip show aiokafka` says otherwise:
`pip install "aiokafka[lz4]==0.12.0" cramjam==2.11.0`.

**Faust starts, joins the group, then fails on the lz4 codec.** The producers compress with lz4, and aiokafka needs
`cramjam` to read it. Same fix as above.

**`opentracing` fails to build** while installing faust-streaming. Upgrade first:
`python -m pip install -U pip setuptools wheel`.

**confluent-kafka or faust-streaming will not install on Python 3.13.** Use Python 3.12 (`py -3.12 -m venv .venv`).
The full set was verified on 3.12 on 31 Oct 2026.

**`import eccodes` fails.** It is only needed by the MRMS rain tools (`ml/mrms_history.py`). The pipeline, console and
agents work without it.

## Windows

**`UnicodeEncodeError: 'charmap' codec can't encode ...`.** Windows consoles default to cp1252, and LLM text often
contains characters such as U+202F (narrow no-break space) or U+2011 (non-breaking hyphen). `python -m agentic_ai.run`
switches its own output to UTF-8. For any other command, run `$env:PYTHONIOENCODING = "utf-8"` first, or use Windows
Terminal.

**`fatal: Unable to create '.git/index.lock': File exists`, or other stray git lock files.** This usually happens in a
OneDrive-synced folder such as `Documents`. Move the repo out (for example to `C:\dev\dews\`). If no git process is
running, delete the lock file. Do not let OneDrive sync `.git`.

**`The '<' operator is reserved for future use`.** PowerShell has no input redirection. Pipe instead:
`Get-Content storage\views.sql | docker exec -i flood-postgres psql -U spa_user -d disaster_warning`.

**`Activate.ps1 cannot be loaded because running scripts is disabled`.**
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.

**Latency numbers look wrong.** They depend on the PC clock. `w32tm /query /status` should show a recent sync.

**Line-ending errors in shell, YAML or SQL files** (`$'\r': command not found` in `topic-init`). `.gitattributes` forces
LF. A clone made before that needs `git add --renormalize .`.

## Docker, Kafka, PostgreSQL

**Brokers stay "starting" for a minute or two.** That is normal on Windows (60 to 130 s); the healthcheck allows for
it. If they never become healthy, give Docker more memory (4 GB or more) or use `docker-compose.single.yml`.

**`port is already allocated` (9092 or 5432).** Only one compose file can run at a time, and a local PostgreSQL also
uses 5432. Stop the other stack or the local service.

**A schema change in `storage/schema.sql` does not appear.** It loads only into an empty volume. Apply additions with
`psql`, or wipe everything with `docker compose down -v`.

**Console shows "Cannot reach Kafka".** Check `docker compose ps`. On the single-broker stack, set `KAFKA_BOOTSTRAP` in
the console's terminal too.

**Readings & alerts is empty, or says `relation "alert_overview" does not exist`.** Either the sink is not running
(`python -m storage.sink`), or the database volume predates the views and they were never applied (see
[02 Setup](02-setup.md#2-start-kafka-and-postgresql)).

**`scripts\start_demo.ps1` cannot find Python or Faust.** The script looks for the team's venv at `..\spaenv` next to
the repo folder. Create the venv there, or start the components by hand ([03](03-run-a-demo.md#1-start-the-pipeline)).
Its logs are in `gate_output\<name>.log` and `.err`.

**Nodes show "not running".** Expected for components you have not started or that are not built yet (MRMS producer,
agents, notifier).

## Data

**Replay says `no cached data for 08158700`.** Fetch the history first:
`python -m ml.fetch_history --gauges-only DRWT2 --start-year 2019 --end-year 2019`.

**Replaying the same dates again produces no new windows.** Faust already holds state past those dates. Replay another
range, or reset as described in [03](03-run-a-demo.md#running-it-again).

<a id="usgs-history-redirect"></a>
**USGS history fails, or the fetch stops at once with a network message.** USGS serves Instantaneous Values older than
about 120 days through a 301 redirect from `waterservices.usgs.gov` to **`nwis.waterservices.usgs.gov`**. Both hosts
must be reachable. Corporate, campus or cloud-sandbox proxies sometimes allow only the first. `ml.fetch_history` checks
this first and stops with a clear message instead of retrying forever. Run it from a network that allows both hosts.

**USGS or NOAA unreachable on demo day.** Use replay from the cached history; it is marked SIMULATION. Without
internet the Map tab cannot load its basemap, imagery or elevation tiles. It says so and draws plain ground, and the
gauges still show.

## Agents and LLMs

<a id="groq-model-choice"></a>
**Groq model choice.** The default `GROQ_MODEL` is `openai/gpt-oss-120b`. Earlier planning documents mention Llama
3.3 70B; the agents workstream changed the default on 31 Oct 2026. Groq retires and renames models, so if you get a
"model not found" or "decommissioned" error, pick a current chat model from your Groq console and set `GROQ_MODEL` in
`.env`. Free-tier rate limits apply per model. On a rate limit the call is retried once on `LLM_FALLBACK`, and then the
template advisory stays.

**`401 Invalid API Key`.** The key in `.env` is wrong or revoked. The run does not fail: it falls back and keeps or
writes the template text. Check the key in the Groq console.

**The agent run ends `v1_kept`.** The reviewer rejected every draft. The printed reasons say which check failed (for
example "probability not stated" or "SIMULATION label missing"). Template v1 stays, which is the designed behaviour.

**No LangSmith trace.** Tracing needs both `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY`. Without the key it is
switched off silently.

**Ollama unreachable.** Start Ollama and run `ollama pull qwen2.5:7b`, or set `LLM_PROVIDER=groq` or `none`.

## Email

**`missing in .env: NOTIFY_TO, ...`.** The notifier needs `NOTIFY_TO`, `SMTP_HOST`, `SMTP_USER` and `SMTP_PASSWORD`.

**Gmail `535 Username and Password not accepted`.** Use an app password (2-Step Verification on), not the account
password.

**The approved email never arrived.** Check the *DLQ* chip. After 3 failed attempts the record goes to
`deadletter.flood`, and the advisory stays approved with an empty *Delivered* time. Fix SMTP, then restart the
notifier.
