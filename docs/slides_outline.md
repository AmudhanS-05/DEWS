# Slide outlines and demo script

Two decks and one demo. Each slide has a title, the **one** figure or number it carries, and the committed file
behind it. Numbers come only from those files (see `docs/paper/README.md` for the rules). Every result is replay
(SIMULATION) unless marked LIVE.

## Deck 1: stream processing (pipeline)

| # | Title | The one figure or number | Source |
|---|---|---|---|
| 1 | Real-time flood forecasting and alerting on Apache Kafka | title, team | `README.md` |
| 2 | A warning is only as good as its timeliness | live feed delay 24 to 69 min (one sample) | `FAQ.md` (EDA, 3 Oct 2026) |
| 3 | Objectives | the four objectives, verbatim | `docs/REVIEW_WORDING.md` |
| 4 | Data | 38 gauges, 30.8 M stage readings 2015-2026, 331 minor crossings; official NWPS stages as labels | `ml/figures/usgs_fetch_summary.csv`, `ml/MODEL_CARD.md` |
| 5 | Architecture | Figure 1 (Mermaid) | `docs/paper/01-method.md` |
| 6 | Topics, partitions, consumer groups | 9 topics × 3 partitions, RF 3, min ISR 2; 5 groups | `common/config.py`, `docker/docker-compose.yml` |
| 7 | Event-time windows in Faust | one diagram: late batch, zero-fill, revision; 119 windows, none twice | `streams/windowing.py`, commit `c68a9e7` |
| 8 | Effectively once into PostgreSQL | 4233/4233 readings, 0 gaps, 0 duplicates after a sink kill | commit `960c361` |
| 9 | Fault tolerance | recovery 18.1 / 21.1 / 5.4 / 3.9 / 1.1 / 4.2 s (Faust, broker, sink, PostgreSQL, notifier, producer) | commit `390f66d` |
| 10 | Latency waterfall | replay: ingest 0.066 s, forecast 0.506 s, alert 1.07 s (median); approval and delivery: TODO on a real approval | commit `bde227c`, console Metrics tab |
| 11 | Forecast of crossing an official category | Table 3 at 3 h: s3_v0 29/63 detected, 150 false alarms vs rise-rate 33/63, 224; "comparable detection with about a third fewer false alarms", no lead-time gain (−0.58 h net) | `ml/figures/s3_eval.csv` |
| 12 | The bar was corrected | 0.65 → 0.25 at DRWT2: the 1 h look-ahead found and removed | `ml/REFERENCE_BASELINES.md` |
| 13 | Rain did not help (yet) | v1 3 h F1 0.24 vs v0 0.31 on rain rows; median 100.9 mm before slow floods vs 0.07 mm dry | `ml/figures/s3_eval.csv`, `ml/figures/mrms_event_vs_dry.csv` |
| 14 | Sensor faults | 3 rules shipped (dropout 100 %, step 81.4 %, stuck 29.5 %); spike held back at 1.07 % flood false flags; Isolation Forest flags 11.2 % of floods | `ml/figures/fault_rules_test.csv`, `ml/MODEL_CARD.md` |
| 15 | Human approval and delivery | screenshot: approval panel; "nothing is sent unapproved" | console Readings & alerts |
| 16 | Dashboards | console + Power BI on the same tables (DirectQuery) | `dashboard/powerbi/README.md`; TODO: Power BI screenshot once page 1 exists |
| 17 | Why US data: India's pain points | CWC river stations about hourly (July 2022, 83 stations); on real US data hourly readings cut detection 0.52 → 0.17, a 1 h feed delay to 0.12; by decision a pain-point analysis, no Indian data run | `india/PAIN_POINTS.md`, `india/figures/degradation.csv` |
| 18 | Limits and next steps | no live detection result; no claim against NWS / FLASH / GloFAS; throughput TODO | `docs/paper/03-results.md` §3.4 |

## Deck 2: agentic AI (advisory writer)

| # | Title | The one figure or number | Source |
|---|---|---|---|
| 1 | From an alert to an advisory a person can approve | title | |
| 2 | Two layers: reactive and deliberative | v1 template in seconds, v2 from agents; v1 stays on any failure | `docs/paper/05-agents.md` §5.1 |
| 3 | Coordinator–Worker–Delegator in LangGraph | graph figure | `docs/guide/07-agents.md` (Mermaid), console Agents tab |
| 4 | Evidence vs context | leading (rain, upstream) vs supporting (downstream, nearby) signals | `agentic_ai/rules.py` |
| 5 | Conflict resolution | disagreement → UNCERTAIN with both signals | `agentic_ai/rules.py`, tests in `tests/agents/` |
| 6 | Reflection: the reviewer | deterministic checks first, model judgement second; at most 2 revisions | `agentic_ai/checks.py` |
| 7 | Tools and memory | read-only PostgreSQL tools; "Update n" in an episode; MCP server TODO (S4) | `agentic_ai/tools.py` |
| 8 | Safety boundary | agents never publish or send; only a person's approval reaches `alerts.approved` | `alerting/approval.py` |
| 9 | Live runs | Table 5: 5/5 first-attempt pass, 2.6 to 3.9 s, 3.1k to 3.5k tokens at reasoning effort low | `docs/breadcrumbs/agents.md` |
| 10 | A slip and its fix | "NGS" for NWS in 2 of 5 runs → abbreviation check → 0 in 5 | `docs/breadcrumbs/agents.md` |
| 11 | Observability | LangSmith trace per advisory (5/5 found) | `docs/breadcrumbs/agents.md` |
| 12 | Evaluation still to do | TODO: reviewer catch rate, faithfulness, single- vs multi-agent ablation | `docs/paper/05-agents.md` §5.3 |

## Demo script (consistent with `docs/DEMO.md`)

Roles: one narrates, one drives the console and terminals, one handles questions and Power BI. Start everything with
`scripts\start_demo.ps1` (stop with `scripts\stop_demo.ps1`) or by hand ([guide 03](guide/03-run-a-demo.md)).

| Scene | Show | Say | Fallback |
|---|---|---|---|
| 0 Live (DEMO.md scene 0) | landing page → globe → 38 gauges, all below action | "Every pillar is a real gauge, coloured by its official NWS level; the feed arrives about hourly." | no internet: the bundled world image; gauges still show |
| 1 Replay a flood | `replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 3600`; Pipeline flow lights up; DRWT2 pillar climbs to major | "This is May 2019, replayed and marked SIMULATION." | `--speed 36000` if short of time |
| 2 Alert and advisory | Readings & alerts: v1 template in seconds, then v2 from the agents with reviewer PASS | "The model drafts; a person decides." State honestly: the baseline alerted after the minor crossing on this flood | `LLM_PROVIDER=none` if Groq is rate-limited (v1 or deterministic v2) |
| 3 Approve and deliver | type the approver, Approve, confirm; the notifier emails the bulletin; Delivered fills in | "Nothing is sent without this click." | `alerting.notifier --dry-run` if SMTP is blocked |
| 4 Break it | `tools.fault_drill --scenario faust` (or `broker`); Status changes log | "Recovery is measured, not assumed." | quote the committed recovery times |
| 5 Numbers | Metrics tab (throughput, lag, latency waterfall); Power BI page 1 | live and replay apart, gross and net | console only if Power BI is unavailable (say so) |

TODO: a rain scene once `claude/rain` is merged. The 26 Oct 2023 AHOT2/LLAT2 flood is visual only, because it lies in
the validation period.

Questions to prepare for: `docs/DEMO.md`, "Questions to prepare for".
