import json
import os

from tools import gauge_locations

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "live_nwps_gauge.json")


def test_parse_location_from_real_nwps_fixture():
    with open(FIX, encoding="utf-8") as f:
        row = gauge_locations.parse_location(json.load(f), "08158700")
    assert row["lid"] == "DRWT2" and row["site_no"] == "08158700" and row["state"] == "TX"
    assert abs(row["lat"] - 30.08278) < 1e-4 and abs(row["lon"] + 98.0075) < 1e-4
    assert row["minor"] == 5.5 and row["major"] == 13.0 and row["units"]


def test_no_coordinates_is_skipped():
    assert gauge_locations.parse_location({"lid": "X"}) is None
