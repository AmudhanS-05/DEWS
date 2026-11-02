import json
import os

import pytest

from common import nwps, usgs
from common.timeutil import to_epoch

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def test_build_iv_url_period_and_dates():
    u = usgs.build_iv_url(["09510200", "07332500"], period="PT3H")
    assert "sites=09510200,07332500" in u and "period=PT3H" in u and "format=json" in u
    u2 = usgs.build_iv_url(["09510200"], start="2020-01-01", end="2020-12-31")
    assert "startDT=2020-01-01" in u2 and "endDT=2020-12-31" in u2
    with pytest.raises(ValueError):
        usgs.build_iv_url(["x"], period="PT1H", start="2020-01-01")


def test_parse_iv_reconstructed():
    rs = usgs.parse_iv_json(load("usgs_iv_reconstructed.json"))
    blue = [r for r in rs if r["site_no"] == "07332500"]
    syc = [r for r in rs if r["site_no"] == "09510200"]
    assert len(blue) == 3 and all(r["value"] == 5.19 for r in blue)
    # noDataValue point dropped; two series kept apart
    assert len(syc) == 5
    assert {r["series"] for r in syc} == {"11", "12"}
    assert all(usgs.is_provisional(r) for r in rs)
    # same timestamp, different series -> different dedupe keys
    keys = {usgs.reading_key(r) for r in syc}
    assert len(keys) == 5


def test_time_offsets_normalised_to_utc():
    # 00:00 at -07:00 and 02:00 at -05:00 are both 07:00 UTC
    assert to_epoch("2026-09-30T00:00:00.000-07:00") == to_epoch("2026-09-30T02:00:00.000-05:00")


def test_parse_iv_rejects_unknown_layout():
    with pytest.raises(ValueError):
        usgs.parse_iv_json({"something": "else"})


def test_parse_nwps_reconstructed():
    c = nwps.parse_flood_categories(load("nwps_gauge_reconstructed.json"))
    assert (c["action"], c["minor"], c["moderate"], c["major"]) == (16.0, 16.8, 17.5, 18.5)
    assert c["units"] == "ft" and c["lid"] == "TSGA2"
    assert nwps.category_of(16.9, c) == "minor"
    assert nwps.category_of(15.0, c) is None
    assert nwps.category_of(20.0, c) == "major"


@pytest.mark.skipif(not os.path.exists(os.path.join(FIX, "live_usgs_iv.json")),
                    reason="run `python -m tools.fetch_sample` to save a real USGS response")
def test_parse_iv_live_response():
    rs = usgs.parse_iv_json(load("live_usgs_iv.json"))
    assert rs, "parser returned nothing from a real response: layout mismatch?"
    for r in rs:
        assert r["site_no"] and r["parameter"] and r["reading_time"]


@pytest.mark.skipif(not os.path.exists(os.path.join(FIX, "live_nwps_gauge.json")),
                    reason="run `python -m tools.fetch_sample --nwps <LID>` to save a real NWPS response")
def test_parse_nwps_live_response():
    c = nwps.parse_flood_categories(load("live_nwps_gauge.json"))
    assert any(c[k] is not None for k in nwps.CATEGORIES), \
        "no flood category found in a real NWPS response: layout mismatch?"
