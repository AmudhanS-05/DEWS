# 4. Multi-agent advisory writer

## 4.1 Design

An alert holds numbers. A duty officer needs a short, checked, plain-language advisory. DEWS uses two layers:

- **Reactive:** the sink writes a template advisory (v1) the moment an alert lands (`alerting/template.py`). There is
  no LLM, and every number comes from the alert.
- **Deliberative:** a LangGraph graph (Coordinator–Worker–Delegator) writes v2 (`agentic_ai/graph.py`):
  coordinator (triage, plan, "Update n" in an episode) → evidence worker (the gauge's own windows and forecast) ∥
  context worker (radar rain and upstream gauges as **leading** signals; downstream and nearby gauges as
  **supporting** only) → reconcile → writer → reviewer, with at most 2 revisions → save v2, or keep v1.

**Conflict rule** (`agentic_ai/rules.py`): if a *leading* context signal disagrees with the evidence, for example heavy
rain on a flat gauge, the advisory is marked UNCERTAIN and states both signals. Supporting signals never cause a
disagreement.

**Reviewer:** deterministic checks first (`agentic_ai/checks.py`, `CHECKS`): numbers only from the facts; the official
category and stage; no certainty language; probability, horizon and lead time stated or "not available"; SIMULATION
label on replay and never on live data; provisional note; no raw field names; no unknown all-caps abbreviation; at most
900 characters. A model judgement runs second, only on a draft that passed. Each check is also a numbered writer
instruction with the exact values.

**Safety boundary:** the agents read PostgreSQL only, never produce to Kafka and never send email. Only a person's
approval publishes to `alerts.approved`. Any failure (LLM, tool, reviewer) leaves v1 in place.

## 4.2 Results

All runs use the alert `08158700|major|2019-05-04T01:00:00Z` (DRWT2, May 2019 replay, SIMULATION), Groq
`openai/gpt-oss-120b`, runs spaced 65 s apart [31 Oct 2026; commits `defe0a8`,
`b30018a`, `d4165a5`].

**Table 5.** Functional check on one alert.

| Setting | Runs | Saved, reviewer pass | Passed on attempt 1 | Time per advisory | Tokens per advisory |
|---|---|---|---|---|---|
| reasoning effort not set | 5 | 5/5 | 4/5 | 4.2 to 11.0 s | 4.0k to 8.4k |
| reasoning effort low | 5 | 5/5 | 5/5 | 2.0 to 3.1 s | 3.0k to 3.3k |
| reasoning effort low + abbreviation check (default) | 5 | 5/5 | **5/5**, 0 revisions | **2.6 to 3.9 s** | **3.1k to 3.5k** |

- The abbreviation check was added after "NGS" (for NWS) passed both reviewers in 2 of 5 low-effort runs. It did not
  recur in the 5 runs with the check.
- Free-tier limit: back-to-back runs hit Groq's tokens-per-minute 429 on 2 of 5. The template text covered both, and
  nothing was blocked.
- Deterministic path (`LLM_PROVIDER=none`): v2 saved in under 0.1 s, 33 events [`docs/guide/07-agents.md`, run on a
  scratch PostgreSQL 16].
- Tests: 122 pass with PostgreSQL 16 [commit `3b72417`].

## 4.3 Evaluation on replayed alerts

Measured after the functional check above, on 9 alerts raised by the model's probability on 9 replayed USGS floods
(mode SIMULATION, live Groq writer, runs 65 s apart) [`agentic_ai/eval/agent_eval.md`, `agentic_ai/eval/runs.jsonl`]:

| | multi-agent | multi-agent, independent Gemini judge | single agent | template only |
|---|---|---|---|---|
| final text passes all deterministic checks | 9/9 | 9/9 | 6/9 | 9/9 |
| numbers found in the evidence | 142/142 | 147/147 | 70/70 | 193/193 |
| UNCERTAIN where the forecasts disagree | 8 of 8 | 8 of 8 | 8 of 8 | 8 of 8 |
| tokens per alert, median | 3376 | 3346 | 2731 | 0 |
| v1 to v2 time per alert, median | 2.7 s | 2.7 s | 1.86 s | 0.14 s |

- **Reviewer on seeded errors** planted in the 9 LLM-written texts: 135 of 135 caught by the deterministic checks
  [`agentic_ai/eval/agent_eval.md`].
- **LLM down:** 9 of 9 alerts kept the template v1 and nothing was blocked [same file].
- **Not measured:** human approval rate and edit size, and any result on live alerts (none occurred).
