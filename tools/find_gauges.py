"""Find candidate gauges for the gate check: NWS gauges that (a) are linked to a
USGS site and (b) have official flood categories.

Two modes:

  # 1. Look up the NWPS id (lid) of a USGS gauge you already know
  python -m tools.find_gauges --near-usgs 09510200

  # 2. Search a region and write the best 10 into config/candidate_gauges.csv
  python -m tools.find_gauges --bbox=-100.5,29.4,-97.4,30.9 --top 10 --write

bbox = min_lon,min_lat,max_lon,max_lat (write it as --bbox=..., because it starts with a minus sign). Example regions (flash-flood prone):
  Texas Hill Country  -100.5,29.4,-97.4,30.9
  Central Arizona     -112.6,33.2,-110.8,34.3
  West Virginia/KY    -83.5,37.0,-80.0,39.0

NWPS API: GET /nwps/v1/gauges?bbox.xmin=..&bbox.ymin=..&bbox.xmax=..&bbox.ymax=..&srid=EPSG_4326
lists gauges; GET /nwps/v1/gauges/{lid} gives metadata incl. usgsId and
flood.categories.{action,minor,moderate,major}.stage (from the API's swagger spec).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from urllib.parse import urlencode

from common import config, nwps, usgs
from common.http import get_json

NWPS_BASE = "https://api.water.noaa.gov/nwps/v1/gauges"


def list_gauges(xmin, ymin, xmax, ymax) -> list[dict]:
    q = urlencode({"bbox.xmin": xmin, "bbox.ymin": ymin, "bbox.xmax": xmax, "bbox.ymax": ymax,
                   "srid": "EPSG_4326"})
    doc = get_json(f"{NWPS_BASE}?{q}")
    if isinstance(doc, list):
        return doc
    for key in ("gauges", "data", "items"):
        if isinstance(doc.get(key), list):
            return doc[key]
    raise ValueError(f"unrecognised gauge-list response, keys: {list(doc)[:10]}")


def gauge_meta(lid: str) -> dict:
    doc = get_json(f"{NWPS_BASE}/{lid}")
    cats = nwps.parse_flood_categories(doc)
    state = doc.get("state") or {}
    return {
        "lid": doc.get("lid") or lid,
        "usgs_id": (doc.get("usgsId") or "").strip(),
        "name": doc.get("name") or "",
        "state": state.get("abbreviation", "") if isinstance(state, dict) else str(state),
        "in_service": doc.get("inService"),
        **{c: cats.get(c) for c in nwps.CATEGORIES},
        "units": cats.get("units"),
    }


def usable(m: dict) -> bool:
    return bool(m["usgs_id"]) and m["minor"] is not None and m["action"] is not None


def score(m: dict) -> int:
    """More official categories = better labels."""
    return sum(m[c] is not None for c in nwps.CATEGORIES)


def site_latlon(site: str) -> tuple[float, float]:
    doc = get_json(usgs.build_iv_url([site], period="PT2H"))
    g = doc["value"]["timeSeries"][0]["sourceInfo"]["geoLocation"]["geogLocation"]
    return float(g["latitude"]), float(g["longitude"])


def scan(xmin, ymin, xmax, ymax, max_lookups: int) -> list[dict]:
    items = list_gauges(xmin, ymin, xmax, ymax)
    print(f"{len(items)} NWS gauges in the box; checking up to {max_lookups} for USGS link + flood categories")
    out = []
    for it in items[:max_lookups]:
        lid = it.get("lid") if isinstance(it, dict) else None
        if not lid:
            continue
        try:
            m = gauge_meta(lid)
        except Exception as e:
            print(f"  {lid}: skipped ({str(e)[:80]})")
            continue
        out.append(m)
        flag = "OK " if usable(m) else "-- "
        print(f"  {flag}{lid:7s} usgs={m['usgs_id'] or '-':9s} minor={m['minor']} {m['name'][:50]}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--near-usgs", help="USGS site number: find its NWPS lid")
    g.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    p.add_argument("--max-lookups", type=int, default=60)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--write", action="store_true", help="overwrite config/candidate_gauges.csv with the top gauges")
    a = p.parse_args(argv)

    if a.near_usgs:
        lat, lon = site_latlon(a.near_usgs)
        found = scan(lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15, a.max_lookups)
        hit = [m for m in found if m["usgs_id"] == a.near_usgs]
        if hit:
            m = hit[0]
            print(f"\nUSGS {a.near_usgs} = NWPS lid {m['lid']} ({m['name']}); categories "
                  f"action={m['action']} minor={m['minor']} moderate={m['moderate']} major={m['major']} {m['units']}")
            print(f"Next: python -m tools.fetch_sample --site {a.near_usgs} --nwps {m['lid']}")
            return 0
        print(f"\nNo NWS forecast gauge is linked to USGS {a.near_usgs}. It cannot be labelled with official "
              "flood categories, so pick a different gauge (use --bbox).")
        return 1

    xmin, ymin, xmax, ymax = (float(v) for v in a.bbox.split(","))
    found = [m for m in scan(xmin, ymin, xmax, ymax, a.max_lookups) if usable(m)]
    found.sort(key=lambda m: (-score(m), m["lid"]))
    os.makedirs(config.GATE_OUTPUT_DIR, exist_ok=True)
    allp = os.path.join(config.GATE_OUTPUT_DIR, "gauges_found.csv")
    if found:
        with open(allp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(found[0].keys()))
            w.writeheader()
            w.writerows(found)
    print(f"\n{len(found)} usable gauges (USGS-linked, action + minor stages). All listed in {allp}")
    top = found[:a.top]
    for m in top:
        print(f"  {m['usgs_id']},{m['lid']}  {m['name']} ({m['state']})")
    if a.write and top:
        path = "config/candidate_gauges.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("usgs_site,nwps_lid,note\n")
            for m in top:
                note = f"{m['name']} ({m['state']}); minor {m['minor']} {m['units'] or ''}".replace(",", " ")
                f.write(f"{m['usgs_id']},{m['lid']},{note}\n")
        print(f"wrote {len(top)} gauges to {path}. Next: python -m tools.gate_check --years 10")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
