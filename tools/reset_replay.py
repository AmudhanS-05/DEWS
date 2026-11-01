"""Make a replayed flood repeatable: clear one gauge's replay state and its SIMULATION rows, so the same dates replay from scratch.

    python -m tools.reset_replay --site 08158700 --start 2019-05-02 --end 2019-05-06            # asks first
    python -m tools.reset_replay --site AHOT2,LLAT2 --start 2023-10-25 --end 2023-10-27 --yes
    python -m tools.reset_replay --site 08158700 --start 2019-05-02 --end 2019-05-06 --dry-run  # counts only

Why it is needed: Faust keeps a window position per gauge in a changelog-backed table. A reading older than that position and
the 3 closed windows kept for late data is `too_late`: it goes to deadletter.flood and no window, forecast or alert follows.
The alert id also repeats for the same flood, so an old row would keep its old status.

What it does, in order:
  1. waits until the forecaster and the sink have caught up, so no row in flight lands after the delete;
  2. stops the Faust workers and the forecaster (the live pipeline waits on Kafka and catches up, nothing is lost);
  3. writes a tombstone for the gauge to Faust's replay changelogs and removes the gauge's replay entry from the forecaster's
     state file. Live state is never touched;
  4. deletes SIMULATION rows of that gauge and date range (--end included), in one transaction: readings, windows, forecasts,
     traces, alerts raised in the range and their advisories and agent records. Alerts and advisories are first saved to
     gate_output/reset_replay_<time>.jsonl. evaluation_runs and LIVE rows are never touched;
  5. starts the workers and the forecaster again and waits for Faust to be ready.
The Kafka topics keep their history. tools.check_windows and tools.check_sink read state/replay_resets.json and leave out replay
records older than the reset.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from common import config
from common.progress import Task
from common.timeutil import now_epoch

MODE = "SIMULATION"
ROOT = Path(__file__).resolve().parent.parent
RESETS_FILE = Path(config.STATE_DIR) / "replay_resets.json"
FORECASTER_STATE = Path(config.STATE_DIR) / "forecaster_state.json"
PORTS = (6066, 6067, 6068)
CHANGELOGS = ("flood-windowing-window_state_replay-changelog", "flood-windowing-rain_state_replay-changelog")
SITE_MODE = "site_no = %(site)s AND mode = 'SIMULATION'"
ALERTS_IN_RANGE = ("SELECT alert_id FROM alerts WHERE site_no = %(site)s AND mode = 'SIMULATION' AND split_part(alert_id, '|', 3) ~ '^[0-9]{4}-' "
                   "AND split_part(alert_id, '|', 3)::timestamptz >= %(start)s AND split_part(alert_id, '|', 3)::timestamptz < %(end)s")
# table -> WHERE clause; every clause is limited to SIMULATION, directly or through the alert it belongs to
DELETES = [
    ("agent_events", f"alert_id IN ({ALERTS_IN_RANGE})"),
    ("agent_runs", f"alert_id IN ({ALERTS_IN_RANGE})"),
    ("advisories", f"alert_id IN ({ALERTS_IN_RANGE})"),
    ("alerts", f"alert_id IN ({ALERTS_IN_RANGE})"),
    ("latency_trace", f"{SITE_MODE} AND reading_time >= %(start)s AND reading_time < %(end)s"),
    ("risk_scores", f"{SITE_MODE} AND window_start >= %(start)s AND window_start < %(end)s"),
    ("windowed_aggregates", f"{SITE_MODE} AND window_start >= %(start)s AND window_start < %(end)s"),
    ("gauge_readings", f"{SITE_MODE} AND reading_time >= %(start)s AND reading_time < %(end)s"),
]


def date_range(start: str, end: str) -> dict:
    """--end is included, as in the replay producer. Timestamps in UTC."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b < a:
        raise ValueError(f"--end {end} is before --start {start}")
    return {"start": f"{a.isoformat()}T00:00:00Z", "end": f"{(b + timedelta(days=1)).isoformat()}T00:00:00Z"}


def resolve_sites(text: str, path: Path = ROOT / "config" / "gauge_locations.csv") -> list[str]:
    """Site numbers or NWPS ids, comma separated."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by = {r["site_no"]: r["site_no"] for r in rows} | {r["lid"]: r["site_no"] for r in rows}
    out = []
    for s in (x.strip() for x in text.split(",") if x.strip()):
        if s not in by:
            raise ValueError(f"unknown gauge {s}: not in {path.name}")
        out.append(by[s])
    return out


def count_rows(conn, site: str, span: dict) -> dict[str, int]:
    args = {"site": site, **span}
    with conn.cursor() as cur:
        out = {}
        for table, where in DELETES:
            cur.execute(f"SELECT count(*) FROM {table} WHERE {where}", args)      # table and clause come from the constant above
            out[table] = cur.fetchone()[0]
    return out


def delete_rows(conn, site: str, span: dict, backup=None) -> dict[str, int]:
    """One transaction. `backup` (a text file) receives the alerts and advisories first, one JSON object per line."""
    args = {"site": site, **span}
    out = {}
    with conn, conn.cursor() as cur:
        if backup is not None:
            for table in ("alerts", "advisories"):
                cur.execute(f"SELECT row_to_json(t) FROM {table} t WHERE alert_id IN ({ALERTS_IN_RANGE})", args)
                for (row,) in cur.fetchall():
                    backup.write(json.dumps({"table": table, "row": row}) + "\n")
        for table, where in DELETES:
            cur.execute(f"DELETE FROM {table} WHERE {where}", args)
            out[table] = cur.rowcount
    return out


def drop_forecaster_state(states: dict, sites: list[str]) -> dict:
    """The forecaster's state without these gauges' replay entries. Live entries stay."""
    gone = {f"{s}|{MODE}" for s in sites}
    return {k: v for k, v in states.items() if k not in gone}


def changelog_partitions(topic: str, keys: set[bytes]) -> dict[bytes, int]:
    """key -> partition holding it in a Faust changelog (Faust writes it to the partition of the source record)."""
    from confluent_kafka import Consumer, KafkaException, TopicPartition
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": "reset-replay-" + uuid.uuid4().hex[:8], "enable.auto.commit": False})
    found: dict[bytes, int] = {}
    try:
        md = c.list_topics(topic, timeout=10).topics.get(topic)
        if md is None or md.error is not None:
            return found                                                       # no such table yet
        end, tps = {}, []
        for p in md.partitions:
            lo, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            if hi > lo:
                end[p] = hi
                tps.append(TopicPartition(topic, p, lo))
        c.assign(tps)
        while end:
            m = c.poll(5)
            if m is None:
                raise KafkaException(f"{topic}: no data for 5 s with partitions {sorted(end)} unread")
            if m.error():
                continue
            if m.key() in keys:
                found[m.key()] = m.partition()
            if m.offset() + 1 >= end.get(m.partition(), 0):
                end.pop(m.partition(), None)
    finally:
        c.close()
    return found


def write_tombstones(sites: list[str]) -> int:
    from confluent_kafka import Producer
    prod = Producer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "acks": "all"})
    keys = {json.dumps(s).encode(): s for s in sites}
    n = 0
    for topic in CHANGELOGS:
        for key, part in changelog_partitions(topic, set(keys)).items():
            prod.produce(topic, key=key, value=None, partition=part)
            n += 1
    if prod.flush(30):
        raise RuntimeError("could not write the tombstones")
    return n


def stop_consumers(t: Task) -> None:
    from tools.fault_drill import find_pids, kill, module_pids
    kill(module_pids("streams.forecaster"))
    for port in PORTS:
        kill(find_pids(port))
    t.update(2, "Faust workers and forecaster stopped")


def start_consumers(t: Task, timeout_s: float = 240.0) -> float:
    """Returns seconds until every worker logged Ready."""
    from tools.fault_drill import start_module, start_worker
    logs = {p: ROOT / "gate_output" / f"faust_{p}.log" for p in PORTS}
    t0 = time.monotonic()
    for p, log in logs.items():
        start_worker(p, log)
    start_module("streams.forecaster", ROOT / "gate_output" / "forecaster.log")
    waiting = set(PORTS)
    while waiting and time.monotonic() - t0 < timeout_s:
        time.sleep(2)
        waiting = {p for p in waiting if "Worker]: Ready" not in logs[p].read_text(encoding="utf-8", errors="ignore")}
        t.update(4, f"waiting for Faust: {len(PORTS) - len(waiting)}/{len(PORTS)} ready")
    if waiting:
        raise RuntimeError(f"Faust workers {sorted(waiting)} not ready after {timeout_s:g} s: see gate_output")
    return time.monotonic() - t0


def wait_caught_up(timeout_s: float = 90.0) -> list[str]:
    from tools.fault_drill import group_lag
    want = {config.GROUP_FORECASTER: [config.TOPIC_AGG], config.GROUP_SINK: [config.TOPIC_AGG, config.TOPIC_REPLAY_GAUGE, config.TOPIC_FORECASTS, config.TOPIC_ALERTS]}
    deadline = time.monotonic() + timeout_s
    while True:
        behind = [g for g, topics in want.items() if group_lag(g, topics) > 0]
        if not behind or time.monotonic() > deadline:
            return behind
        time.sleep(1.5)


def load_resets(path: Path = RESETS_FILE) -> dict[str, float]:
    """site_no -> wall-clock time of its newest reset."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def superseded(site: str | None, mode: str | None, kafka_ts: float, resets: dict[str, float]) -> bool:
    """A replay record older than its gauge's reset: its rows were deleted, so the check tools leave it out."""
    return mode == MODE and kafka_ts < resets.get(site, 0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", required=True, help="site number or NWPS id, comma separated")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC), included")
    p.add_argument("--yes", action="store_true", help="do not ask")
    p.add_argument("--dry-run", action="store_true", help="print the row counts, change nothing")
    a = p.parse_args(argv)
    import psycopg2
    try:
        sites, span = resolve_sites(a.site), date_range(a.start, a.end)
    except ValueError as e:
        print(f"reset_replay: {e}")
        return 2
    conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
    for s in sites:
        print(f"{s} {a.start}..{a.end} SIMULATION rows: " + ", ".join(f"{k} {v}" for k, v in count_rows(conn, s, span).items()))
    if a.dry_run:
        print("dry run: nothing changed")
        return 0
    if not a.yes and input("Stop Faust and the forecaster for a moment, clear the replay state and delete these rows? [y/N] ").strip().lower() != "y":
        print("nothing changed")
        return 1
    backup_path = ROOT / "gate_output" / f"reset_replay_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with Task(f"reset_replay {','.join(sites)} {a.start}..{a.end}", total=5, unit=" steps") as t:
        behind = wait_caught_up()
        if behind:
            t.summary(f"FAIL: {', '.join(behind)} still behind after 90 s; nothing changed")
            return 1
        t.update(1, "forecaster and sink caught up")
        stop_consumers(t)
        cleared = write_tombstones(sites)
        states = json.loads(FORECASTER_STATE.read_text(encoding="utf-8")) if FORECASTER_STATE.exists() else {}
        tmp = FORECASTER_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(drop_forecaster_state(states, sites)), encoding="utf-8")
        os.replace(tmp, FORECASTER_STATE)
        t.update(3, f"replay state cleared ({cleared} changelog entries)")
        os.makedirs(backup_path.parent, exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            done = {s: delete_rows(conn, s, span, f) for s in sites}
        RESETS_FILE.write_text(json.dumps({**load_resets(), **{s: now_epoch() for s in sites}}), encoding="utf-8")
        total = sum(sum(c.values()) for c in done.values())
        t.update(4, f"{total} rows deleted")
        ready = start_consumers(t)
        t.update(5, f"Faust ready after {ready:.0f} s")
        t.summary(f"PASS: replay state cleared for {len(sites)} gauge(s), {total} rows deleted, Faust ready after {ready:.0f} s; alerts and advisories saved to {backup_path.name}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
