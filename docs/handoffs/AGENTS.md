# Handoff: multi-agent advisory system (workstream `agents`)

## Mission
Build the Coordinator–Worker–Delegator multi-agent system in LangGraph that turns a forecaster alert into a checked,
plain-language advisory (version 2), plus its evaluation. Design: `docs/BUILD_PHASES.md` section "Phase 6b".
Agents draft and check only: they never publish to Kafka and never send email.

## Read first
`CLAUDE.md`, `docs/SLICES.md` (your rows: A1.1 to A5.1), `docs/CONTRACTS.md` (alerts, `advisories`, `agent_runs`,
`agent_events`, tool list), `docs/BUILD_PHASES.md` Phase 6b, `agentic_ai/README.md`.

## Decisions already made
- LLM: `LLM_PROVIDER=groq` (free tier, `openai/gpt-oss-120b`, key in `.env` as `GROQ_API_KEY`) for quality;
  `ollama` (`qwen2.5:7b`, local, offline) as fallback; `none` = deterministic stubs for tests. Every call has a
  timeout; any failure leaves the template v1 in place.
- Tracing: LangSmith Developer plan (free) through `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`
  (optional; the system works without it).
- Langflow is not used at runtime (LangGraph is the syllabus framework; a second visual runtime would duplicate the
  graph). The console's Agents tab is the live visual.
- Worker split: Evidence = the gauge's own readings and forecast; Context = radar rain, upstream/downstream gauges,
  impacts. Disagreement → advisory marked `uncertain`, both signals stated, logged.
- Episodic memory: repeat alert in an episode → "Update n" wording; officer edits as wording examples; numbers only
  from current evidence.

## You own
`agentic_ai/`, `requirements-agents.txt`, tests under `tests/agents/`. You write rows to `advisories`, `agent_runs`,
`agent_events` only through your own functions. The console's Agents tab is built by core from `agent_events`; give
core a recorded fixture of events early (A1.3) so it can build the view in parallel.

## Work without waiting for core
`python -m agentic_ai.seed` inserts the May 2019 DRWT2 alert (and the windows around it) into PostgreSQL, so the graph
can run before the core forecaster exists. Tests run with `LLM_PROVIDER=none` and no network.

## Starter prompt (paste into Claude Code in the agents worktree)
> Read CLAUDE.md and docs/handoffs/AGENTS.md. You are the agents workstream on branch `agents`. Work through slice S1
> tasks A1.1 to A1.6 in docs/SLICES.md. Use LLM_PROVIDER=none for tests and groq for a live check. Write agent_events
> exactly as in docs/CONTRACTS.md and save a recorded events fixture for the core session. Add a [agents] line to the
> BREADCRUMBS.md in the main repo folder for each finished task, commit on your branch, and stop with a summary when
> S1 is done or you need a key from the user.
