# 3. Results

All results are from **replayed** history (mode SIMULATION) unless marked LIVE. Each table names its source.

## 3.1 Pipeline

| Measure | Result | Source |
|---|---|---|
| Broker failure | one of 3 brokers stopped: topics stayed available (ISR 3 → 2 → 3) | verified 29 Oct 2026 |
| Windows | May 2019 DRWT2 replay: 119 hourly windows, none emitted twice (`tools.check_windows` PASS) | commit `c68a9e7` |
| Sink exactly-once effect | sink killed mid-replay and restarted: 4233/4233 readings, 0 gaps, 0 duplicates | commit `960c361` |
| Forecaster | 3903 windows → 3903 forecasts | commit `608c4e9` |
| Recovery after restart (one run each) | Faust worker 18.1 s, broker 21.1 s, sink 5.4 s, PostgreSQL 3.9 s, notifier 1.1 s, live producer 4.2 s; all loss/duplication checks PASS | commit `390f66d` |
| Stage latency (replay, 167 traces) | ingest median 0.066 s, forecast 0.506 s, one alert 1.07 s | commit `bde227c` |
| Notification | an approved replayed advisory was emailed and `delivered_at` recorded | commit `ecd842f` |

TODO (not measured yet):
- throughput: `--no-sleep` replay of 2017 to 2026 (`tools/throughput_run.py`);
- end-to-end latency including human approval and delivery on a real approval (`bde227c` covers these with a
  PostgreSQL test only);
- (since measured: the live feed-delay log, see [02-data §2.5](02-data.md#25-live-data).)

## 3.2 Forecaster

**Protocol** [`ml/MODEL_CARD.md` §1]: fit 2015-2020, validation 2021-2023, test 2024-01-01 to 2026-10-31 (63 minor
crossings, 23 storms). The threshold uses a false-alarm budget: the most validation detections without more validation
false alarms than the rise-rate rule. Event scoring is per gauge and pooled, with equal weight per gauge and per event.

**Table 3.** Test period, all test windows, POOLED [`ml/figures/s3_eval.csv`, run `m3.1-s3`; h ≠ 3 from
`ml/MODEL_CARD.md` §1]

| h | s3_v0 F1 | s3_v0 false alarms | rise-rate F1 | rise-rate false alarms |
|---|---|---|---|---|
| 1 | 0.29 | 44 | 0.30 | 72 |
| 2 | 0.24 | 109 | 0.26 | 145 |
| 3 | **0.24** (P 0.16, R 0.46, 29/63 detected) | **150** | **0.21** (P 0.13, R 0.52, 33/63) | **224** |
| 4 | 0.19 | 189 | 0.19 | 297 |
| 5 | no alert | 0 | 0.17 | 362 |
| 6 | 0.17 | 340 | 0.16 | 425 |

- **Lead time at 3 h:** s3_v0 median 0.42 h gross / **−0.58 h net** of the 1 h feed delay; rise-rate 0.50 / −0.50 h.
  There is no lead-time gain.
- **Slow floods only, 3 h:** F1 0.16 for both models.
- **Wording (team decision):** s3_v0 at 3 h gives "comparable detection with about a third fewer false alarms on the
  2024-26 test period". It is active in the stream at 3 h only [`ml/models/active.json`].
- **Rain (v1):** on the rain-era test rows (a case-control sample: event and dry hours only), v1 F1 at 3 h is 0.24 (48
  false alarms) against v0 0.31 (97) and rise-rate 0.26 (156) [`s3_eval.csv`, run `m3.1-s3-rainrows`]. v1 is never
  above rise-rate. **Adding radar rain did not help.**
- **Radius 5 / 10 / 25 km:** row-level average precision at 3 h of 0.065 / 0.063 / 0.058; no consistent effect
  [`ml/figures/s3_radius.csv`].
- **Precision-recall figure:** `ml/figures/s3_pr_3h.png`.
- **Disclosed protocol error:** a first run refitted v0 on 2015-2023 and kept the validation threshold; test false
  alarms at 3 h rose to 378, against a budget of 224. It is kept in `ml/figures/s3_eval_run1_refit*.csv` and replaced by
  the final protocol.

**The longer-period bar.** Rise-rate at 3 h on 2015 to 2025 (38 gauges, 310 crossings): pooled F1 0.28 (P 0.18,
R 0.61), slow 0.21, median warning 0.8 h gross / −0.2 h net [`ml/figures/baselines.csv`]. Earlier EDA figures (0.65 /
0.26 / 0.64 / 0.41 per gauge) contained a 1 h look-ahead and are withdrawn [`ml/REFERENCE_BASELINES.md`].

**Demo flood.** On the May 2019 DRWT2 replay, the rise-rate rule's first alert came in the window where the gauge was
already at 8.2 ft, above moderate (minor is 5.5 ft), so there was no lead time on the minor crossing
[`agentic_ai/fixtures/drwt2_2019-05_replay.json`]. The rain demo candidates of 26 Oct 2023 (AHOT2, LLAT2) lie in the
validation period, so their alert leads are **not** results [`ml/figures/demo_flood_candidates.csv`].

TODO: a sentence placing −0.5 h net lead time in context. Stage-only forecasting cannot beat the hourly feed delay on
these fast-rising gauges. State it as an observation from Table 3, not a general claim.

## 3.3 Sensor-fault rules

**Table 4.** Test 2024-26, faults injected into real series, 10 per type per gauge [`ml/figures/fault_rules_test.csv`;
settings from `ml/figures/fault_rules_settings.json`, chosen on 2021-23]

| Rule | Detected | Median time to flag | Flood windows falsely flagged | Shipped |
|---|---|---|---|---|
| no readings (6 h dropout) | 100 % | 2.9 h | 0.49 % | yes |
| step | 81.4 % | 0.33 h | 0 % | yes |
| stuck (24 h flat) | 29.5 % | 23.3 h | 0 % | yes |
| spike | 100 % | 0.08 h | 1.07 % | no (limit 1 %) |
| slow drift | 0 % | n/a | n/a | no rule |

**Negative result:** an Isolation Forest on window features flagged 11.2 % of real flood windows (30 % with the first
feature set) and its detection rates swung between validation and test [`ml/MODEL_CARD.md` §2,
`ml/figures/anomaly_eval.csv`]. It is not shipped.

## 3.4 What is not claimed

- No live detection result: no live flood occurred at the 38 gauges while the system ran. TODO: confirm at submission
  time.
- No comparison with NWS, NSSL FLASH or GloFAS.
- No per-gauge conclusions: the 63 test crossings spread over 26 gauges, 1 to 9 each [`ml/MODEL_CARD.md` §1].
