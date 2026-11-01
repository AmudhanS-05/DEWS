> **Before alert_by_model** (archived 1 Nov): measured on the replay set built before core 6b105c1, when every alert was raised by the stage forecast crossing a category (s3_v0 stage regressor before ml's refit). Superseded by ../agent_eval.md.

# Agent evaluation on replayed alerts (A4.2)

Data: 8 floods from the measured USGS record (AHOT2 2023-10-26, AHOT2 2025-05-29, DRWT2 2019-05-04, HNTT2 2025-07-04, JNCT2 2024-07-23, KRRT2 2025-07-04, LCPT2 2024-01-23, LLAT2 2023-10-26), replayed through core's windowing and forecaster (s3_v0 at 3 h, MRMS rain from ml's cache), mode SIMULATION; every non-retracted alert. All numbers below are measured in this run (agentic_ai/eval/runs.jsonl); replay only, no live alerts.

| metric | multi | single | template | llm_down |
|---|---|---|---|---|
| alerts | 14 | 14 | 14 | 14 |
| draft saved (v2) / written | 14/14 (100 %) | 14/14 (100 %) | 14/14 (100 %) | 0/14 (0 %) |
| template v1 kept | 0 | 0 | 0 | 14 |
| text written by the LLM | 14 | 14 | 0 | 0 |
| final text passes all deterministic checks | 14/14 (100 %) | 7/14 (50 %) | 14/14 (100 %) | - |
| saved on attempt 1 | 14/14 (100 %) | 14/14 (100 %) | 14/14 (100 %) | 0/14 (0 %) |
| mean attempts | 1 | 1 | 1 | 1 |
| faithfulness: texts with every number in the evidence | 14/14 (100 %) | 13/14 (93 %) | 14/14 (100 %) | - |
| faithfulness: numbers found in the evidence | 176/176 (100 %) | 124/125 (99 %) | 309/309 (100 %) | - |
| LLM judge PASS / FAIL (final verdicts) | 14 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| disagreements (evidence vs leading context) | 2 | 0 | 2 | 2 |
| UNCERTAIN in the text | 2 | 0 | 2 | 0 |
| tokens per alert (median) | 3056.0 | 2513.5 | 0.0 | 0.0 |
| tokens total | 43310 | 35147 | 0 | 0 |
| v1 -> v2 time per alert, median s (max) | 2.38 (2.98) | 1.67 (2.01) | 0.12 (0.27) | 0.11 (0.13) |
| checks failed on final texts | none | abbreviation, category_stage, numbers, uncertain, update | none | none |

Reviewer on seeded errors in the LLM-written multi-agent texts (deterministic checks, 14 texts as bases): caught **201 of 201** (100.0 %); base texts flagged although saved: 0.

| error kind | caught / seeded |
|---|---|
| wrong_number | 14 / 14 |
| wrong_probability | 8 / 8 |
| wrong_category | 14 / 14 |
| wrong_category_stage | 14 / 14 |
| invented_threshold | 14 / 14 |
| invented_threshold_known | 14 / 14 |
| invented_category | 14 / 14 |
| missing_simulation | 14 / 14 |
| cause_downstream | 14 / 14 |
| cause_claim | 14 / 14 |
| unknown_abbreviation | 14 / 14 |
| certainty | 14 / 14 |
| null_value | 11 / 11 |
| markdown | 14 / 14 |
| wrong_update | 14 / 14 |

Per alert (multi):

| alert | outcome | attempts | checks | faithful | judge | tokens | s |
|---|---|---|---|---|---|---|---|
| 08150000|action|2024-07-22T20:00:00Z | saved | 1 | pass | 19/19 | PASS | 2999 | 2.43 |
| 08150000|action|2024-07-23T11:00:00Z | saved | 1 | pass | 6/6 | PASS | 2751 | 2.33 |
| 08150000|minor|2024-07-22T20:00:00Z | saved | 1 | pass | 14/14 | PASS | 3065 | 2.53 |
| 08150000|minor|2024-07-23T11:00:00Z | saved | 1 | pass | 14/14 | PASS | 3158 | 2.5 |
| 08151500|minor|2023-10-26T04:00:00Z | saved | 1 | pass | 7/7 | PASS | 2885 | 2.01 |
| 08156800|action|2023-10-24T22:00:00Z | saved | 1 | pass | 13/13 | PASS | 3063 | 2.58 |
| 08156800|action|2025-05-27T06:00:00Z | saved | 1 | pass | 15/15 | PASS | 3199 | 2.22 |
| 08156800|minor|2023-10-26T12:00:00Z | saved | 1 | pass | 12/12 | PASS | 3049 | 2.98 |
| 08156800|minor|2025-05-28T23:00:00Z | saved | 1 | pass | 17/17 | PASS | 3192 | 2.48 |
| 08158700|minor|2019-05-04T00:00:00Z | saved | 1 | pass | 17/17 | PASS | 3419 | 2.65 |
| 08165500|minor|2025-07-04T06:00:00Z | saved | 1 | pass | 7/7 | PASS | 3037 | 2.28 |
| 08166200|action|2025-07-04T07:00:00Z | saved | 1 | pass | 11/11 | PASS | 3001 | 2.08 |
| 08172400|action|2024-01-22T14:00:00Z | saved | 1 | pass | 7/7 | PASS | 3045 | 2.23 |
| 08172400|minor|2024-01-22T14:00:00Z | saved | 1 | pass | 17/17 | PASS | 3447 | 2.3 |
