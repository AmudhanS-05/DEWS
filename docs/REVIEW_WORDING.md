# Review wording (source of truth for review material)

Taken from the team's edited Phase I deck `presentations/SPA_Phase1.pptx` (2 Oct 2026).
Any slide, report or document generated for a review uses these phrases. Where the
design docs say something different, this file wins for wording; differences in
substance are listed at the bottom so they get decided, not silently merged.

## Title and course
- **Title:** Real-Time Flood & Flash-Flood Forecasting and Alerting using Apache Kafka
- **Cover:** VII Semester · Stream Processing and Analytics (AI372IA) · Experiential Learning – Phase I Review
- **Mentors:** Dr. Vijayalakshmi M. N., Dr. Anupama Kumar
- **Team:** Aaditey Chalva (1RV23AI001), Amudhan S (1RV23AI014), Dhaksha Muthukumaran (1RV23AI029)

## Objectives (verbatim)
1. Design and implement an **Apache Kafka pipeline** that ingests live rainfall and river-gauge readings continuously, with no manual intervention required.
2. **Forecast river stage** several hours ahead of real time, and raise an alert when the forecast crosses an officially defined flood category rather than an invented threshold.
3. Measure and report true **system performance end-to-end**, including detection latency, alert delivery time, throughput under load and recovery time after a deliberately induced failure.
4. **Deliver forecasts and alerts** through a live, auto-refreshing dashboard and a notification channel, demonstrating a complete early-warning workflow.

## Key phrases
- Introduction: "A warning is only as good as its timeliness: river gauges record every 15 min, but the feed transmits roughly hourly." / "This project builds a live Apache Kafka pipeline that ingests rainfall and river-gauge data the moment it is published, and provides flood alerts in advance."
- Datasets (4): USGS Instantaneous Values (core signal the model predicts and alerts against); NOAA MRMS radar rainfall (leading early-warning rainfall input); NOAA NWPS gauge metadata (ground-truth flood thresholds for evaluation and alerts); historical USGS + MRMS archives (live demos and offline evaluation metrics). CAMELS-IND and GloFAS are no longer named in review material.
- Methodology headings: Live Data Ingestion · Stream Processing · Forecasting Model · Storage and dashboard · Fault Tolerance.
- Notification: "Email notification, delivery time logged"; "nothing is sent unapproved".
- Progress is reported as **Work Status** (~40 % complete at Phase I), not a percentage chart.

## Literature gaps (deck v3, 2 Oct)
- G1 · Live performance is not measured → Objective 3
- G2 · Alerts are not tied to official flood categories → Objective 2
- G3 · No fault-tolerant streaming pipeline for river-stage forecasting → Objective 1
- G4 · Forecasts do not reach people as a live workflow → Objective 4

## Substance to decide (raised 2 Oct, not changed in the deck)
- **Methodology slide says "A supervised model (Isolation forest) predicts river stage".** Isolation Forest is an unsupervised anomaly detector and cannot predict a stage value. The repo's design is a supervised forecaster (choice made after the persistence / rise-rate baselines), with Isolation Forest at most a sensor-fault check. The wording should change before a reviewer asks.
- The objectives no longer mention the agentic AI layer or the India retrospective test; the cover names only AI372IA. Decide whether the agent layer (AI373TA) appears in SPA reviews at all.
- Contents slide still lists "Summary of Literature"; that section is now the single "Literature – Gaps Identified" slide.
