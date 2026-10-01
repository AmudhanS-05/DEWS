"""Save one REAL response from each service into tests/fixtures/, so `pytest`
checks our parsers against the true layout (not just our reconstruction).

    python -m tools.fetch_sample --site 09510200 --nwps TSGA2
"""
from __future__ import annotations

import argparse
import json
import os

from common import config, usgs
from common.http import get_json

FIX = os.path.join("tests", "fixtures")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--site", default="09510200")
    p.add_argument("--nwps", default=None, help="NWPS gauge id (lid), e.g. TSGA2")
    a = p.parse_args(argv)
    os.makedirs(FIX, exist_ok=True)

    url = usgs.build_iv_url([a.site], [config.PARAM_STAGE, config.PARAM_DISCHARGE], period="PT6H")
    doc = get_json(url)
    with open(os.path.join(FIX, "live_usgs_iv.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    rs = usgs.parse_iv_json(doc)
    print(f"USGS: saved live_usgs_iv.json, parsed {len(rs)} readings, "
          f"series={sorted({(r['parameter'], r['series'], r['method']) for r in rs})}")

    if a.nwps:
        doc = get_json(config.NWPS_GAUGE_URL.format(lid=a.nwps))
        with open(os.path.join(FIX, "live_nwps_gauge.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        from common.nwps import parse_flood_categories
        print(f"NWPS: saved live_nwps_gauge.json, parsed {parse_flood_categories(doc)}")


if __name__ == "__main__":
    main()
