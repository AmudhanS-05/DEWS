"""Run the advisory graph (A1.5).

  python -m agentic_ai.run --alert-id '08158700|minor|2019-05-04T03:00:00Z'   one alert from PostgreSQL, then exit
  python -m agentic_ai.run                                                   consumer group `agent` on alerts.flood

Consume-only: this process reads Kafka and writes PostgreSQL (advisories, agent_runs, agent_events). It never
produces to Kafka and never sends email. Add --force to re-run an alert that already has an agents advisory.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from agentic_ai import graph, llm, tracing
from agentic_ai.db import connect, ensure_schema
from agentic_ai.tools import Tools

SINK_WAIT_TRIES = 5     # the alert may reach PostgreSQL (sink) a moment after it reaches Kafka
SINK_WAIT_S = 2.0


def summarize(res: dict) -> str:
    s = f"{res['alert_id']}: {res['outcome']}"
    if res.get("advisory_id"):
        s += f" -> {res['advisory_id']} ({res.get('confidence')}, reviewer {res.get('verdict')}, {res.get('attempts')} attempt(s))"
    if res.get("drop_reason"):
        s += f" ({res['drop_reason']})"
    if res.get("error"):
        s += f" [error: {res['error'][:120]}]"
    return s + f"; {len(res['events'])} events, {res.get('tokens', 0)} tokens"


def process_record(tools, raw: bytes | str, force: bool = False, sleep=time.sleep) -> dict | None:
    """One `alerts.flood` record. Returns the run result, or None when the record is unusable or is a retraction."""
    try:
        rec = json.loads(raw)
        alert_id = rec["alert_id"]
    except (ValueError, KeyError, TypeError):
        print("skip: not an alert record", file=sys.stderr)
        return None
    if rec.get("kind") == "retract":
        print(f"skip: retraction {alert_id}")
        return None
    for _ in range(SINK_WAIT_TRIES):
        if tools.get_alert(alert_id) is not None:
            break
        sleep(SINK_WAIT_S)
    return graph.run_alert(tools, alert_id, force=force)


def consume(tools, force: bool = False) -> None:
    from confluent_kafka import Consumer   # imported here so --alert-id works without Kafka installed
    from common import config
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": config.GROUP_AGENT,
                  "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    c.subscribe([config.TOPIC_ALERTS])
    print(f"agent group '{config.GROUP_AGENT}' reading {config.TOPIC_ALERTS} (Ctrl-C to stop)")
    try:
        while True:
            msg = c.poll(1.0)
            if msg is None or msg.error():
                continue
            res = process_record(tools, msg.value(), force)
            if res:
                print(summarize(res))
            c.commit(message=msg, asynchronous=False)   # after PostgreSQL is written; a crash re-runs the alert (dedupe drops it)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--alert-id")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dsn")
    args = ap.parse_args(argv)
    t = tracing.configure()
    print(f"LLM_PROVIDER={llm.provider()}  LangSmith tracing: {t['reason']}")
    conn = connect(args.dsn)
    ensure_schema(conn)
    tools = Tools(conn)
    if args.alert_id:
        res = graph.run_alert(tools, args.alert_id, force=args.force)
        print(summarize(res))
        if res.get("draft"):
            print("\n" + res["draft"])
        return 0 if res["outcome"] in ("saved", "dropped") else 1
    consume(tools, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
