"""Save each candidate gauge's location and official flood stages to config/gauge_locations.csv.

The console's map reads this file. One request per gauge to the NOAA NWPS API (needs internet):

    python -m tools.gauge_locations
    python -m tools.gauge_locations --out config/gauge_locations.csv

Input: config/candidate_gauges.csv (usgs_site, nwps_lid, note). Gauges whose NWPS record has no
latitude/longitude are listed and skipped.
"""
from __future__ import annotations

import argparse
import csv
import os

from common import config, nwps
from common.http import get_json

FIELDS = ["site_no", "lid", "name", "lat", "lon", "county", "state", "action", "minor", "moderate", "major", "units"]


def parse_location(doc: dict, site_no: str | None = None) -> dict | None:
    """NWPS gauge document -> one CSV row (None if it has no coordinates)."""
    try:
        lat, lon = float(doc["latitude"]), float(doc["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    cats = nwps.parse_flood_categories(doc)
    st = doc.get("state")
    return {
        "site_no": site_no or doc.get("usgsId") or "", "lid": doc.get("lid") or "",
        "name": doc.get("name") or "", "lat": round(lat, 5), "lon": round(lon, 5),
        "county": doc.get("county") or "",
        "state": (st.get("abbreviation") if isinstance(st, dict) else st) or "",
        **{c: cats.get(c) for c in nwps.CATEGORIES}, "units": cats.get("units") or "ft",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--candidates", default="config/candidate_gauges.csv")
    p.add_argument("--out", default="config/gauge_locations.csv")
    a = p.parse_args(argv)

    with open(a.candidates, encoding="utf-8") as f:
        cands = [r for r in csv.DictReader(f) if r.get("usgs_site") and r.get("nwps_lid")]
    rows, skipped = [], []
    for c in cands:
        lid, site = c["nwps_lid"], c["usgs_site"]
        try:
            doc = get_json(config.NWPS_GAUGE_URL.format(lid=lid))
            row = parse_location(doc, site)
        except Exception as e:
            skipped.append((lid, str(e)[:60]))
            continue
        if row is None:
            skipped.append((lid, "no coordinates"))
            continue
        rows.append(row)
        print(f"  {lid:6s} {site}  {row['lat']:.4f}, {row['lon']:.4f}  minor={row['minor']}  {row['name'][:40]}")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} gauges to {a.out}; skipped {len(skipped)}: {skipped}")


if __name__ == "__main__":
    main()
