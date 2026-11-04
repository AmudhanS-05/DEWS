"""Fault drills: break one part of the pipeline and measure the recovery (C1.7, runbook step 13).

    python -m tools.fault_drill --scenario faust                 # kill Faust worker 6068 mid-replay, restart it
    python -m tools.fault_drill --scenario broker                # stop flood-kafka-2 mid-replay, start it again
    python -m tools.fault_drill --scenario sink                  # kill the sink mid-replay, restart it
    python -m tools.fault_drill --scenario postgres              # stop flood-postgres mid-replay, start it again
    python -m tools.fault_drill --scenario notifier              # notifier down, position kept, restart it (sends nothing)
    python -m tools.fault_drill --scenario producer              # kill the live USGS producer, restart it
    python -m tools.fault_drill --scenario sink --no-db          # skip the evaluation_runs row

Replay drills start a replay of a date range after the newest one replayed for the gauge (so every window is new
output; --start/--end override it), break the part after --kill-after seconds, keep it down for --down seconds, and let
the replay finish. recovery_time_s, measured from the restart until:
  faust     the worker logs "Worker: Ready"                      then tools.check_windows must PASS (no window twice)
  broker    the broker is back and no partition is under-replicated     windows must keep flowing during the outage
  sink      the sink's consumer lag is back under --caught-up    then tools.check_sink must PASS (no gaps, no duplicates)
  postgres  the sink's consumer lag is back under --caught-up    then tools.check_sink must PASS; messages wait on Kafka
  producer  a new message arrives on gauge.usgs                  no reading is published a second time (state kept)
  notifier  the notifier has rejoined its group with lag 0       its committed position must not move during the outage

The notifier drill never publishes an approval. While the live notifier is down it runs a --dry-run on a throwaway
group over the approvals already on the topic: bulletins are built and nothing is sent. A person may approve an advisory
in the console during a longer outage (--down 300); that one is then sent on restart, as any approval is.

Every drill except producer also reads the console's status log (http://127.0.0.1:8765, if it is running) for the
failure and the recovery of the node. The row written to evaluation_runs (run_label 'fault_drill:<scenario>') carries
recovery_time_s and the notes. Everything that was stopped is started again, also if the drill fails.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from common import config
from common.progress import Task

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FAUST = str(Path(PY).with_name("faust.exe" if os.name == "nt" else "faust"))
DOWN_STATES = ("down", "absent", "warn")


# ---- pure logic (tests/test_fault_drill.py) -----------------------------------------------------------------------

def next_range(newest: date | None, days: int = 5, fallback: date = date(2019, 6, 1)) -> tuple[str, str]:
    """Replay range after the newest replayed window, so every window is new output. `days` simulated days."""
    start = newest + timedelta(days=2) if newest else fallback
    return start.isoformat(), (start + timedelta(days=days - 1)).isoformat()


def lag_of(committed: int, lo: int, hi: int) -> int:
    """Messages not yet committed on one partition (committed < 0: the group has no offset there yet)."""
    return hi - (committed if committed >= 0 else lo)


def reading_keys(msgs: list[dict]) -> Counter:
    return Counter((m["site_no"], m["parameter"], m["series"], m["reading_ts"], m["value"]) for m in msgs
                   if m.get("type", "reading") == "reading" and not m.get("revision"))


def republished(before: list[dict], after: list[dict]) -> int:
    """Readings published again between two reads of the same topic (revisions are flagged and do not count)."""
    b, a = reading_keys(before), reading_keys(after)
    return sum(n - b[k] for k, n in a.items() if k in b and n > b[k])


def node_changes(events: list[dict], node: str, since: float) -> list[dict]:
    """The console's status changes of one node since `since`, oldest first."""
    return sorted((e for e in events if e["node"] == node and e["ts"] >= since), key=lambda e: e["ts"])


def failed_and_recovered(changes: list[dict]) -> bool:
    """The node left its normal state and came back. Broker counts read '3 up' / '2 up'."""
    if not changes:
        return False
    if " up" in str(changes[0]["to"]):
        return len(changes) >= 2 and changes[-1]["to"] == changes[0]["from"]
    return any(c["to"] in DOWN_STATES for c in changes) and changes[-1]["to"] not in DOWN_STATES


# ---- probes -------------------------------------------------------------------------------------------------------

def topic_count(topic: str) -> int:
    """Messages currently on the topic (sum of end minus start offsets over all partitions)."""
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": "fault-drill-probe"})
    try:
        parts = c.list_topics(topic, timeout=10).topics[topic].partitions
        total = 0
        for p in parts:
            lo, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            total += hi - lo
        return total
    finally:
        c.close()


def group_lag(group: str, topics: list[str]) -> int:
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": group, "enable.auto.commit": False})
    try:
        tps = [TopicPartition(tp, p) for tp in topics for p in c.list_topics(tp, timeout=10).topics[tp].partitions]
        total = 0
        for tp in c.committed(tps, timeout=10):
            lo, hi = c.get_watermark_offsets(TopicPartition(tp.topic, tp.partition), timeout=10)
            total += lag_of(tp.offset, lo, hi)
        return total
    finally:
        c.close()


def committed_offsets(group: str, topic: str) -> dict[int, int]:
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": group, "enable.auto.commit": False})
    try:
        tps = [TopicPartition(topic, p) for p in c.list_topics(topic, timeout=10).topics[topic].partitions]
        return {tp.partition: tp.offset for tp in c.committed(tps, timeout=10)}
    finally:
        c.close()


def admin() -> AdminClient:
    return AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP})


def group_members(group: str) -> int:
    client = admin()                                     # must outlive the future
    return len(client.describe_consumer_groups([group])[group].result(timeout=10).members)


def under_replicated() -> int:
    md = admin().list_topics(timeout=10)
    return sum(1 for t in md.topics.values() for p in t.partitions.values() if len(p.isrs) < len(p.replicas))


def brokers_up() -> int:
    return len(admin().list_topics(timeout=10).brokers)


def pg_ok() -> bool:
    import psycopg2
    try:
        psycopg2.connect(config.PG_DSN, connect_timeout=3).close()
        return True
    except psycopg2.Error:
        return False


def newest_replayed(site: str) -> date | None:
    import psycopg2
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT max(window_start)::date FROM windowed_aggregates WHERE site_no = %s AND mode = 'SIMULATION'", (site,))
        return cur.fetchone()[0]


CONSOLE_URL = "http://127.0.0.1:8765/api/state"
_console_url = CONSOLE_URL
_console_events: dict[tuple, dict] = {}                  # the console keeps only its last 60 changes: collect them here


def console_state() -> dict | None:
    try:
        with urllib.request.urlopen(_console_url, timeout=5) as r:
            st = json.load(r)
    except OSError:
        return None
    for e in st["events"]:
        _console_events[(e["ts"], e["node"], e["to"])] = e
    return st


def watch_console(url: str, every_s: float = 5.0) -> None:
    global _console_url
    _console_url = url

    def loop():
        while True:
            console_state()
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True).start()


def console_report(node_id: str | None, since: float) -> tuple[bool | None, str]:
    """(saw failure and recovery, text) from the console's status log; None when the console is not running."""
    st = console_state()
    if st is None:
        return None, "console not running"
    title = "Kafka brokers" if node_id is None else st["nodes"][node_id]["title"]
    ch = node_changes(list(_console_events.values()), title, since)
    return failed_and_recovered(ch), f"console {title}: " + (", ".join(f"{c['from']}->{c['to']}" for c in ch) or "no change logged")


# ---- processes ----------------------------------------------------------------------------------------------------

def _pids(where: str) -> list[int]:
    cmd = f"Get-CimInstance Win32_Process | Where-Object {{ {where} }} | ForEach-Object {{ $_.ProcessId }}"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def find_pids(port: int) -> list[int]:
    """Process ids of the Faust worker started with --web-port <port> (launcher and python child)."""
    return _pids(f"$_.Name -match '^(python|faust)' -and $_.CommandLine -match 'faust_app' -and $_.CommandLine -match 'web-port {port}'")


def module_pids(module: str) -> list[int]:
    """Process ids of `python -m <module>` (launcher and python child)."""
    return _pids(f"$_.Name -match '^python' -and $_.CommandLine -match '-m {module.replace('.', '[.]')}'")


def kill(pids: list[int]):
    for pid in pids:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                       capture_output=True)


def spawn(args: list[str], log: Path) -> subprocess.Popen:
    """Start a process that outlives the drill, with its output in `log`."""
    log.parent.mkdir(exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(args, cwd=ROOT, stdout=open(log, "w"), stderr=subprocess.STDOUT, creationflags=flags)


def start_worker(port: int, log: Path) -> subprocess.Popen:
    return spawn([FAUST, "-A", "streams.faust_app", "worker", "-l", "info", "--web-port", str(port)], log)


def start_module(module: str, log: Path) -> subprocess.Popen:
    return spawn([PY, "-m", module], log)


def docker(*args: str) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"docker {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def wait_ready(log: Path, timeout_s: float) -> bool:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if log.exists() and "Worker]: Ready" in log.read_text(errors="ignore"):
            return True
        time.sleep(0.5)
    return False


def wait_until(pred, timeout_s: float, step_s: float = 1.0) -> float | None:
    """Seconds until pred() is true, or None on timeout. A probe failing (stack still coming up) counts as false."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            if pred():
                return time.monotonic() - t0
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step_s)
    return None


def run_check(*module_args: str) -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", *module_args], cwd=ROOT, capture_output=True, text=True, errors="replace")
    lines = r.stdout.strip().splitlines()
    return r.returncode == 0, (lines[-1] if lines else "no output")


# ---- drills -------------------------------------------------------------------------------------------------------

def finish(t: Task, checks: list[tuple[str, bool]], recovery: float | None, notes: str, node, since: float, **extra) -> dict:
    """Summarise a drill. `checks` are the pass conditions; the console log is one when the console is running.
    node: the console's node id, None for the broker count, False to skip the console."""
    if node is not False:
        time.sleep(4)                                    # the console polls every 2 s
        seen, text = console_report(node, since)
        notes += "; " + text
        if seen is not None:
            checks = checks + [("console log shows failure and recovery", seen)]
    failed = [name for name, good in checks if not good]
    notes += "; FAILED: " + ", ".join(failed) if failed else ""
    t.summary(("FAIL" if failed else "PASS") + f": recovery_time_s={-1.0 if recovery is None else recovery:.1f}; {notes}")
    return {"ok": not failed, "recovery_time_s": recovery, "notes": notes, **extra}


DRILL_STATE = ROOT / "gate_output" / "drill_state.json"


def mark(node: str, **kw) -> None:
    """What the drill has taken down right now, for the console: it shows the part as down at once instead of waiting
    for Kafka's session timeout, and Stop on the console restarts it. kw: killed_at / restarted_at plus how to restart."""
    try:
        old = json.loads(DRILL_STATE.read_text(encoding="utf-8")) if kw.get("restarted_at") else {}
    except (OSError, ValueError):
        old = {}
    DRILL_STATE.parent.mkdir(exist_ok=True)
    DRILL_STATE.write_text(json.dumps({**old, "node": node, **kw}), encoding="utf-8")


def replay_range(a) -> tuple[str, str]:
    return (a.start, a.end) if a.start and a.end else next_range(newest_replayed(a.site))


def start_replay(a, start: str, end: str) -> subprocess.Popen:
    return subprocess.Popen([PY, "-m", "producers.replay_producer", "--site", a.site, "--start", start, "--end", end,
                             "--speed", str(a.speed)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_phase(t: Task, t0: float, until: float, note) -> None:
    while time.monotonic() < until:
        time.sleep(2)
        try:
            t.update(time.monotonic() - t0, note())
        except Exception as e:  # noqa: BLE001 - a probe failing mid-outage is expected
            t.update(time.monotonic() - t0, f"probe failed: {type(e).__name__}")


def faust_drill(a) -> dict:
    down = a.down or 70                                  # beyond Faust's 60 s session timeout: the group shows 2/3
    start, end = replay_range(a)
    with Task(f"Faust kill drill (worker {a.port}, replay {start}..{end} at speed {a.speed})", total=a.kill_after + down + a.settle, unit="s") as t:
        if not find_pids(a.port):
            raise SystemExit(f"no Faust worker on web port {a.port}: start the three workers first")
        since = time.time()
        n0 = topic_count(config.TOPIC_AGG)
        replay = start_replay(a, start, end)
        t0 = time.monotonic()
        wait_phase(t, t0, t0 + a.kill_after, lambda: f"replay running, windows +{topic_count(config.TOPIC_AGG) - n0}")
        n_kill = topic_count(config.TOPIC_AGG)
        kill(find_pids(a.port))
        mark("faust", port=a.port, killed_at=time.time())
        t_kill = time.monotonic()
        t.update(t_kill - t0, f"KILLED worker {a.port}; windows so far +{n_kill - n0}")
        log = ROOT / "gate_output" / f"faust_{a.port}_drill.log"
        try:
            wait_phase(t, t0, t_kill + down, lambda: f"worker down, windows +{topic_count(config.TOPIC_AGG) - n0}")
            n_down = topic_count(config.TOPIC_AGG)
        finally:
            t_restart = time.monotonic()
            start_worker(a.port, log)
            mark("faust", restarted_at=time.time())
        ready = wait_ready(log, a.ready_timeout)
        recovery = time.monotonic() - t_restart if ready else None
        t.update(time.monotonic() - t0, f"worker restarted, Ready={ready} after {time.monotonic() - t_restart:.1f}s")
        replay.communicate(timeout=600)
        time.sleep(a.settle)
        n_end = topic_count(config.TOPIC_AGG)
        good, verdict = run_check("tools.check_windows", "--mode", "SIMULATION", "--site", a.site)
        notes = (f"killed worker {a.port} at +{a.kill_after:g}s for {down:g}s during replay {start}..{end}; "
                 f"windows during outage +{n_down - n_kill}, total new +{n_end - n0}; {verdict}")
        return finish(t, [("worker ready", ready), ("windows during outage", n_down > n_kill),
                          ("check_windows", verdict.startswith("PASS"))], recovery, notes, "faust", since)


def broker_drill(a) -> dict:
    down, name = a.down or 30, "flood-kafka-2"
    start, end = replay_range(a)
    with Task(f"Broker stop drill ({name}, replay {start}..{end} at speed {a.speed})", total=a.kill_after + down + a.settle, unit="s") as t:
        if brokers_up() != 3:
            raise SystemExit("need all 3 brokers up before the drill")
        since = time.time()
        n0 = topic_count(config.TOPIC_AGG)
        replay = start_replay(a, start, end)
        t0 = time.monotonic()
        wait_phase(t, t0, t0 + a.kill_after, lambda: f"replay running, windows +{topic_count(config.TOPIC_AGG) - n0}")
        n_kill = topic_count(config.TOPIC_AGG)
        mark("broker", name=name, killed_at=time.time())
        docker("stop", name)
        t_stop = time.monotonic()
        t.update(t_stop - t0, f"STOPPED {name}; windows so far +{n_kill - n0}")
        try:
            wait_phase(t, t0, t_stop + down, lambda: f"broker down, windows +{topic_count(config.TOPIC_AGG) - n0}, under-replicated {under_replicated()}")
            n_down = topic_count(config.TOPIC_AGG)
            ur_down = under_replicated()
        finally:
            docker("start", name)
            mark("broker", restarted_at=time.time())
        recovery = wait_until(lambda: brokers_up() == 3 and under_replicated() == 0, a.ready_timeout, 2)
        t.update(time.monotonic() - t0, f"{name} back, all ISRs full after {recovery}s" if recovery else f"{name} NOT recovered")
        out = replay.communicate(timeout=600)[0].strip().splitlines()
        time.sleep(a.settle)
        good, verdict = run_check("tools.check_windows", "--mode", "SIMULATION", "--site", a.site)
        sent = out[-1] if out else "no replay output"
        notes = (f"stopped {name} at +{a.kill_after:g}s for {down:g}s during replay {start}..{end}; under-replicated while down {ur_down}; "
                 f"windows during outage +{n_down - n_kill}; replay {sent}; {verdict}")
        return finish(t, [("recovered", recovery is not None), ("windows during outage", n_down > n_kill),
                          ("replay lost nothing", "failed=0" in sent), ("check_windows", verdict.startswith("PASS"))],
                      recovery, notes, None, since)


def sink_topics() -> list[str]:
    return [config.TOPIC_GAUGE, config.TOPIC_REPLAY_GAUGE, config.TOPIC_AGG, config.TOPIC_FORECASTS, config.TOPIC_ALERTS]


def sink_check_after_replay(t: Task, a, replay, t0: float, drain_s: float = 180) -> tuple[bool, str]:
    """Replay finished, everything drained into PostgreSQL: compare the topics with the tables."""
    replay.communicate(timeout=600)
    time.sleep(a.settle)
    drained = wait_until(lambda: group_lag(config.GROUP_SINK, sink_topics()) == 0, drain_s, 2) is not None
    t.update(time.monotonic() - t0, f"drained={drained}; running check_sink")
    good, verdict = run_check("tools.check_sink", "--mode", "SIMULATION")
    return drained and good, verdict


def sink_drill(a) -> dict:
    down = a.down or 60                                  # beyond the 45 s session timeout: the group shows as empty
    start, end = replay_range(a)
    log = ROOT / "gate_output" / "sink_drill.log"
    with Task(f"Sink kill drill (replay {start}..{end} at speed {a.speed})", total=a.kill_after + down + a.settle, unit="s") as t:
        if not module_pids("storage.sink"):
            raise SystemExit("the sink is not running: start `python -m storage.sink` first")
        since = time.time()
        replay = start_replay(a, start, end)
        t0 = time.monotonic()
        wait_phase(t, t0, t0 + a.kill_after, lambda: f"replay running, sink lag {group_lag(config.GROUP_SINK, sink_topics())}")
        kill(module_pids("storage.sink"))
        mark("sink", killed_at=time.time())
        t_kill = time.monotonic()
        t.update(t_kill - t0, "KILLED the sink")
        try:
            wait_phase(t, t0, t_kill + down, lambda: f"sink down, lag {group_lag(config.GROUP_SINK, sink_topics())}")
            lag_down = group_lag(config.GROUP_SINK, sink_topics())
        finally:
            start_module("storage.sink", log)
            mark("sink", restarted_at=time.time())
        recovery = wait_until(lambda: group_lag(config.GROUP_SINK, sink_topics()) <= a.caught_up, a.ready_timeout)
        t.update(time.monotonic() - t0, f"sink restarted, caught up after {recovery}s" if recovery else "sink did NOT catch up")
        good, verdict = sink_check_after_replay(t, a, replay, t0)
        notes = (f"killed the sink at +{a.kill_after:g}s for {down:g}s during replay {start}..{end}; lag when restarted {lag_down}; "
                 f"caught up means lag <= {a.caught_up}; {verdict}")
        return finish(t, [("caught up", recovery is not None), ("messages waited on Kafka", lag_down > 0), ("check_sink", good)],
                      recovery, notes, "sink", since)


def postgres_drill(a) -> dict:
    down, name = a.down or 30, "flood-postgres"
    start, end = replay_range(a)
    with Task(f"PostgreSQL stop drill (replay {start}..{end} at speed {a.speed})", total=a.kill_after + down + a.settle, unit="s") as t:
        if not module_pids("storage.sink"):
            raise SystemExit("the sink is not running: start `python -m storage.sink` first")
        since = time.time()
        replay = start_replay(a, start, end)
        t0 = time.monotonic()
        wait_phase(t, t0, t0 + a.kill_after, lambda: f"replay running, sink lag {group_lag(config.GROUP_SINK, sink_topics())}")
        docker("stop", name)
        t_stop = time.monotonic()
        t.update(t_stop - t0, f"STOPPED {name}")
        try:
            wait_phase(t, t0, t_stop + down, lambda: f"postgres down, sink lag {group_lag(config.GROUP_SINK, sink_topics())}")
            lag_down = group_lag(config.GROUP_SINK, sink_topics())
        finally:
            t_start = time.monotonic()
            docker("start", name)
        pg_back = wait_until(pg_ok, a.ready_timeout)
        caught = wait_until(lambda: group_lag(config.GROUP_SINK, sink_topics()) <= a.caught_up, a.ready_timeout)
        recovery = None if caught is None else time.monotonic() - t_start
        t.update(time.monotonic() - t0, f"postgres back after {pg_back}s; sink caught up {recovery}s after the start")
        alive = bool(module_pids("storage.sink"))
        good, verdict = sink_check_after_replay(t, a, replay, t0)
        notes = (f"stopped {name} at +{a.kill_after:g}s for {down:g}s during replay {start}..{end}; sink lag at the start {lag_down}; "
                 f"accepting connections after {pg_back if pg_back is None else round(pg_back, 1)}s; sink survived without a restart: {alive}; {verdict}")
        return finish(t, [("caught up", recovery is not None), ("messages waited on Kafka", lag_down > 0), ("sink survived", alive),
                          ("check_sink", good)], recovery, notes, "pg", since)


def notifier_drill(a) -> dict:
    down = a.down or 60                                  # beyond the 45 s session timeout: the group shows as empty
    log = ROOT / "gate_output" / "notifier_drill.log"
    with Task("Notifier down drill (the drill itself sends nothing)", total=down, unit="s") as t:
        if not module_pids("alerting.notifier"):
            raise SystemExit("the notifier is not running: start `python -m alerting.notifier` first")
        since = time.time()
        before = committed_offsets(config.GROUP_NOTIFIER, config.TOPIC_APPROVED)
        kill(module_pids("alerting.notifier"))
        t0 = time.monotonic()
        t.update(0, "KILLED the notifier")
        try:
            approved = topic_count(config.TOPIC_APPROVED)
            dry = subprocess.run([PY, "-m", "alerting.notifier", "--dry-run", "--group", "fault-drill-" + uuid.uuid4().hex[:8],
                                  "--exit-when-idle", "15"], cwd=ROOT, capture_output=True, text=True, errors="replace")
            n_dry = (dry.stdout + dry.stderr).count("DRY RUN: would send")
            t.update(time.monotonic() - t0, f"dry run while down: {n_dry} of {approved} approvals on the topic rendered, none sent")
            wait_phase(t, t0, t0 + down, lambda: f"notifier down, lag {group_lag(config.GROUP_NOTIFIER, [config.TOPIC_APPROVED])}")
            after = committed_offsets(config.GROUP_NOTIFIER, config.TOPIC_APPROVED)
            waiting = group_lag(config.GROUP_NOTIFIER, [config.TOPIC_APPROVED])
        finally:
            start_module("alerting.notifier", log)
        recovery = wait_until(lambda: group_members(config.GROUP_NOTIFIER) >= 1
                              and group_lag(config.GROUP_NOTIFIER, [config.TOPIC_APPROVED]) == 0, a.ready_timeout)
        t.update(time.monotonic() - t0, f"restarted, rejoined after {recovery}s" if recovery else "NOT rejoined")
        notes = (f"notifier down {down:g}s; committed position unchanged: {before == after}; approvals waiting at restart {waiting}; "
                 f"dry run on a throwaway group rendered {n_dry} of {approved} approvals, the drill sent no email")
        return finish(t, [("rejoined", recovery is not None), ("position kept", before == after), ("dry run saw every approval", n_dry == approved)],
                      recovery, notes, "notifier", since)


def producer_drill(a) -> dict:
    from tools.check_sink import read_topic
    down = a.down or 20
    log = ROOT / "gate_output" / "producer_drill.log"
    with Task("Live producer crash drill (USGS producer, gauge.usgs)", total=down + a.settle, unit="s") as t:
        if not module_pids("producers.usgs_iv_producer"):
            raise SystemExit("the live producer is not running: start `python -m producers.usgs_iv_producer` first")
        kill(module_pids("producers.usgs_iv_producer"))
        t0 = time.monotonic()
        t.update(0, "KILLED the producer")
        try:
            before = read_topic(config.TOPIC_GAUGE)
            n_before = topic_count(config.TOPIC_GAUGE)
            time.sleep(max(0.0, down - (time.monotonic() - t0)))
        finally:
            start_module("producers.usgs_iv_producer", log)
        recovery = wait_until(lambda: topic_count(config.TOPIC_GAUGE) > n_before, a.ready_timeout)
        t.update(time.monotonic() - t0, f"first message after {recovery}s" if recovery else "NO message after the restart")
        time.sleep(a.settle)
        after = read_topic(config.TOPIC_GAUGE)
        again = republished(before, after)
        polls = [ln for ln in log.read_text(errors="ignore").splitlines() if "sent=" in ln]
        notes = (f"killed the live producer for {down:g}s; +{len(after) - len(before)} messages after the restart, "
                 f"readings published a second time: {again}; first poll: {polls[0] if polls else 'not logged'}; "
                 "console stale check not exercised, it needs a longer outage (runbook step 5)")
        return finish(t, [("published again", recovery is not None), ("no reading published twice", again == 0)],
                      recovery, notes, False, 0, data_source="live")


def failure_reason(notes: str) -> str | None:
    m = re.search(r"FAILED: ([^(]*)", notes)
    return m.group(1).strip() if m else None


def record(label: str, recovery: float | None, notes: str, data_source: str = "replay", ok: bool = True):
    """Every run gets a row, a failed one too (status failed + reason). Failed rows are never deleted."""
    import psycopg2
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO evaluation_runs (run_label, data_source, recovery_time_s, notes, status, failure_reason) VALUES (%s,%s,%s,%s,%s,%s)",
                    (label, data_source, recovery, notes, "passed" if ok else "failed", None if ok else failure_reason(notes) or "drill failed"))


def llm_drill(a) -> dict:
    """LLM down (C4.3): the agents' own check, agentic_ai/drill.py. Template v1 stays and nothing is blocked. Uses the newest replayed alert."""
    from agentic_ai.drill import llm_down
    with Task("LLM down drill (forced down, no network call)", total=1, unit=" run") as t:
        res = llm_down()
        t.update(1, "agents finished")
        notes = str(res.get("notes", ""))
        if not res.get("ok"):
            notes += "; FAILED: LLM down check"
        t.summary(f"{'PASS' if res.get('ok') else 'FAIL'}: recovery_time_s={res.get('recovery_time_s')}; {notes}")
    return {"ok": bool(res.get("ok")), "recovery_time_s": res.get("recovery_time_s"), "notes": notes, "data_source": "replay"}


SCENARIOS = {"llm": llm_drill, "faust": faust_drill, "broker": broker_drill, "sink": sink_drill, "postgres": postgres_drill,
             "notifier": notifier_drill, "producer": producer_drill}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    ap.add_argument("--port", type=int, default=6068)
    ap.add_argument("--site", default="08158700")
    ap.add_argument("--start", help="replay start, YYYY-MM-DD (default: after the newest replayed window)")
    ap.add_argument("--end", help="replay end")
    ap.add_argument("--speed", type=float, default=3600)
    ap.add_argument("--kill-after", type=float, default=20)
    ap.add_argument("--down", type=float, help="seconds the part stays down (default per scenario)")
    ap.add_argument("--caught-up", type=int, default=50, help="sink lag that counts as caught up during a replay")
    ap.add_argument("--settle", type=float, default=15)
    ap.add_argument("--ready-timeout", type=float, default=180)
    ap.add_argument("--console", default=CONSOLE_URL, help="the console's /api/state URL")
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    watch_console(a.console)
    res = SCENARIOS[a.scenario](a)
    if not a.no_db:
        record(f"fault_drill:{a.scenario}", res["recovery_time_s"], res["notes"], res.get("data_source", "replay"), res["ok"])
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
