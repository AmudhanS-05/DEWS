> **Before alert_by_model** (archived 1 Nov): measured on the replay set built before core 6b105c1, when every alert was raised by the stage forecast crossing a category (s3_v0 stage regressor before ml's refit). Superseded by ../ablation.md.

# Ablation: single agent vs multi-agent graph (A5.1)

Same replayed alerts, same LLM, same deterministic checks. Single agent = one LLM call with the raw tool outputs and the same rules in prose; it has no evidence/context workers, no precomputed facts, no reviewer and no revision. Multi-agent = the LangGraph graph (writer + deterministic reviewer + LLM judge, up to 2 revisions). Measured in this run; replay only.

| metric | multi-agent | single agent |
|---|---|---|
| alerts | 14 | 14 |
| text passes all deterministic checks | 14/14 (100 %) | 7/14 (50 %) |
| passes on the first draft | 14/14 (100 %) | 7/14 (50 %) |
| texts with every number in the evidence | 14/14 (100 %) | 13/14 (93 %) |
| numbers found in the evidence | 176/176 (100 %) | 124/125 (99 %) |
| tokens per alert (median) | 3056.0 | 2513.5 |
| time per alert, median s (max) | 2.38 (2.98) | 1.67 (2.01) |
| UNCERTAIN where the signals disagree | 2 of 2 | 0 of 2 |
| checks failed (any text) | none | abbreviation, category_stage, numbers, uncertain, update |

Prompt correction (1 Nov): a first single-agent run told it to give the rise-rate lead time, while the checks require 'within N h (model forecast)' when the rise rate is slower than the forecast; it passed 1/14 (lead_time failed 12). That run is kept in runs_single_v1.jsonl and not used here; the table above is the re-run with the same lead-time rule as the multi-agent writer.

A single-agent text that fails a check would go to the officer only as an unchecked draft; in the multi-agent graph a failing draft is revised or template v1 stays.

Single-agent numbers not in the evidence: 1; of these 1 are within 0.1 of an evidence value (raw data rounded differently from the report precision) and 0 are further off (computed or invented values).

Single-agent check failures by check: category_stage 3, uncertain 2, update 1, abbreviation 1, numbers 1

