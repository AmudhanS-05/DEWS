# Reviewer catch rate on seeded errors (A3.1)

Deterministic reviewer (`agentic_ai/checks.py`), 5 correct base drafts (template), one seeded error per corrupted draft. Base drafts flagged although correct (false alarms): 0.

**Caught 74 of 74 seeded errors (100.0 %).**

| error kind | caught / seeded | in SLICES A3.1 list |
|---|---|---|
| wrong_number | 5 / 5 | yes |
| wrong_category | 5 / 5 | yes |
| wrong_category_stage | 5 / 5 |  |
| invented_threshold | 5 / 5 | yes |
| invented_threshold_known | 5 / 5 |  |
| invented_category | 5 / 5 |  |
| missing_simulation | 4 / 4 | yes |
| cause_downstream | 5 / 5 |  |
| cause_claim | 5 / 5 | yes |
| unknown_abbreviation | 5 / 5 | yes |
| certainty | 5 / 5 |  |
| null_value | 1 / 1 |  |
| markdown | 5 / 5 |  |
| wrong_update | 5 / 5 |  |
| wrong_probability | 4 / 4 |  |
| missing_provisional | 4 / 4 |  |
| simulation_on_live | 1 / 1 |  |
