"""Kafka -> PostgreSQL sink (C1.3).

    python -m storage.sink                     # run until Ctrl-C
    python -m storage.sink --group sink-rerun  # a new group name re-reads every topic from the start

Reads gauge.usgs + replay.gauge -> gauge_readings, aggregates.window -> windowed_aggregates,
forecasts.stage -> risk_scores (+ latency_trace), alerts.flood -> alerts.

Delivery: at least once with idempotent upserts. A batch is written and committed to PostgreSQL first and only
then are the Kafka offsets committed, so a crash replays at worst a few messages whose rows are overwritten in
place: no gaps, no duplicates (tools.check_sink verifies this). If PostgreSQL is down the sink keeps retrying and
commits nothing, so the messages wait on the broker.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict

import psycopg2
from confluent_kafka import Consumer
from psycopg2.extras import Json, execute_values

from alerting import template
from common import config

log = logging.getLogger("sink")

TOPICS = [config.TOPIC_GAUGE, config.TOPIC_REPLAY_GAUGE, config.TOPIC_AGG, config.TOPIC_FORECASTS, config.TOPIC_ALERTS]
GROUP = config.GROUP_SINK


# ---- pure mappers: one Kafka record -> one table row (or None to skip) -------------------------------------------

def gauge_row(m: dict):
    if m.get("type", "reading") != "reading":
        return None
    return (m["site_no"], m["parameter"], m["series"], m["reading_time"], m["value"], m.get("unit"),
            list(m.get("qualifiers") or []), m.get("mode", "LIVE"), m.get("fetched_at"))


def window_row(m: dict):
    return (m["site_no"], m["window_start"], m["window_end"], m.get("mode", "LIVE"), m["n_readings"],
            m.get("stage_last"), m.get("stage_max"), m.get("stage_min"), m.get("rise_rate_ft_per_h"),
            m.get("rain_1h"), m.get("rain_3h"), m.get("rain_6h"), m.get("provisional"))


def forecast_row(m: dict):
    return (m["site_no"], m["window_start"], m.get("mode", "LIVE"), m["model"], m["horizon_h"],
            m.get("forecast_stage"), m.get("p_action"), m.get("p_minor"), m.get("p_moderate"), m.get("p_major"))


def trace_row(m: dict):
    """latency_trace from a forecast record: reading -> fetched -> window closed -> forecast made."""
    t = m.get("trace") or {}
    if t.get("reading_ts") is None:
        return None
    from common.timeutil import epoch_to_iso
    iso = lambda k: epoch_to_iso(t[k]) if t.get(k) is not None else None  # noqa: E731
    mode = m.get("mode", "LIVE")
    return (f"{m['site_no']}|{mode}|{int(t['reading_ts'])}", m["site_no"], mode, iso("reading_ts"), iso("fetched_ts"),
            iso("window_close_time"), epoch_to_iso(m["made_at"]) if isinstance(m.get("made_at"), (int, float)) else m.get("made_at"))


def alert_row(m: dict):
    return (m["alert_id"], m["site_no"], m.get("mode", "LIVE"), m["category"], m.get("probability"), m.get("horizon_h"),
            m.get("provisional_data"), m["raised_at"], Json(m), m.get("kind", "new"))


# ---- SQL --------------------------------------------------------------------------------------------------------

SQL = {
    "gauge_readings": """INSERT INTO gauge_readings (site_no, parameter, series, reading_time, value, unit, qualifiers, mode, fetched_at)
        VALUES %s ON CONFLICT (site_no, parameter, series, reading_time, mode)
        DO UPDATE SET value = EXCLUDED.value, qualifiers = EXCLUDED.qualifiers""",
    "windowed_aggregates": """INSERT INTO windowed_aggregates (site_no, window_start, window_end, mode, n_readings, stage_last,
        stage_max, stage_min, rise_rate_ft_per_h, rain_1h, rain_3h, rain_6h, provisional) VALUES %s
        ON CONFLICT (site_no, window_start, mode) DO UPDATE SET n_readings = EXCLUDED.n_readings,
        stage_last = EXCLUDED.stage_last, stage_max = EXCLUDED.stage_max, stage_min = EXCLUDED.stage_min,
        rise_rate_ft_per_h = EXCLUDED.rise_rate_ft_per_h, rain_1h = EXCLUDED.rain_1h, rain_3h = EXCLUDED.rain_3h,
        rain_6h = EXCLUDED.rain_6h, provisional = EXCLUDED.provisional""",
    "risk_scores": """INSERT INTO risk_scores (site_no, window_start, mode, model, horizon_h, forecast_stage, p_action, p_minor,
        p_moderate, p_major) VALUES %s ON CONFLICT (site_no, window_start, mode, model, horizon_h) DO UPDATE SET
        forecast_stage = EXCLUDED.forecast_stage, p_action = EXCLUDED.p_action, p_minor = EXCLUDED.p_minor,
        p_moderate = EXCLUDED.p_moderate, p_major = EXCLUDED.p_major""",
    "latency_trace": """INSERT INTO latency_trace (trace_id, site_no, mode, reading_time, fetched_at, window_close_time,
        forecast_time) VALUES %s ON CONFLICT (trace_id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at,
        window_close_time = EXCLUDED.window_close_time, forecast_time = EXCLUDED.forecast_time""",
    # v1 is written once; later versions come from the agents, a person's edits never get overwritten
    "advisories": """INSERT INTO advisories (advisory_id, alert_id, version, source, text) VALUES %s
        ON CONFLICT (advisory_id) DO NOTHING""",
    # a withdrawn alert makes its unapproved drafts moot (approved ones need a human-written retraction)
    "advisory_retract": """UPDATE advisories a SET status = 'superseded' FROM (VALUES %s) AS v(alert_id)
        WHERE a.alert_id = v.alert_id AND a.status = 'draft'""",
    # an existing alert keeps its status (a person may already have approved it); only a retract changes it
    "alerts": """INSERT INTO alerts (alert_id, site_no, mode, category, probability, horizon_h, provisional_data, raised_at, details, status)
        VALUES %s ON CONFLICT (alert_id) DO UPDATE SET details = COALESCE(alerts.details, EXCLUDED.details),
        status = CASE WHEN EXCLUDED.status = 'retracted' THEN 'retracted' ELSE alerts.status END""",
}


def route(topic: str, m: dict) -> list[tuple[str, tuple]]:
    """(table, row) pairs for one record."""
    out: list[tuple[str, tuple]] = []
    if topic in (config.TOPIC_GAUGE, config.TOPIC_REPLAY_GAUGE):
        r = gauge_row(m)
        if r:
            out.append(("gauge_readings", r))
    elif topic == config.TOPIC_AGG:
        out.append(("windowed_aggregates", window_row(m)))
    elif topic == config.TOPIC_FORECASTS:
        out.append(("risk_scores", forecast_row(m)))
        t = trace_row(m)
        if t:
            out.append(("latency_trace", t))
    elif topic == config.TOPIC_ALERTS:
        a = alert_row(m)
        status = "retracted" if a[-1] == "retract" else "draft"
        out.append(("alerts", a[:-1] + (status,)))        # (..., details, status)
        if status == "retracted":
            out.append(("advisory_retract", (m["alert_id"],)))
        else:                                              # advisory v1 within seconds of the alert
            out.append(("advisories", (m["alert_id"] + "|1", m["alert_id"], 1, "template", template.render_v1(m))))
    return out


def write_batch(conn, rows: dict[str, list[tuple]]) -> int:
    """Upsert all rows in one transaction. Rows within one statement are de-duplicated by the caller."""
    n = 0
    with conn.cursor() as cur:
        for table in ("gauge_readings", "windowed_aggregates", "risk_scores", "latency_trace", "alerts", "advisories",
                      "advisory_retract"):
            batch = rows.get(table)
            if batch:
                execute_values(cur, SQL[table], batch, page_size=500)
                n += len(batch)
    conn.commit()
    return n


def last_wins(table: str, batch: list[tuple]) -> list[tuple]:
    """ON CONFLICT cannot touch the same key twice in one statement: keep the newest row per key."""
    keys = {"gauge_readings": (0, 1, 2, 3, 7), "windowed_aggregates": (0, 1, 3), "risk_scores": (0, 1, 2, 3, 4),
            "latency_trace": (0,), "alerts": (0,), "advisories": (0,), "advisory_retract": (0,)}[table]
    d: dict = {}
    for r in batch:
        d[tuple(r[i] for i in keys)] = r
    return list(d.values())


def connect():
    return psycopg2.connect(config.PG_DSN, connect_timeout=5)


def run(group: str, max_batch: int, stop_after_idle_s: float | None):
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": group, "enable.auto.commit": False,
                  "auto.offset.reset": "earliest", "isolation.level": "read_committed"})
    c.subscribe(TOPICS)
    conn = None
    total, last_log, idle_since = 0, time.monotonic(), time.monotonic()
    try:
        while True:
            msgs = c.consume(num_messages=max_batch, timeout=1.0)
            rows: dict[str, list[tuple]] = defaultdict(list)
            got = 0
            for msg in msgs:
                if msg.error():
                    log.warning("kafka: %s", msg.error())
                    continue
                got += 1
                try:
                    for table, row in route(msg.topic(), json.loads(msg.value())):
                        rows[table].append(row)
                except (KeyError, ValueError, TypeError) as e:
                    log.error("skipping bad record %s[%s]@%s: %r", msg.topic(), msg.partition(), msg.offset(), e)
            if not got:
                if stop_after_idle_s is not None and time.monotonic() - idle_since > stop_after_idle_s:
                    break
                continue
            idle_since = time.monotonic()
            rows = {t: last_wins(t, b) for t, b in rows.items()}
            while True:                                   # PostgreSQL down: wait, never commit the offsets
                try:
                    if conn is None or conn.closed:
                        conn = connect()
                    total += write_batch(conn, rows)
                    break
                except psycopg2.Error as e:
                    log.warning("postgres unavailable (%s); retrying in 3 s", str(e).strip().splitlines()[0])
                    try:
                        conn and conn.rollback()
                    except psycopg2.Error:
                        conn = None
                    time.sleep(3)
            c.commit(asynchronous=False)
            if time.monotonic() - last_log > 10:
                log.info("rows written so far: %d", total)
                last_log = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
        log.info("sink stopped; rows written: %d", total)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", default=GROUP, help="consumer group (a new name re-reads every topic from the start)")
    ap.add_argument("--max-batch", type=int, default=500)
    ap.add_argument("--exit-when-idle", type=float, help="stop after this many idle seconds (for scripts and tests)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("sink starting: group %s, topics %s", a.group, ", ".join(TOPICS))
    run(a.group, a.max_batch, a.exit_when_idle)


if __name__ == "__main__":
    main()
