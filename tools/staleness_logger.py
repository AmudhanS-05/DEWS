"""Phase 0 check 4: how stale is the live USGS feed, really?

Polls the IV service every few minutes for the candidate gauges and logs, per
gauge and series, the newest reading time against the wall clock. Leave it
running for several days (ideally through some rain) and look at
gate_output/staleness_log.csv: staleness_min is the delay the forecaster will
always inherit.

    python -m tools.staleness_logger --every-min 5 --hours 72
"""
from __future__ import annotations

import argparse
import csv
import os
import time

from common import config, usgs
from common.http import get_json
from common.timeutil import epoch_to_iso, now_epoch
from tools.gate_check import load_candidates


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="config/candidate_gauges.csv")
    p.add_argument("--every-min", type=float, default=5.0)
    p.add_argument("--hours", type=float, default=72.0)
    a = p.parse_args(argv)

    sites = [r["usgs_site"].strip() for r in load_candidates(a.candidates)]
    os.makedirs(config.GATE_OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.GATE_OUTPUT_DIR, "staleness_log.csv")
    new_file = not os.path.exists(path)
    seen_newest: dict[str, float] = {}
    stop_at = now_epoch() + a.hours * 3600
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["polled_at", "site_no", "series", "newest_reading_time", "staleness_min",
                        "new_readings_since_last_poll", "error"])
        while now_epoch() < stop_at:
            polled = now_epoch()
            try:
                readings = usgs.parse_iv_json(get_json(usgs.build_iv_url(sites, period="PT6H")))
                by_key: dict[str, list[float]] = {}
                for r in readings:
                    by_key.setdefault(f"{r['site_no']}|{r['series']}", []).append(r["reading_ts"])
                for key, tss in sorted(by_key.items()):
                    newest = max(tss)
                    prev = seen_newest.get(key)
                    n_new = len([t for t in tss if prev is None or t > prev])
                    seen_newest[key] = newest
                    site, series = key.split("|")
                    w.writerow([epoch_to_iso(polled), site, series, epoch_to_iso(newest),
                                round((polled - newest) / 60.0, 1), n_new, ""])
            except Exception as e:
                w.writerow([epoch_to_iso(polled), "", "", "", "", "", str(e)[:300]])
            f.flush()
            print(f"{epoch_to_iso(polled)} logged", flush=True)
            time.sleep(a.every_min * 60)


if __name__ == "__main__":
    main()
