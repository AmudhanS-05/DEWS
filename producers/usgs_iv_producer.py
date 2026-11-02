"""Live producer: USGS instantaneous values -> Kafka topic gauge.usgs.

Every --every-s seconds:
  1. fetch the last 6 h of gage height for the configured gauges (overlapping on
     purpose: USGS transmits roughly hourly and revises provisional values);
  2. publish only new or revised readings (ReadingTracker), keyed by site_no,
     each stamped with fetched_at so feed staleness is measurable;
  3. publish one heartbeat per gauge (drives zero-fill windows downstream);
  4. save tracker state, so a restart does not re-publish everything.
After downtime longer than the 6 h overlap, the first poll backfills from the last
reading we published (startDT), so nothing in between is skipped.

    python -m producers.usgs_iv_producer                # gauges from config/candidate_gauges.csv
    python -m producers.usgs_iv_producer --sites 09510200 --every-s 300
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone

from common import config, usgs
from common.http import get_json
from common.kafka_io import JsonProducer
from common.timeutil import now_epoch
from producers.tracking import ReadingTracker, make_envelope, make_heartbeat

STATE_FILE = os.path.join(config.STATE_DIR, "usgs_producer_state.json")
OVERLAP_H = 6


def load_sites(arg_sites: str | None) -> list[str]:
    if arg_sites:
        return [s.strip() for s in arg_sites.split(",") if s.strip()]
    from tools.gate_check import load_candidates
    return [r["usgs_site"].strip() for r in load_candidates("config/candidate_gauges.csv")]


def load_state() -> tuple[ReadingTracker, float | None]:
    if not os.path.exists(STATE_FILE):
        return ReadingTracker(), None
    with open(STATE_FILE, encoding="utf-8") as f:
        s = json.load(f)
    return ReadingTracker.loads(s["tracker"]), s.get("last_poll_ts")


def save_state(tracker: ReadingTracker, last_poll_ts: float):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tracker": tracker.dumps(), "last_poll_ts": last_poll_ts}, f)
    os.replace(tmp, STATE_FILE)   # atomic: a crash never leaves a half-written state file


def fetch_url(sites: list[str], last_poll_ts: float | None, now_ts: float) -> str:
    if last_poll_ts is not None and now_ts - last_poll_ts > OVERLAP_H * 3600:
        start = datetime.fromtimestamp(last_poll_ts - 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M+0000")
        return usgs.build_iv_url(sites, start=start)          # backfill after downtime
    return usgs.build_iv_url(sites, period=f"PT{OVERLAP_H}H")


def poll_once(sites, tracker, producer, last_poll_ts) -> tuple[dict, float]:
    now_ts = now_epoch()
    stats = {"new": 0, "revision": 0, "duplicate": 0, "error": ""}
    try:
        doc = get_json(fetch_url(sites, last_poll_ts, now_ts))
        fetched_at = now_epoch()
        for r in usgs.parse_iv_json(doc):
            kind = tracker.classify(r)
            stats[kind] += 1
            if kind != "duplicate":
                producer.send(config.TOPIC_GAUGE, r["site_no"],
                              make_envelope(r, fetched_at, "LIVE", revision=(kind == "revision")))
    except ValueError as e:                 # layout changed: keep evidence in the dead-letter topic
        stats["error"] = f"parse: {e}"
        producer.send(config.TOPIC_DLQ, "usgs_iv", {"error": str(e), "at": now_ts})
    except Exception as e:                  # network / HTTP: next poll retries; heartbeats still go out
        stats["error"] = f"fetch: {str(e)[:200]}"
    for s in sites:
        producer.send(config.TOPIC_GAUGE, s, make_heartbeat(s, now_epoch()))
    producer.flush(10)
    tracker.prune(now_ts)
    return stats, now_ts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sites", default=None, help="comma-separated USGS site numbers")
    p.add_argument("--every-s", type=float, default=300.0)
    p.add_argument("--once", action="store_true", help="poll once and exit (for testing)")
    a = p.parse_args(argv)

    sites = load_sites(a.sites)
    tracker, last_poll_ts = load_state()
    producer = JsonProducer("usgs-iv-producer")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    print(f"polling {sites} every {a.every_s}s -> {config.TOPIC_GAUGE} @ {config.KAFKA_BOOTSTRAP}")
    while not stop["flag"]:
        stats, polled = poll_once(sites, tracker, producer, last_poll_ts)
        if not stats["error"].startswith("fetch"):
            last_poll_ts = polled
            save_state(tracker, last_poll_ts)
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {stats} "
              f"sent={producer.sent} failed={producer.failed}", flush=True)
        if a.once:
            break
        for _ in range(int(a.every_s)):
            if stop["flag"]:
                break
            time.sleep(1)
    producer.flush(30)


if __name__ == "__main__":
    main()
