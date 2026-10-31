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


def utf8_stdio() -> None:
    """Windows consoles default to cp1252; model text can hold U+202F and similar. Never crash on printing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _f(v, unit=""):
    """Numbers to 0.1 for display; a missing value is spelled out, never '?' or None."""
    if v is None:
        return "not available"
    return f"{round(v, 1):g}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def progress_line(e: dict) -> str | None:
    """One line per agent step as it happens (None for events that are not a step, e.g. tool calls, text chunks)."""
    a, k, d = e["agent"], e["kind"], e["detail"]
    ms = f" ({d['ms']} ms)" if "ms" in d else ""
    msg = None
    if a == "coordinator" and k == "end":
        if "plan" in d:
            msg = f"plan {' -> '.join(d['plan'])}; update {d['update_n']}; {d['mode']}{'; URGENT' if d.get('urgent') else ''}{ms}"
        elif "dropped" in d:
            msg = f"dropped: {d['dropped']}"
        elif "saved" in d:
            msg = f"saved {d['saved']} ({d.get('confidence')})"
        elif "error" in d:
            msg = f"error: {d['error']}; template v1 stays"
        elif "kept" in d:
            msg = "reviewer failed after the last attempt; template v1 stays"
    elif a == "coordinator" and k == "disagreement":
        msg = f"DISAGREEMENT evidence: {d['evidence']} | context: {d['context']} -> UNCERTAIN"
    elif a == "evidence" and k == "end":
        msg = (f"{d['direction']}: stage {_f(d.get('stage_last'), ' ft')}, rate {_f(d.get('rise_rate_ft_per_h'), ' ft/h')}, "
               f"forecast {_f(d.get('forecast_stage'), ' ft')}{ms}")
    elif a == "context" and k == "end":
        rain = "rain data not available" if d.get("rain_3h") is None else f"rain 3 h {_f(d['rain_3h'], ' mm')}"
        msg = (f"{d['direction']}: {d['summary']}{ms}" if d.get("summary") else
               f"{d['direction']}: {rain}, {d.get('n_neighbours', 0)} neighbour gauge(s){ms}")
    elif a == "writer" and k == "revision" and "llm_fallback" in d:
        msg = f"{d['primary']} failed ({d.get('primary_error')}); retried on {d['llm_fallback']}: {'ok' if d['ok'] else 'failed'}"
    elif a == "writer" and k == "revision" and "llm_failed" in d:
        msg = f"LLM failed ({d['llm_failed']}); using the deterministic template text"
    elif a == "writer" and k == "end":
        src = f"{d['provider']} {d.get('model') or ''}".strip() if d.get("llm") else "template text"
        msg = (f"attempt {d['attempt']}/{d.get('max_attempts', 3)}: {src}, {d['chars']} chars, {d['tokens']} tokens"
               f"{' (fallback from ' + d['fallback_from'] + ')' if d.get('fallback_from') and d.get('llm') else ''}{ms}")
    elif a == "reviewer" and k == "verdict":
        msg = f"attempt {d['attempt']}/{d.get('max_attempts', 3)}: {d['verdict'].upper()}"
        if d["reasons"]:
            msg += ": " + "; ".join(d["reasons"])
        elif str(d.get("llm_judgement") or "").startswith("no reply"):   # deterministic checks passed, judge silent
            msg += f" (deterministic checks only; LLM judgement {d['llm_judgement'][:120]})"
    if msg is None:
        return None
    return f"[{e.get('t_s', 0):6.2f}s] {a:<11} {msg}"


def final_line(res: dict, trace_url: str | None = None) -> str:
    """advisory id, version, outcome, tokens and the LangSmith link when tracing is on."""
    if res["outcome"] == "saved":
        s = (f"advisory {res['advisory_id']}  version {res.get('version')}  outcome saved ({res.get('confidence')}, "
             f"reviewer pass after {res.get('attempts')}/3 attempt(s))")
    elif res["outcome"] == "dropped":
        s = f"alert {res['alert_id']}  outcome dropped: {res.get('drop_reason')}"
    else:
        s = f"alert {res['alert_id']}  outcome {res['outcome']}: template v1 stays ({res['alert_id']}|1)"
    s += f"  tokens {res.get('tokens', 0)}  {len(res['events'])} events  {res.get('elapsed_s', 0):g} s"
    if res.get("trace_run_id"):
        s += f"\nLangSmith trace: {trace_url or 'project ' + str(res.get('trace_project')) + ', run ' + res['trace_run_id']}"
    return s


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
    ap.add_argument("--quiet", action="store_true", help="no per-step progress lines")
    args = ap.parse_args(argv)
    utf8_stdio()
    t = tracing.configure()
    prov = llm.provider()
    fb = llm.fallback()
    print(f"LLM_PROVIDER={prov}{' (' + llm.model_name(prov) + ')' if llm.model_name(prov) else ''}"
          f"{'  LLM_FALLBACK=' + fb if fb != 'none' else ''}  LangSmith tracing: {t['reason']}", flush=True)
    conn = connect(args.dsn)
    ensure_schema(conn)
    tools = Tools(conn)
    if args.alert_id:
        def show(e):
            line = progress_line(e)
            if line:
                print(line, flush=True)
        res = graph.run_alert(tools, args.alert_id, force=args.force, on_event=None if args.quiet else show)
        if res.get("draft") and res["outcome"] != "dropped":
            print("\n" + res["draft"] + "\n")
        print(final_line(res, tracing.trace_url(res.get("trace_run_id"), res.get("trace_project"))), flush=True)
        return 0 if res["outcome"] in ("saved", "dropped") else 1
    consume(tools, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
