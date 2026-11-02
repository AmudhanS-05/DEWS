"""Phase 0 gate check: is there a gauge we can actually build and evaluate on?

For each candidate gauge in config/candidate_gauges.csv:
  1. fetch its official NWPS flood categories (the labels);
  2. download N years of USGS gage-height history (cached under data/raw/usgs/,
     which the replay producer reuses later);
  3. count events that crossed the chosen category and measure each event's
     rise time (see common/hydro.py for the definitions);
  4. print PASS / FAIL against the kill rules and write CSVs to gate_output/.

Run on a machine that can reach waterservices.usgs.gov and api.water.noaa.gov:
    python -m tools.gate_check --years 10 --category minor
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date
from statistics import median

from common import config, hydro, nwps, usgs
from common.http import get_json
from common.timeutil import epoch_to_iso


def load_candidates(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("usgs_site") and not r["usgs_site"].startswith("#")]
    if not rows:
        sys.exit(f"No candidate gauges in {path}. Add rows: usgs_site,nwps_lid,note")
    return rows


def fetch_year(site: str, year: int, refresh: bool) -> dict:
    raw_dir = os.path.join(config.DATA_DIR, "raw", "usgs")
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"{site}_{config.PARAM_STAGE}_{year}.json")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    end = f"{year}-12-31" if year < date.today().year else date.today().isoformat()
    url = usgs.build_iv_url([site], [config.PARAM_STAGE], start=f"{year}-01-01", end=end)
    doc = get_json(url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return doc


def primary_series(readings: list[dict]) -> tuple[str | None, int]:
    counts = Counter(r["series"] for r in readings)
    if not counts:
        return None, 0
    return counts.most_common(1)[0][0], len(counts)


def check_gauge(row: dict, years: int, category: str, min_events: int, min_rise_h: float,
                refresh: bool) -> tuple[dict, list[dict]]:
    site = row["usgs_site"].strip()
    lid = (row.get("nwps_lid") or "").strip()
    summary = {"usgs_site": site, "nwps_lid": lid, "name": "", "category_used": "",
               "threshold_ft": "", "years_requested": years, "first_reading": "", "last_reading": "",
               "n_readings": 0, "n_series": 0, "median_interval_min": "", "largest_gap_h": "",
               "n_events": 0, "median_rise_h": "", "min_rise_h": "", "verdict": "FAIL", "reasons": ""}
    reasons = []

    # 1. labels
    cats = {}
    if not lid:
        reasons.append("no NWPS lid given")
    else:
        try:
            cats = nwps.parse_flood_categories(get_json(config.NWPS_GAUGE_URL.format(lid=lid)))
            summary["name"] = cats.get("name") or ""
        except Exception as e:
            reasons.append(f"NWPS fetch/parse failed: {e}")
    threshold = cats.get(category)
    used = category
    if threshold is None and cats.get("action") is not None and category != "action":
        threshold, used = cats["action"], "action"
        reasons.append(f"no {category} stage; fell back to action stage")
    if threshold is None:
        reasons.append("no usable flood category threshold")
    summary["category_used"], summary["threshold_ft"] = used, threshold if threshold is not None else ""

    # 2. history
    readings = []
    fetch_errors = 0
    this_year = date.today().year
    for y in range(this_year - years + 1, this_year + 1):
        try:
            readings += usgs.parse_iv_json(fetch_year(site, y, refresh))
        except Exception as e:
            fetch_errors += 1
            reasons.append(f"{y}: {str(e)[:160]}")
    series_id, n_series = primary_series(readings)
    summary["n_series"] = n_series
    series = sorted((r["reading_ts"], r["value"]) for r in readings if r["series"] == series_id)
    summary["n_readings"] = len(series)
    if series:
        summary["first_reading"] = epoch_to_iso(series[0][0])
        summary["last_reading"] = epoch_to_iso(series[-1][0])
        mi, lg = hydro.median_interval_min(series), hydro.largest_gap_h(series)
        summary["median_interval_min"] = round(mi, 1) if mi is not None else ""
        summary["largest_gap_h"] = round(lg, 1) if lg is not None else ""
    else:
        reasons.append("no gage-height history returned")

    # 3. events
    events = []
    if series and threshold is not None:
        for i in hydro.find_crossings(series, threshold):
            j = hydro.rise_start(series, i)
            pk_i, pk_v = hydro.event_peak(series, i)
            events.append({"usgs_site": site, "crossing_time": epoch_to_iso(series[i][0]),
                           "rise_start_time": epoch_to_iso(series[j][0]),
                           "rise_start_stage": series[j][1], "crossing_stage": series[i][1],
                           "rise_time_h": round((series[i][0] - series[j][0]) / 3600, 2),
                           "peak_time": epoch_to_iso(series[pk_i][0]), "peak_stage": pk_v})
    summary["n_events"] = len(events)
    if events:
        rises = [e["rise_time_h"] for e in events]
        summary["median_rise_h"] = round(median(rises), 2)
        summary["min_rise_h"] = round(min(rises), 2)

    # 4. kill rules
    ok = True
    if len(events) < min_events:
        ok = False
        reasons.append(f"only {len(events)} events (< {min_events})")
    if events and median(e["rise_time_h"] for e in events) < min_rise_h:
        ok = False
        reasons.append(f"median rise time < {min_rise_h} h")
    summary["verdict"] = "PASS" if ok and threshold is not None else "FAIL"
    if not series and fetch_errors:
        summary["verdict"] = "ERROR"  # could not get data: not a verdict on the gauge
    summary["reasons"] = "; ".join(reasons)
    return summary, events


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="config/candidate_gauges.csv")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--category", default="minor", choices=nwps.CATEGORIES)
    p.add_argument("--min-events", type=int, default=10)
    p.add_argument("--min-rise-h", type=float, default=3.0,
                   help="kill rule: median rise time must be at least this (hours)")
    p.add_argument("--refresh", action="store_true", help="re-download cached years")
    a = p.parse_args(argv)

    os.makedirs(config.GATE_OUTPUT_DIR, exist_ok=True)
    summaries, all_events = [], []
    for row in load_candidates(a.candidates):
        print(f"checking {row['usgs_site']} ({row.get('nwps_lid', '')}) ...", flush=True)
        s, ev = check_gauge(row, a.years, a.category, a.min_events, a.min_rise_h, a.refresh)
        summaries.append(s)
        all_events += ev
        print(f"  {s['verdict']}: events={s['n_events']} median_rise_h={s['median_rise_h']} "
              f"interval_min={s['median_interval_min']} series={s['n_series']}  {s['reasons']}")

    sp = os.path.join(config.GATE_OUTPUT_DIR, "gate_summary.csv")
    with open(sp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0].keys()))
        w.writeheader()
        w.writerows(summaries)
    ep = os.path.join(config.GATE_OUTPUT_DIR, "gate_events.csv")
    with open(ep, "w", newline="", encoding="utf-8") as f:
        fields = ["usgs_site", "crossing_time", "rise_start_time", "rise_start_stage", "crossing_stage",
                  "rise_time_h", "peak_time", "peak_stage"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_events)
    passed = [s for s in summaries if s["verdict"] == "PASS"]
    print(f"\nwrote {sp} and {ep}. {len(passed)}/{len(summaries)} gauges PASS.")
    errored = [s for s in summaries if s["verdict"] == "ERROR"]
    if errored:
        print(f"{len(errored)} gauge(s) ERROR: data could not be fetched (network, proxy or bad id). "
              "That is not a verdict; fix and rerun.")
    # Pooled view: the evaluation can use events from several gauges in one region,
    # so a gauge set can be usable even when no single gauge has enough floods.
    slow = [e for e in all_events if e["rise_time_h"] >= a.min_rise_h]
    gauges_with_slow = sorted({e["usgs_site"] for e in slow})
    pooled_ok = len(slow) >= a.min_events
    print(f"POOLED: {len(slow)} events with rise time >= {a.min_rise_h} h across {len(gauges_with_slow)} gauges "
          f"({len(all_events)} events in total) -> {'PASS' if pooled_ok else 'FAIL'} "
          f"(rule: >= {a.min_events} pooled events)")
    if not passed and not errored and not pooled_ok:
        print("KILL RULE: neither any single gauge nor the pooled set passes. Revisit the gauge set or the hazard.")
    elif not passed and pooled_ok:
        print("No single gauge passes, but the pooled set does: evaluate across these gauges together.")
    return 0 if (passed or pooled_ok) else (3 if errored else 2)


if __name__ == "__main__":
    sys.exit(main())
