"""I1.1 / INDIA_STUDY step A: data audit, US side (same function will take India-WRIS series later).

Per station, from a list of readings [{reading_ts, value, series, site_no}]:
  interval_min (median), archive_days, missing_pct (vs the median interval), longest_gap_h,
  events (crossings of `threshold`), median_rise_h, rise_resolvable (interval shorter than the median rise).
Publication delay and access method are properties of the source, not of the series: they are set by hand in
`SOURCE_FACTS` (US values are measured by tools/staleness_logger.py; fill them in there, none are assumed here).

    python -m india.audit --gauges config/gauge_locations.csv --out india/figures/audit_us.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import median

from common import config, hydro
from ml.dataset import primary_series

COLUMNS = ["site_no", "n_readings", "interval_min", "archive_days", "missing_pct", "longest_gap_h", "events",
           "median_rise_h", "rise_resolvable"]
# Fill from measurement, per source. None = not measured yet.
SOURCE_FACTS = {"USGS-IV": {"access": "API", "publication_delay_h": None, "thresholds_machine_readable": True}}


def audit_station(readings: list[dict], threshold: float | None) -> dict:
    rs = primary_series(readings) if readings else []
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    out = {"site_no": str(rs[0]["site_no"]) if rs else "", "n_readings": len(series), "interval_min": None,
           "archive_days": 0.0, "missing_pct": None, "longest_gap_h": None, "events": None,
           "median_rise_h": None, "rise_resolvable": None}
    if len(series) < 2:
        return out
    interval = hydro.median_interval_min(series)
    span_s = series[-1][0] - series[0][0]
    expected = span_s / 60.0 / interval + 1 if interval else None
    out.update(interval_min=interval, archive_days=span_s / 86400.0, longest_gap_h=hydro.largest_gap_h(series),
               missing_pct=max(0.0, 100.0 * (1 - len(series) / expected)) if expected else None)
    if threshold is not None:
        idx = hydro.find_crossings(series, threshold)
        out["events"] = len(idx)
        if idx:
            rises = [hydro.rise_time_h(series, i) for i in idx]
            out["median_rise_h"] = median(rises)
            out["rise_resolvable"] = bool(interval and interval / 60.0 < out["median_rise_h"])
    return out


def write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(rows)


def main(argv=None):
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only", help="comma list of NWPS ids or site numbers")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--out", default="india/figures/audit_us.csv")
    p.add_argument("--data-dir", help="folder holding raw/usgs/ (default: DATA_DIR env or ./data)")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer = Timer()
    print(f"india.audit: auditing cached USGS series {a.start}..{a.end} from {config.DATA_DIR}/raw/usgs -> {a.out}")
    rows = []
    for g in bar(iter_gauges(a.gauges, a.gauges_only, a.start, a.end, config.DATA_DIR), "stations",
                 total=count_gauges(a.gauges, a.gauges_only)):
        r = audit_station(g["readings"], g["cats"]["minor"])
        rows.append(r)
        print(f"  {r['site_no']} {g['lid']}: interval {r['interval_min']} min, {r['archive_days']:.0f} days, "
              f"missing {r['missing_pct']:.1f}%, longest gap {r['longest_gap_h']} h, events {r['events']}")
    if not rows:
        raise SystemExit(f"ERROR: 0 gauges audited, nothing written. Reasons are listed above (skip lines); "
                         f"data dir = {config.DATA_DIR}/raw/usgs, gauge list = {a.gauges}")
    write_csv(rows, a.out)
    print(f"done: {len(rows)} stations -> {a.out} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
