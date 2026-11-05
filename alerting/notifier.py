"""Notifier (C1.5): alerts.approved -> email. The only component that sends anything, and it reads only that topic.

    python -m alerting.notifier                  # run until Ctrl-C
    python -m alerting.notifier --dry-run        # log what would be sent; sends nothing, marks nothing, commits no offsets

Settings come from .env (never printed): NOTIFY_TO (comma separated), SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD.
Port 465 uses SSL, anything else STARTTLS. A send is tried SEND_ATTEMPTS times with growing waits; after the last
failure the record goes to deadletter.flood and the advisory stays 'approved' with delivered_at empty (visible in
the console). On success advisories/alerts get delivered_at, status 'sent' and channel 'email'. The Kafka offset is
committed only after that, so a crash between send and commit re-sends at most one email (at-least-once).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import psycopg2
from confluent_kafka import Consumer, TopicPartition

from alerting import bulletin
from alerting.approval import ALERT_TRACE_ID
from common import config
from common.envfile import load_env
from common.kafka_io import JsonProducer
from common.kafka_commit import commit_sync

log = logging.getLogger("notifier")

SEND_ATTEMPTS = int(os.getenv("NOTIFY_ATTEMPTS", "3"))
BACKOFF_S = float(os.getenv("NOTIFY_BACKOFF_S", "5"))


def smtp_settings(env=os.environ) -> dict:
    missing = [k for k in ("NOTIFY_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not env.get(k)]
    if missing:
        raise RuntimeError("missing in .env: " + ", ".join(missing))
    return {"to": [a.strip() for a in env["NOTIFY_TO"].split(",") if a.strip()], "host": env["SMTP_HOST"],
            "port": int(env.get("SMTP_PORT") or 587), "user": env["SMTP_USER"], "password": env["SMTP_PASSWORD"]}


def build_message(rec: dict, alert: dict | None, site_name: str | None, sender: str, to: list[str]) -> EmailMessage:
    """Multipart email (plain text + HTML) laid out as a flood forecast bulletin (alerting/bulletin.py)."""
    f = bulletin.facts(rec, alert, site_name)
    msg = EmailMessage()
    msg["Subject"] = bulletin.subject(f)
    msg["From"], msg["To"] = sender, ", ".join(to)
    msg.set_content(bulletin.render_text(f), charset="utf-8")
    msg.add_alternative(bulletin.render_html(f), subtype="html", charset="utf-8")
    return msg


def smtp_send(msg: EmailMessage, cfg: dict) -> None:
    cls = smtplib.SMTP_SSL if cfg["port"] == 465 else smtplib.SMTP
    with cls(cfg["host"], cfg["port"], timeout=20) as s:
        if cls is smtplib.SMTP:
            s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)


def send_with_retry(send, msg, cfg, attempts: int = SEND_ATTEMPTS, backoff_s: float = BACKOFF_S, sleep=time.sleep):
    """Try `send` up to `attempts` times; returns (ok, last_error)."""
    err = None
    for i in range(1, attempts + 1):
        try:
            send(msg, cfg)
            return True, None
        except Exception as e:  # noqa: BLE001 - any SMTP/network failure is retried
            err = f"{type(e).__name__}: {e}"
            log.warning("send attempt %d/%d failed: %s", i, attempts, err)
            if i < attempts:
                sleep(backoff_s * i)
    return False, err


def mark_delivered(conn, rec: dict, when: datetime) -> None:
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE advisories SET status = 'sent', delivered_at = %s WHERE advisory_id = %s",
                    (when, rec["advisory_id"]))
        cur.execute("UPDATE alerts SET status = 'sent', delivered_at = %s, channel = 'email' WHERE alert_id = %s",
                    (when, rec["alert_id"]))
        cur.execute(f"UPDATE latency_trace SET delivered_time = %s WHERE trace_id = {ALERT_TRACE_ID}", (when, rec["alert_id"]))


def alert_info(conn, alert_id: str):
    """(gauge name, alert row as dict) for the bulletin; (None, None) if the alert is unknown."""
    with conn.cursor() as cur:
        cur.execute("SELECT g.name, a.site_no, a.mode, a.category, a.probability, a.horizon_h, a.provisional_data, a.details "
                    "FROM alerts a LEFT JOIN gauges g USING (site_no) WHERE a.alert_id = %s", (alert_id,))
        r = cur.fetchone()
    if not r:
        return None, None
    return r[0], dict(zip(("site_no", "mode", "category", "probability", "horizon_h", "provisional_data", "details"), r[1:]))


def handle(rec: dict, conn, cfg: dict, dry_run: bool, send=smtp_send, sleep=time.sleep, dlq=None) -> str:
    """Send one approved record. Returns 'sent' | 'dry-run' | 'failed'."""
    name, alert = alert_info(conn, rec["alert_id"])
    msg = build_message(rec, alert, name, cfg["user"], cfg["to"])
    if dry_run:
        log.info("DRY RUN: would send to %s: %s", ", ".join(cfg["to"]), msg["Subject"])
        return "dry-run"
    ok, err = send_with_retry(send, msg, cfg, sleep=sleep)
    if ok:
        mark_delivered(conn, rec, datetime.now(timezone.utc))
        log.info("sent %s to %d recipient(s)", rec["alert_id"], len(cfg["to"]))
        return "sent"
    log.error("giving up on %s: %s", rec["alert_id"], err)
    if dlq:
        dlq({"reason": f"notifier: {err}", "record": rec})
    return "failed"


def run(dry_run: bool, group: str, exit_when_idle: float | None):
    load_env()
    cfg = smtp_settings()
    conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
    prod = JsonProducer("notifier")
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": group, "enable.auto.commit": False,
                  "auto.offset.reset": "earliest", "isolation.level": "read_committed"})
    c.subscribe([config.TOPIC_APPROVED])
    idle_since = time.monotonic()
    log.info("notifier ready (%s): to %d recipient(s) via %s:%d", "DRY RUN" if dry_run else "live", len(cfg["to"]), cfg["host"], cfg["port"])
    try:
        while True:
            m = c.poll(1.0)
            if m is None or m.error():
                if exit_when_idle is not None and time.monotonic() - idle_since > exit_when_idle:
                    break
                continue
            idle_since = time.monotonic()
            try:
                rec = json.loads(m.value())
                if conn.closed:
                    conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
                handle(rec, conn, cfg, dry_run, dlq=lambda v: (prod.send(config.TOPIC_DLQ, "notifier", v), prod.flush()))
            except psycopg2.Error as e:
                log.warning("postgres unavailable (%s); will retry this message", str(e).strip().splitlines()[0])
                conn = psycopg2.connect(config.PG_DSN, connect_timeout=5) if conn.closed else conn
                c.seek(TopicPartition(m.topic(), m.partition(), m.offset()))
                time.sleep(3)
                continue
            except (KeyError, ValueError) as e:
                log.error("bad approved record at offset %s: %r", m.offset(), e)
            if not dry_run:                       # a dry run must never consume a message the live notifier should send
                commit_sync(c, message=m)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
        prod.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--group", default=config.GROUP_NOTIFIER)
    ap.add_argument("--exit-when-idle", type=float)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(a.dry_run, a.group, a.exit_when_idle)


if __name__ == "__main__":
    main()
