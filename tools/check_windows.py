"""Check aggregates.window for duplicate window emissions (Phase 3 test, e.g. after killing a Faust worker).

    python -m tools.check_windows                    # everything on the topic
    python -m tools.check_windows --mode SIMULATION --site 08158700

A window may appear again only as a flagged revision (late or revised data). The same window
emitted twice as an ordinary (non-revision) row means a closed window was emitted again after a
restart: that is a failure. Also prints windows per gauge, zero-filled windows (n_readings = 0)
and gaps between consecutive windows.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

from common import config


def summarise(msgs: list[dict]) -> dict:
    firsts: Counter = Counter()
    revisions = zero = 0
    starts = defaultdict(set)
    for m in msgs:
        key = (m["site_no"], m.get("series"), m.get("mode"), m["window_start_ts"])
        if m.get("revision"):
            revisions += 1
        else:
            firsts[key] += 1
        if m.get("n_readings") == 0:
            zero += 1
        starts[(m["site_no"], m.get("series"), m.get("mode"))].add(m["window_start_ts"])
    dupes = {k: n for k, n in firsts.items() if n > 1}
    gaps = 0
    w = config.WINDOW_SECONDS
    for ss in starts.values():
        s = sorted(ss)
        gaps += sum(1 for a, b in zip(s, s[1:]) if b - a > w)
    return {"messages": len(msgs), "windows": len(firsts), "duplicates": dupes, "revisions": revisions,
            "zero_filled": zero, "gaps": gaps, "series": len(starts)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["LIVE", "SIMULATION"])
    p.add_argument("--site")
    p.add_argument("--timeout-s", type=float, default=10, help="stop after this many seconds without a message")
    a = p.parse_args(argv)
    from confluent_kafka import Consumer, TopicPartition
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": "check-windows-throwaway",
                  "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    parts = [TopicPartition(config.TOPIC_AGG, i, 0) for i in range(config.TOPIC_PARTITIONS)]
    c.assign(parts)
    msgs = []
    try:
        while True:
            m = c.poll(a.timeout_s)
            if m is None:
                break
            if m.error():
                continue
            d = json.loads(m.value())
            if (a.mode and d.get("mode") != a.mode) or (a.site and d.get("site_no") != a.site):
                continue
            msgs.append(d)
    finally:
        c.close()
    r = summarise(msgs)
    print(f"{r['messages']} messages · {r['windows']} distinct windows · {r['series']} gauge series · "
          f"{r['revisions']} revisions · {r['zero_filled']} zero-filled · {r['gaps']} gaps")
    if r["duplicates"]:
        print(f"FAIL: {len(r['duplicates'])} windows emitted more than once as ordinary rows, e.g.")
        for k, n in list(r["duplicates"].items())[:5]:
            print("  ", k, "x", n)
    else:
        print("PASS: no window emitted twice (revisions are flagged)")


if __name__ == "__main__":
    main()
