"""Throughput run (runbook step 6): replay one gauge's whole history as fast as possible, measure msg/s and how the windowing lag drains.

    python -m tools.throughput_run                                            # 08158700, 2017-01-01..2026-09-30
    python -m tools.throughput_run --site 08158700 --start 2017-01-01 --end 2026-09-30

Writes one evaluation_runs row (run_label throughput:replay, throughput_msgs_s, notes with the peak lag and drain time). The
agent consumer must not be running: thousands of replayed windows would call the LLM. It refuses to start if one is.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time

from common import config
from common.progress import Task
from tools.fault_drill import PY, ROOT, group_lag, group_members

GROUPS = {"windowing": (config.GROUP_WINDOWING, [config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN]),
          "forecaster": (config.GROUP_FORECASTER, [config.TOPIC_AGG]),
          "sink": (config.GROUP_SINK, [config.TOPIC_REPLAY_GAUGE, config.TOPIC_AGG, config.TOPIC_FORECASTS, config.TOPIC_ALERTS])}


def parse_rate(line: str) -> tuple[int, float] | None:
    """(sent, msg/s) from the replay producer's last line, e.g. `done: sent=1440 failed=0 in 120.7s = 12 msg/s`."""
    m = re.search(r"sent=(\d+) failed=\d+ in [0-9.]+s = ([0-9.]+) msg/s", line)
    return (int(m.group(1)), float(m.group(2))) if m else None


def drain_time(samples: list[tuple[float, int]]) -> float | None:
    """Seconds from the peak lag to the first later sample at 0; None if it never reached 0."""
    if not samples:
        return None
    peak_t = max(samples, key=lambda s: s[1])[0]
    return next((t - peak_t for t, lag in samples if t >= peak_t and lag == 0), None)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", default="08158700")
    p.add_argument("--start", default="2017-01-01")
    p.add_argument("--end", default="2026-09-30")
    p.add_argument("--drain-timeout-s", type=float, default=1800)
    p.add_argument("--no-db", action="store_true")
    a = p.parse_args(argv)
    if group_members(config.GROUP_AGENT):
        print("throughput_run: FAIL: the agent consumer is running; stop it first, each replayed alert would call the LLM")
        return 1
    samples = {k: [] for k in GROUPS}
    stop = threading.Event()

    def watch(t0):
        while not stop.wait(3):
            for k, (g, topics) in GROUPS.items():
                samples[k].append((time.monotonic() - t0, group_lag(g, topics)))

    with Task(f"Throughput run {a.site} {a.start}..{a.end}", total=2, unit=" phases") as t:
        t0 = time.monotonic()
        threading.Thread(target=watch, args=(t0,), daemon=True).start()
        r = subprocess.run([PY, "-m", "producers.replay_producer", "--site", a.site, "--start", a.start, "--end", a.end, "--no-sleep"],
                           cwd=ROOT, capture_output=True, text=True, errors="replace")
        rate = next((x for x in map(parse_rate, reversed(r.stdout.splitlines())) if x), None)
        if not rate:
            t.summary(f"FAIL: no rate from the replay producer: {(r.stderr or r.stdout).strip()[-200:]}")
            return 1
        sent, msgs = rate
        t.update(1, f"sent {sent} at {msgs:g} msg/s; waiting for the lag to drain")
        deadline = time.monotonic() + a.drain_timeout_s
        while time.monotonic() < deadline:
            time.sleep(5)
            cur = {k: v[-1][1] if v else None for k, v in samples.items()}
            t.update(1, f"lag {cur}")
            if all(v == 0 for v in cur.values()) and time.monotonic() - t0 > 20:
                break
        stop.set()
        peak = {k: max((s[1] for s in v), default=0) for k, v in samples.items()}
        drain = {k: drain_time(v) for k, v in samples.items()}
        drained = all(v is not None for v in drain.values())
        notes = (f"{a.site} {a.start}..{a.end}, single gauge, --no-sleep: sent {sent} messages; producer rate only. Peak lag {peak}; "
                 f"seconds from peak to 0 {({k: None if v is None else round(v, 1) for k, v in drain.items()})}")
        if not a.no_db:
            import psycopg2
            with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO evaluation_runs (run_label, data_source, site_no, throughput_msgs_s, notes, status, failure_reason) VALUES (%s,'replay',%s,%s,%s,%s,%s)",
                            ("throughput:replay", a.site, msgs, notes, "passed" if drained else "failed", None if drained else "lag did not reach 0"))
        t.update(2, "recorded")
        t.summary(f"{'PASS' if drained else 'FAIL'}: {msgs:g} msg/s, {notes}")
    return 0 if drained else 1


if __name__ == "__main__":
    sys.exit(main())
