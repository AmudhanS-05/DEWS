"""Replay producer: historical USGS readings -> replay.gauge, marked SIMULATION.

Reads the yearly files the gate check cached under data/raw/usgs/ and publishes
them in event-time order. Used for the demo (a past flood, shown as it unfolded),
for evaluation, and for the throughput benchmark.

    # replay a past flood, 1 simulated hour per real second
    python -m producers.replay_producer --site 09510200 --start 2021-07-20 --end 2021-07-25 --speed 3600
    # throughput benchmark: as fast as possible, prints messages/second
    python -m producers.replay_producer --site 09510200 --start 2020-01-01 --end 2025-12-31 --no-sleep

Replay has its own topic (replay.gauge), so it never mixes with live gauge.usgs data.
Every record also carries mode = SIMULATION; downstream alerts inherit it, so a replayed
alert can never be mistaken for a live one.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time

from common import config, usgs
from common.kafka_io import JsonProducer
from common.timeutil import now_epoch, to_epoch
from producers.tracking import make_envelope


def load_readings(site: str, start: str, end: str) -> list[dict]:
    t0, t1 = to_epoch(start + "T00:00:00+00:00"), to_epoch(end + "T23:59:59+00:00")
    files = sorted(glob.glob(os.path.join(config.DATA_DIR, "raw", "usgs", f"{site}_{config.PARAM_STAGE}_*.json")))
    if not files:
        raise SystemExit(f"no cached data for {site}; run `python -m tools.gate_check` first")
    out = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            out += [r for r in usgs.parse_iv_json(json.load(f)) if t0 <= r["reading_ts"] <= t1]
    out.sort(key=lambda r: (r["reading_ts"], r["series"]))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--speed", type=float, default=3600.0, help="simulated seconds per real second")
    p.add_argument("--no-sleep", action="store_true", help="publish as fast as possible (throughput test)")
    a = p.parse_args(argv)

    rs = load_readings(a.site, a.start, a.end)
    print(f"replaying {len(rs)} readings for {a.site} ({a.start}..{a.end}) -> {config.TOPIC_REPLAY_GAUGE}")
    prod = JsonProducer("replay-producer")
    wall0, prev_ts = time.perf_counter(), None
    for r in rs:
        if not a.no_sleep and prev_ts is not None and r["reading_ts"] > prev_ts:
            time.sleep((r["reading_ts"] - prev_ts) / a.speed)
        prev_ts = r["reading_ts"]
        prod.send(config.TOPIC_REPLAY_GAUGE, r["site_no"], make_envelope(r, now_epoch(), "SIMULATION"))
    prod.flush(60)
    dt = time.perf_counter() - wall0
    print(f"done: sent={prod.sent} failed={prod.failed} in {dt:.1f}s = {prod.sent / dt if dt else 0:.0f} msg/s")


if __name__ == "__main__":
    main()
