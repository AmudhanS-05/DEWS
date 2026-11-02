# Ablation: single agent vs multi-agent graph (A5.1)

Same replayed alerts, same LLM, same deterministic checks. Single agent = one LLM call with the raw tool outputs and the same rules in prose; it has no evidence/context workers, no precomputed facts, no reviewer and no revision. Multi-agent = the LangGraph graph (writer + deterministic reviewer + LLM judge, up to 2 revisions). Measured in this run; replay only.

| metric | multi-agent | single agent |
|---|---|---|
| alerts | 9 | 9 |
| text passes all deterministic checks | 9/9 (100 %) | 6/9 (67 %) |
| passes on the first draft | 8/9 (89 %) | 6/9 (67 %) |
| texts with every number in the evidence | 9/9 (100 %) | 9/9 (100 %) |
| numbers found in the evidence | 142/142 (100 %) | 70/70 (100 %) |
| tokens per alert (median) | 3376 | 2731 |
| time per alert, median s (max) | 2.7 (17.71) | 1.86 (1.97) |
| UNCERTAIN where needed (forecasts or signals disagree) | 8 of 8 | 8 of 8 |
| checks failed (any text) | none | precision, uncertain, update |

Earlier prompt correction (1 Nov, before alert_by_model): a first single-agent run told it to give the rise-rate lead time, while the checks require 'within N h (model forecast)' when the rise rate is slower than the forecast; it passed 1/14 (lead_time failed 12). That run is kept in runs_single_v1.jsonl and not used; that table was re-run with the same lead-time rule as the multi-agent writer.

A single-agent text that fails a check would go to the officer only as an unchecked draft; in the multi-agent graph a failing draft is revised or template v1 stays.

Single-agent numbers not in the evidence: 0; of these 0 are within 0.1 of an evidence value (raw data rounded differently from the report precision) and 0 are further off (computed or invented values).

Single-agent check failures by check: update 1, uncertain 1, precision 1

## Before and after core's alert_by_model rule

Before: 8 floods, 14 alerts (7 ACTION, 7 MINOR), all raised by the stage forecast crossing a category (before_alert_by_model/). After (tables above): an alert is raised when the model's p_minor >= tau at 3 h; the replay set was rebuilt through core 6b105c1 with 4 more floods (12 floods replayed; 9 raised an alert, DRWT2 2019, KRRT2 2025 and LLAT2 2024 raised none); alerts are MINOR only, and in 8 of the 9 the model's own stage forecast stays below MINOR (the two forecasts disagree). The single agent's rules gained the model-probability rule so both systems get the same instructions. Same LLM and checks.

| | multi before | multi now | single before | single now |
|---|---|---|---|---|
| alerts | 14 | 9 | 14 | 9 |
| pass all checks | 14/14 (100 %) | 9/9 (100 %) | 7/14 (50 %) | 6/9 (67 %) |
| saved / passing on attempt 1 | 14/14 (100 %) | 8/9 (89 %) | 7/14 (50 %) | 6/9 (67 %) |
| numbers in the evidence | 176/176 (100 %) | 142/142 (100 %) | 124/125 (99 %) | 70/70 (100 %) |
| tokens median | 3056.0 | 3376 | 2513.5 | 2731 |
| time median s | 2.38 | 2.7 | 1.67 | 1.86 |
| UNCERTAIN where needed | 2 of 2 | 8 of 8 | 0 of 2 | 8 of 8 |

## Independent judge

User decision 1 Nov ("make Gemini the judge"): the reviewer's LLM judgement runs on JUDGE_PROVIDER instead of the writer's model. Same replayed alerts, same writer (Groq) and deterministic checks; only the judge differs. In the multi-agent table above the judge was the writer's own model (Groq judged its own draft; measured before this change). A judge call that fails is recorded as "judge unavailable" and the deterministic checks decide (no fallback to the writer's model). Measured; replay only.

Judge: gemini gemini-3.5-flash-lite.

| metric | multi, judge = writer (Groq) | multi, independent judge |
|---|---|---|
| alerts | 9 | 9 |
| draft saved (v2) | 9/9 (100 %) | 9/9 (100 %) |
| text passes all deterministic checks | 9/9 (100 %) | 9/9 (100 %) |
| saved on attempt 1 | 8/9 (89 %) | 9/9 (100 %) |
| mean attempts | 1.22 | 1 |
| judge PASS / FAIL (final verdicts) | 8 / 0 | 9 / 0 |
| judge calls / FAIL on any attempt / unavailable | not recorded | 9 / 0 / 0 |
| numbers found in the evidence | 142/142 (100 %) | 147/147 (100 %) |
| UNCERTAIN where needed | 8 of 8 | 8 of 8 |
| tokens per alert, median (total) | 3376 (35258) | 3346 (30435) |
| time per alert, median s (max) | 2.7 (17.71) | 2.7 (3.37) |
| same-model judge warnings | - | 0 |

Independent judge FAIL reasons (any attempt): none

