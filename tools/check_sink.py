"""Compare what is on the Kafka topics with what is in PostgreSQL (C1.3 test).

    python -m tools.check_sink                  # the sink's topics, all modes
    python -m tools.check_sink --mode SIMULATION

For every table the sink fills, reads the whole topic (own throwaway consumer, no offsets committed), builds the set of
natural keys, and compares it with the keys in PostgreSQL:
  gap        key on the topic but not in the table  (the sink lost or has not yet written it)
  duplicate  key present more than once in the table (the primary key / unique index should make this impossible)
  extra      key in the table but not on the topic (e.g. data from an earlier run after the topic was recreated)
Exit code 0 only when there are no gaps and no duplicates.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import psycopg2
from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition

from common import config
from common.progress import Task
from common.timeutil import to_epoch


def read_topic(topic: str, stall_s: float = 20.0) -> list[dict]:
    """Every record on the topic right now: assign all partitions from the start, read to each high watermark."""
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": "check-sink-" + uuid.uuid4().hex[:8],
                  "enable.auto.commit": False})
    out: list[dict] = []
    try:
        parts = c.list_topics(topic, timeout=10).topics[topic].partitions
        end = {}
        for p in parts:
            lo, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            if hi > lo:
                end[p] = hi
        c.assign([TopicPartition(topic, p, OFFSET_BEGINNING) for p in end])
        last = time.monotonic()
        while end:
            m = c.poll(1.0)
            if m is None:
                if time.monotonic() - last > stall_s:
                    raise RuntimeError(f"{topic}: no data for {stall_s}s with partitions {sorted(end)} unread")
                continue
            last = time.monotonic()
            if m.error():
                continue
            out.append(json.loads(m.value()))
            if m.offset() + 1 >= end.get(m.partition(), 0):
                end.pop(m.partition(), None)
    finally:
        c.close()
    return out


def epoch(ts) -> int:
    return int(float(ts))


# table -> (topics, record -> key or None, SQL returning the same key columns, index of the mode column)
CHECKS = {
    "gauge_readings": (
        [config.TOPIC_GAUGE, config.TOPIC_REPLAY_GAUGE],
        lambda m: (m["site_no"], m["parameter"], m["series"], epoch(m["reading_ts"]), m.get("mode", "LIVE"))
        if m.get("type", "reading") == "reading" else None,
        "SELECT site_no, parameter, series, EXTRACT(EPOCH FROM reading_time)::bigint, mode FROM gauge_readings", 4),
    "catchment_rain": (
        [config.TOPIC_RAIN, config.TOPIC_REPLAY_RAIN],
        lambda m: (m["site_no"], m["product"], epoch(m["hour_end_ts"]), int(m.get("accum_hours", 1)), m.get("mode", "LIVE"))
        if m.get("type") == "rain" else None,
        "SELECT site_no, product, EXTRACT(EPOCH FROM window_end)::bigint, accum_hours, mode FROM catchment_rain", 4),
    "windowed_aggregates": (
        [config.TOPIC_AGG],
        lambda m: (m["site_no"], epoch(m["window_start_ts"]), m.get("mode", "LIVE")),
        "SELECT site_no, EXTRACT(EPOCH FROM window_start)::bigint, mode FROM windowed_aggregates", 2),
    "risk_scores": (
        [config.TOPIC_FORECASTS],
        lambda m: (m["site_no"], m["model"], float(m["horizon_h"]), m.get("mode", "LIVE"), epoch(to_epoch(m["window_start"]))),
        "SELECT site_no, model, horizon_h, mode, EXTRACT(EPOCH FROM window_start)::bigint FROM risk_scores", 3),
    "alerts": (
        [config.TOPIC_ALERTS],
        lambda m: (m["alert_id"],),
        "SELECT alert_id FROM alerts", None),
}


def compare(topic_keys: set, table_keys: list) -> dict:
    seen, dupes = set(), set()
    for k in table_keys:
        (dupes if k in seen else seen).add(k)
    return {"topic": len(topic_keys), "table": len(seen), "gaps": len(topic_keys - seen),
            "duplicates": len(dupes), "extra": len(seen - topic_keys)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", help="only keys with this mode (LIVE | SIMULATION)")
    a = ap.parse_args()
    bad = 0
    with Task("check_sink: topics vs PostgreSQL", total=len(CHECKS), unit=" tables") as t:
        conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
        for i, (table, (topics, keyf, sql, mode_idx)) in enumerate(CHECKS.items(), 1):
            tk = set()
            for tp in topics:
                for m in read_topic(tp):
                    try:
                        k = keyf(m)
                    except (KeyError, ValueError, TypeError):
                        k = None
                    if k is not None and (not a.mode or m.get("mode", "LIVE") == a.mode):
                        tk.add(k)
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = [tuple(r) for r in cur.fetchall()]
            if a.mode and mode_idx is not None:
                rows = [r for r in rows if r[mode_idx] == a.mode]
            r = compare(tk, rows)
            ok = r["gaps"] == 0 and r["duplicates"] == 0
            bad += 0 if ok else 1
            t.update(i, f"{table}: topic {r['topic']}, table {r['table']}, gaps {r['gaps']}, "
                        f"duplicates {r['duplicates']}, extra {r['extra']} -> {'ok' if ok else 'FAIL'}")
        t.summary("PASS: no gaps, no duplicates" if not bad else f"FAIL: {bad} table(s) with gaps or duplicates")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
