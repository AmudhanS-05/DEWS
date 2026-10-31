"""Fault drills: break one part of the pipeline while a replay is running and measure the recovery (C1.7).

    python -m tools.fault_drill --scenario faust                 # kill Faust worker 6068 mid-replay, restart it
    python -m tools.fault_drill --scenario faust --no-db         # skip the evaluation_runs row

Faust drill, step by step:
  1. start a replay of a date range not replayed before (so every window is new output)
  2. after --kill-after seconds, kill the Faust worker on --port
  3. keep the replay running for --down seconds; count windows arriving on aggregates.window meanwhile
  4. restart the worker; recovery_time_s = restart until the worker logs "Worker: Ready" again
  5. let the replay finish, then run tools.check_windows (must PASS: no window emitted twice)
The row written to evaluation_runs (run_label 'fault_drill:faust') carries recovery_time_s and the notes.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition

from common import config
from common.progress import Task

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FAUST = str(Path(PY).with_name("faust.exe" if os.name == "nt" else "faust"))


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


def find_pids(port: int) -> list[int]:
    """Process ids of the Faust worker started with --web-port <port> (launcher and python child)."""
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|faust)' -and "
           f"$_.CommandLine -match 'faust_app' -and $_.CommandLine -match 'web-port {port}' }} | "
           "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def kill(pids: list[int]):
    for pid in pids:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                       capture_output=True)


def start_worker(port: int, log: Path) -> subprocess.Popen:
    return subprocess.Popen([FAUST, "-A", "streams.faust_app", "worker", "-l", "info", "--web-port", str(port)],
                            cwd=ROOT, stdout=open(log, "w"), stderr=subprocess.STDOUT)


def wait_ready(log: Path, timeout_s: float) -> bool:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if log.exists() and "Worker]: Ready" in log.read_text(errors="ignore"):
            return True
        time.sleep(0.5)
    return False


def faust_drill(a) -> dict:
    total = a.kill_after + a.down + a.settle
    with Task(f"Faust kill drill (worker {a.port}, replay {a.start}..{a.end} at speed {a.speed})", total=total, unit="s") as t:
        if not find_pids(a.port):
            raise SystemExit(f"no Faust worker on web port {a.port}: start the three workers first")
        n0 = topic_count(config.TOPIC_AGG)
        replay = subprocess.Popen([PY, "-m", "producers.replay_producer", "--site", a.site, "--start", a.start,
                                   "--end", a.end, "--speed", str(a.speed)], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.kill_after:
            time.sleep(2)
            t.update(time.monotonic() - t0, f"replay running, windows +{topic_count(config.TOPIC_AGG) - n0}")
        n_kill = topic_count(config.TOPIC_AGG)
        kill(find_pids(a.port))
        t_kill = time.monotonic()
        t.update(t_kill - t0, f"KILLED worker {a.port}; windows so far +{n_kill - n0}")
        while time.monotonic() - t_kill < a.down:
            time.sleep(2)
            t.update(time.monotonic() - t0, f"worker down, windows +{topic_count(config.TOPIC_AGG) - n0}")
        n_down = topic_count(config.TOPIC_AGG)
        log = ROOT / "gate_output" / f"faust_{a.port}_drill.log"
        t_restart = time.monotonic()
        proc = start_worker(a.port, log)
        ready = wait_ready(log, a.ready_timeout)
        recovery = time.monotonic() - t_restart
        t.update(time.monotonic() - t0, f"worker restarted, Ready={ready} after {recovery:.1f}s")
        replay_out = replay.communicate(timeout=600)[0]
        time.sleep(a.settle)
        n_end = topic_count(config.TOPIC_AGG)
        chk = subprocess.run([PY, "-m", "tools.check_windows", "--mode", "SIMULATION", "--site", a.site],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()
        verdict = chk[-1] if chk else "no output"
        flowed = (n_down - n_kill) > 0
        ok = ready and flowed and verdict.startswith("PASS")
        notes = (f"killed worker {a.port} at +{a.kill_after}s for {a.down}s during replay {a.start}..{a.end}; "
                 f"windows during outage +{n_down - n_kill}, total new +{n_end - n0}; {verdict}")
        t.summary(("PASS" if ok else "FAIL") + f": recovery_time_s={recovery:.1f}; {notes}")
        return {"ok": ok, "recovery_time_s": recovery, "notes": notes, "proc": proc, "replay": replay_out.strip().splitlines()[-1:]}


def record(label: str, recovery: float, notes: str):
    import psycopg2
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO evaluation_runs (run_label, data_source, recovery_time_s, notes) VALUES (%s,'replay',%s,%s)",
                    (label, recovery, notes))


SCENARIOS = {"faust": faust_drill}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    ap.add_argument("--port", type=int, default=6068)
    ap.add_argument("--site", default="08158700")
    ap.add_argument("--start", default="2019-06-01")
    ap.add_argument("--end", default="2019-06-05")
    ap.add_argument("--speed", type=float, default=3600)
    ap.add_argument("--kill-after", type=float, default=20)
    ap.add_argument("--down", type=float, default=25)
    ap.add_argument("--settle", type=float, default=15)
    ap.add_argument("--ready-timeout", type=float, default=180)
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    res = SCENARIOS[a.scenario](a)
    if not a.no_db:
        record(f"fault_drill:{a.scenario}", res["recovery_time_s"], res["notes"])
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
