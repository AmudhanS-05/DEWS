# Paper draft (markdown)

Working draft of the technical paper's main sections. It is **not** the submitted paper: wording, citations and the
title page are finished outside the repository.

| File | Section |
|---|---|
| [01-method.md](01-method.md) | system architecture and method (with the architecture figure) |
| [02-data.md](02-data.md) | data sources, gauges, history, labels |
| [03-results.md](03-results.md) | pipeline, forecaster and sensor-fault results |
| [04-india.md](04-india.md) | why the live system uses US data, and what India would need |
| [05-agents.md](05-agents.md) | the multi-agent advisory writer and its evaluation |

## Rules for this draft

- **Every number has a source** in brackets: a committed file (`ml/figures/s3_eval.csv`) or a commit hash
  (`390f66d`). A number without a source does not go in.
- **Gaps are marked `TODO`**, never filled with an estimate.
- **Replay vs live:** every result here is from replayed history (mode SIMULATION) unless it says LIVE. No live
  detection result exists yet.
- **Wording:** "forecast of crossing an official NWS flood category", never "predicts a flood". Lead time is given
  gross and net of the 1 h feed delay. No comparison with or claim against NWS, NSSL FLASH or GloFAS.
- The model wording agreed by the team: s3_v0 shows "comparable detection with about a third fewer false alarms on
  the 2024-26 test period", not "better forecasts".
