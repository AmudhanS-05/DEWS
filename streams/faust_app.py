"""Faust stream app: gauge.usgs + rain.mrms and replay.gauge + replay.rain -> event-time windows -> aggregates.window.

- One consumer group, "flood-windowing" (Faust's app id). Run 3 workers of it, one per
  partition (see docs/BUILD_PHASES.md, Phase 3).
- Live (gauge.usgs + rain.mrms) and replay (replay.gauge + replay.rain) use separate agents and separate state
  tables, so replayed history can never touch live window state. Each agent reads its gauge AND rain topic (all
  keyed by site_no, 3 partitions, so co-partitioned): a gauge's rain lands in the same per-site state as its
  readings, and windows carry rain_1h/3h/6h/24h when they are emitted (rule: streams/windowing.py, "Rain").
  Rain whose mode does not match the agent (LIVE vs SIMULATION) goes to deadletter.flood. Rain heartbeats are
  ignored: only the gauge producer's heartbeats close (zero-fill) windows.
- Window state lives in Faust Tables keyed by site_no (same key as the topics, so
  they are co-partitioned). Faust mirrors each table to a changelog topic, so a killed
  worker resumes with its window state intact (the fault-tolerance claim).
- Readings update windows (streams/windowing.py); heartbeats from the producer
  close windows by wall clock and zero-fill silent gauges (LIVE only).

Run (from the repo root, stack up), once per instance, each with its own web port:
    faust -A streams.faust_app worker -l info --web-port 6066
    faust -A streams.faust_app worker -l info --web-port 6067
    faust -A streams.faust_app worker -l info --web-port 6068
Single-broker stack: set TOPIC_REPLICATION_FACTOR=1 and KAFKA_BOOTSTRAP=127.0.0.1:9092 first.
"""
from __future__ import annotations

import os

import faust

from common import config
from common.timeutil import epoch_to_iso, now_epoch
from streams import windowing

GRACE_S = float(os.getenv("WINDOW_GRACE_S", "5400"))   # 90 min: USGS batches arrive ~hourly
MAX_LATE_WINDOWS = int(os.getenv("MAX_LATE_WINDOWS", "3"))

app = faust.App(
    "flood-windowing",
    broker=";".join(f"kafka://{h}" for h in config.KAFKA_BOOTSTRAP.split(",")),
    value_serializer="json",
    topic_replication_factor=config.TOPIC_REPLICATION_FACTOR,
    topic_partitions=config.TOPIC_PARTITIONS,
    producer_acks=-1,
)

live_topics = app.topic(config.TOPIC_GAUGE, config.TOPIC_RAIN, key_type=str, value_serializer="json")
replay_topics = app.topic(config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN, key_type=str, value_serializer="json")
agg_topic = app.topic(config.TOPIC_AGG, key_type=str, value_serializer="json")
dlq_topic = app.topic(config.TOPIC_DLQ, key_type=str, value_serializer="json")

live_state = app.Table("window_state", default=dict, partitions=config.TOPIC_PARTITIONS)
replay_state = app.Table("window_state_replay", default=dict, partitions=config.TOPIC_PARTITIONS)


async def _process(stream, table, expected_mode: str):
    async for key, msg in stream.items():
        site = msg.get("site_no") or key
        state = dict(table[site])
        try:
            state, emitted, dlq = windowing.on_record(state, {**msg, "site_no": site}, expected_mode, now_epoch(),
                                                      config.WINDOW_SECONDS, GRACE_S, MAX_LATE_WINDOWS)
        except Exception as e:  # never let one bad record stop the stream
            await dlq_topic.send(key=site, value={"reason": f"error: {e}", "record": msg})
            continue
        table[site] = state
        for d in dlq:
            await dlq_topic.send(key=site, value=d)
        for agg in emitted:
            agg["window_close_time"] = epoch_to_iso(now_epoch())
            await agg_topic.send(key=site, value=agg)


@app.agent(live_topics)
async def window_live(stream):
    await _process(stream, live_state, "LIVE")


@app.agent(replay_topics)
async def window_replay(stream):
    await _process(stream, replay_state, "SIMULATION")
