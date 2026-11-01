# Agent evaluation on replayed alerts (A4.2)

Data: 9 floods from the measured USGS record (AHOT2 2023-10-26, AHOT2 2024-05-02, AHOT2 2025-05-29, GBCT2 2021-10-14, HNTT2 2025-07-04, JNCT2 2024-07-23, LCPT2 2024-01-23, LLAT2 2023-10-26, SIOT2 2025-06-12), replayed through core's windowing and forecaster (s3_v0 at 3 h, MRMS rain from ml's cache; since core 6b105c1 an alert is raised by the model's probability, alert_by_model: p_minor >= tau), mode SIMULATION; every alert raised. Earlier tables (stage-forecast rule): before_alert_by_model/agent_eval.md. All numbers below are measured in this run (agentic_ai/eval/runs.jsonl); replay only, no live alerts.

| metric | template | llm_down |
|---|---|---|
| alerts | 9 | 9 |
| draft saved (v2) / written | 9/9 (100 %) | 0/9 (0 %) |
| template v1 kept | 0 | 9 |
| text written by the LLM | 0 | 0 |
| final text passes all deterministic checks | 9/9 (100 %) | - |
| saved on attempt 1 | 9/9 (100 %) | 0/9 (0 %) |
| mean attempts | 1 | 1 |
| faithfulness: texts with every number in the evidence | 9/9 (100 %) | - |
| faithfulness: numbers found in the evidence | 193/193 (100 %) | - |
| LLM judge PASS / FAIL (final verdicts) | 0 / 0 | 0 / 0 |
| alerts raised by the model's probability (alert_by_model) | 9 | 9 |
| of these, stage forecast below the category (forecasts disagree) | 8 | 8 |
| disagreements (evidence vs leading context) | 1 | 1 |
| UNCERTAIN in the text | 8 | 0 |
| tokens per alert (median) | 0 | 0 |
| tokens total | 0 | 0 |
| v1 -> v2 time per alert, median s (max) | 0.14 (0.32) | 0.12 (0.21) |
| checks failed on final texts | none | none |

Per alert (multi):

| alert | outcome | attempts | checks | faithful | judge | tokens | s |
|---|---|---|---|---|---|---|---|
