# Agent evaluation on replayed alerts (A4.2)

Data: 9 floods from the measured USGS record (AHOT2 2023-10-26, AHOT2 2024-05-02, AHOT2 2025-05-29, GBCT2 2021-10-14, HNTT2 2025-07-04, JNCT2 2024-07-23, LCPT2 2024-01-23, LLAT2 2023-10-26, SIOT2 2025-06-12), replayed through core's windowing and forecaster (s3_v0 at 3 h, MRMS rain from ml's cache; since core 6b105c1 an alert is raised by the model's probability, alert_by_model: p_minor >= tau), mode SIMULATION; every alert raised. Earlier tables (stage-forecast rule): before_alert_by_model/agent_eval.md. All numbers below are measured in this run (agentic_ai/eval/runs.jsonl); replay only, no live alerts.

| metric | multi | multi_ij | single | template | llm_down |
|---|---|---|---|---|---|
| alerts | 9 | 9 | 9 | 9 | 9 |
| draft saved (v2) / written | 9/9 (100 %) | 9/9 (100 %) | 9/9 (100 %) | 9/9 (100 %) | 0/9 (0 %) |
| template v1 kept | 0 | 0 | 0 | 0 | 9 |
| text written by the LLM | 9 | 9 | 9 | 0 | 0 |
| final text passes all deterministic checks | 9/9 (100 %) | 9/9 (100 %) | 6/9 (67 %) | 9/9 (100 %) | - |
| saved on attempt 1 | 8/9 (89 %) | 9/9 (100 %) | 9/9 (100 %) | 9/9 (100 %) | 0/9 (0 %) |
| mean attempts | 1.22 | 1 | 1 | 1 | 1 |
| faithfulness: texts with every number in the evidence | 9/9 (100 %) | 9/9 (100 %) | 9/9 (100 %) | 9/9 (100 %) | - |
| faithfulness: numbers found in the evidence | 142/142 (100 %) | 147/147 (100 %) | 70/70 (100 %) | 193/193 (100 %) | - |
| LLM judge PASS / FAIL (final verdicts) | 8 / 0 | 9 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| judge calls / FAIL (any attempt) / unavailable | - | 9 / 0 / 0 | - | - | - |
| alerts raised by the model's probability (alert_by_model) | 9 | 9 | 9 | 9 | 9 |
| of these, stage forecast below the category (forecasts disagree) | 8 | 8 | 8 | 8 | 8 |
| disagreements (evidence vs leading context) | 1 | 1 | 0 | 1 | 1 |
| UNCERTAIN in the text | 8 | 8 | 8 | 8 | 0 |
| UNCERTAIN where needed (forecasts or signals disagree) | 8 of 8 | 8 of 8 | 8 of 8 | 8 of 8 | 0 of 8 |
| tokens per alert (median) | 3376 | 3346 | 2731 | 0 | 0 |
| tokens total | 35258 | 30435 | 24287 | 0 | 0 |
| v1 -> v2 time per alert, median s (max) | 2.7 (17.71) | 2.7 (3.37) | 1.86 (1.97) | 0.14 (0.32) | 0.12 (0.21) |
| checks failed on final texts | none | none | precision, uncertain, update | none | none |

Reviewer on seeded errors in the LLM-written multi-agent texts (deterministic checks, 9 texts as bases): caught **135 of 135** (100.0 %); base texts flagged although saved: 0.

| error kind | caught / seeded |
|---|---|
| wrong_number | 9 / 9 |
| wrong_probability | 9 / 9 |
| wrong_category | 9 / 9 |
| wrong_category_stage | 9 / 9 |
| invented_threshold | 9 / 9 |
| invented_threshold_known | 9 / 9 |
| invented_category | 9 / 9 |
| missing_simulation | 9 / 9 |
| cause_downstream | 9 / 9 |
| cause_claim | 9 / 9 |
| unknown_abbreviation | 9 / 9 |
| certainty | 9 / 9 |
| null_value | 9 / 9 |
| markdown | 9 / 9 |
| wrong_update | 9 / 9 |

Per alert (multi):

| alert | outcome | attempts | checks | faithful | judge | tokens | s |
|---|---|---|---|---|---|---|---|
| 08150000|minor|2024-07-23T18:00:00Z | saved | 1 | pass | 20/20 | PASS | 3215 | 2.7 |
| 08151500|minor|2023-10-26T07:00:00Z | saved | 1 | pass | 14/14 | PASS | 3246 | 2.13 |
| 08156800|minor|2023-10-26T12:00:00Z | saved | 1 | pass | 19/19 | PASS | 3582 | 2.9 |
| 08156800|minor|2024-05-02T00:00:00Z | saved | 1 | pass | 21/21 | PASS | 3480 | 2.77 |
| 08156800|minor|2025-05-28T23:00:00Z | saved | 1 | pass | 15/15 | PASS | 3434 | 2.52 |
| 08165500|minor|2025-07-04T07:00:00Z | saved | 1 | pass | 8/8 | PASS | 3273 | 2.16 |
| 08169500|minor|2021-10-14T05:00:00Z | saved | 1 | pass | 19/19 | PASS | 3357 | 2.77 |
| 08172400|minor|2024-01-22T21:00:00Z | saved | 3 | pass | 13/13 | no reply (RateLimitError: Error code: 429 - {'error': {'mess | 8295 | 17.71 |
| 08177700|minor|2025-06-12T07:00:00Z | saved | 1 | pass | 13/13 | PASS | 3376 | 2.28 |

Per alert (multi_ij, independent judge; judge per attempt):

| alert | outcome | attempts | checks | judge per attempt | tokens | s |
|---|---|---|---|---|---|---|
| 08150000|minor|2024-07-23T18:00:00Z | saved | 1 | pass | 1: pass | 3093 | 2.7 |
| 08151500|minor|2023-10-26T07:00:00Z | saved | 1 | pass | 1: pass | 3331 | 2.34 |
| 08156800|minor|2023-10-26T12:00:00Z | saved | 1 | pass | 1: pass | 3336 | 3.37 |
| 08156800|minor|2024-05-02T00:00:00Z | saved | 1 | pass | 1: pass | 3465 | 2.85 |
| 08156800|minor|2025-05-28T23:00:00Z | saved | 1 | pass | 1: pass | 3440 | 2.7 |
| 08165500|minor|2025-07-04T07:00:00Z | saved | 1 | pass | 1: pass | 3308 | 2.14 |
| 08169500|minor|2021-10-14T05:00:00Z | saved | 1 | pass | 1: pass | 3525 | 2.88 |
| 08172400|minor|2024-01-22T21:00:00Z | saved | 1 | pass | 1: pass | 3591 | 2.84 |
| 08177700|minor|2025-06-12T07:00:00Z | saved | 1 | pass | 1: pass | 3346 | 2.5 |
