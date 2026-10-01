"""Faust stream app: gauge.usgs -> event-time windows -> aggregates.window.

- Consumes gauge.usgs as consumer group "flood-windowing" (Faust's app id).
- Window state lives in a Faust Table keyed by site_no (same key as the topic, so
  it is co-partitioned). Faust mirrors the table to a changelog topic, so a killed
  worker resumes with its window state intact (the fault-tolerance claim).
- Readings update windows (streams/windowing.py); heartbeats from the producer
  close windows by wall clock and zero-fill silent gauges (LIVE only).

Run (from the repo root, stack up):
    faust -A streams.faust_app worker -l info
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

gauge_topic = app.topic(config.TOPIC_GAUGE, key_type=str, value_serializer="json")
agg_topic = app.topic(config.TOPIC_AGG, key_type=str, value_serializer="json")
dlq_topic = app.topic(config.TOPIC_DLQ, key_type=str, value_serializer="json")

window_state = app.Table("window_state", default=dict, partitions=config.TOPIC_PARTITIONS)


@app.agent(gauge_topic)
async def window_readings(stream):
    async for key, msg in stream.items():
        site = msg.get("site_no") or key
        state = dict(window_state[site])
        try:
            if msg.get("type") == "heartbeat":
                state, emitted = windowing.on_tick(site, state, float(msg["now_ts"]),
                                                   config.WINDOW_SECONDS, GRACE_S, MAX_LATE_WINDOWS)
            else:
                state, emitted, too_late = windowing.on_reading(state, msg, config.WINDOW_SECONDS,
                                                                msg.get("mode", "LIVE"), MAX_LATE_WINDOWS)
                if too_late:
                    await dlq_topic.send(key=site, value={"reason": "too_late", "record": msg})
        except Exception as e:  # never let one bad record stop the stream
            await dlq_topic.send(key=site, value={"reason": f"error: {e}", "record": msg})
            continue
        window_state[site] = state
        for agg in emitted:
            agg["window_close_time"] = epoch_to_iso(now_epoch())
            await agg_topic.send(key=site, value=agg)
