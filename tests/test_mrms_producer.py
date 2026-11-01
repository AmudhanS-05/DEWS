"""C2.1 rain producer: decode + circle on a tiny synthetic GRIB2 grid, record schema, replay pacing, dedupe and
idempotent keys, live poll with a fake bucket and a fake Kafka producer."""
import gzip
import math
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from common import config, mrms
from producers import mrms_qpe_producer as p

# a small grid laid out like MRMS (0.01 deg, rows north -> south, 0..360 longitudes)
GEOM = {"ni": 400, "nj": 400, "lat_first": 31.995, "lon_first": 360 - 99.995, "dlat": 0.01, "dlon": 0.01}
GAUGE = {"site_no": "08158000", "lid": "ACRT2", "lat": 30.24611, "lon": -97.68}
H = datetime(2024, 6, 1, 6, tzinfo=timezone.utc).timestamp()


class FakeProducer:
    def __init__(self):
        self.msgs, self.sent, self.failed = [], 0, 0

    def send(self, topic, key, value):
        self.msgs.append((topic, key, value))
        self.sent += 1

    def flush(self, timeout=0):
        return 0


def synthetic_grib(values: np.ndarray, ni: int, nj: int, lat_first: float, lon_first: float) -> bytes:
    eccodes = pytest.importorskip("eccodes")
    h = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    for k, v in [("Ni", ni), ("Nj", nj), ("latitudeOfFirstGridPointInDegrees", lat_first),
                 ("longitudeOfFirstGridPointInDegrees", lon_first),
                 ("latitudeOfLastGridPointInDegrees", round(lat_first - (nj - 1) * 0.01, 3)),
                 ("longitudeOfLastGridPointInDegrees", round(lon_first + (ni - 1) * 0.01, 3)),
                 ("iDirectionIncrementInDegrees", 0.01), ("jDirectionIncrementInDegrees", 0.01),
                 ("jScansPositively", 0), ("iScansNegatively", 0), ("bitmapPresent", 0)]:
        eccodes.codes_set(h, k, v)
    eccodes.codes_set_values(h, values.astype(float))
    msg = eccodes.codes_get_message(h)
    eccodes.codes_release(h)
    return gzip.compress(msg)


def tiny_file(rain_inside: float = 4.0):
    """60 x 60 cells around ACRT2: rain_inside mm within 5 km, 1 mm elsewhere, a no-coverage (-3) band inside 25 km only."""
    ni = nj = 60
    lat_first, lon_first = round(GAUGE["lat"] + 0.295, 3), round(360 + GAUGE["lon"] - 0.295, 3)
    geom = {"ni": ni, "nj": nj, "lat_first": lat_first, "lon_first": lon_first, "dlat": 0.01, "dlon": 0.01}
    v = np.ones(ni * nj)
    v[mrms.circle_indices(GAUGE["lat"], GAUGE["lon"], 5.0, geom)] = rain_inside
    v[ni * 10:ni * 12] = -3.0                           # two rows ~19-21 km north without radar coverage
    return synthetic_grib(v, ni, nj, lat_first, lon_first), geom, v


# ---------- common.mrms ----------

def test_key_and_product_names():
    assert mrms.hour_key(H) == ("CONUS/MultiSensor_QPE_01H_Pass2_00.00/20240601/"
                                "MRMS_MultiSensor_QPE_01H_Pass2_00.00_20240601-060000.grib2.gz")
    assert mrms.key_time(mrms.hour_key(H, mrms.product_name("radaronly"))) == H
    assert mrms.product_name("pass1") == "MultiSensor_QPE_01H_Pass1_00.00"
    assert mrms.product_name("Foo_00.00") == "Foo_00.00"


def test_circle_cell_count_matches_area():
    idx = mrms.circle_indices(30.2, -97.8, 10.0, GEOM)
    cell_km2 = (0.01 * 111.19) * (0.01 * 111.19 * math.cos(math.radians(30.2)))
    expected = math.pi * 100 / cell_km2
    assert abs(len(idx) - expected) / expected < 0.03


def test_circle_mean_excludes_no_coverage():
    mean, cov = mrms.circle_mean(np.array([2.0, 4.0, -3.0, 9999.0, 0.0]), np.arange(5))
    assert mean == 2.0 and cov == 0.6
    mean, cov = mrms.circle_mean(np.array([-3.0, -3.0]), np.arange(2))
    assert math.isnan(mean) and cov == 0.0


def test_decode_tiny_grib_and_circle_means():
    body, geom, v = tiny_file(4.0)
    g, vals, missing = mrms.decode(body)
    assert g == geom and missing == 9999.0
    assert np.allclose(vals, v)
    out = mrms.gauge_means(vals, g, missing, [GAUGE], [5.0, 10.0, 25.0])[GAUGE["site_no"]]
    assert out[5.0][0] == pytest.approx(4.0) and out[5.0][1] == 1.0
    n5, n10 = out[5.0][2], out[10.0][2]
    assert out[10.0][0] == pytest.approx((4.0 * n5 + 1.0 * (n10 - n5)) / n10, rel=1e-6)
    assert out[10.0][1] == 1.0 and 0.9 < out[25.0][1] < 1.0   # the -3 band only cuts the 25 km circle
    assert 0 < out[25.0][0] < out[10.0][0]


def test_parse_listing():
    xml = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>true</IsTruncated>'
           '<Contents><Key>CONUS/X/20261031/MRMS_X_20261031-170000.grib2.gz</Key>'
           '<LastModified>2026-10-31T17:02:55.000Z</LastModified></Contents>'
           '<NextContinuationToken>abc</NextContinuationToken></ListBucketResult>')
    items, tok = mrms.parse_listing(xml)
    assert tok == "abc" and items[0][1].startswith("2026-10-31T17:02")
    assert mrms.key_time(items[0][0]) == datetime(2026, 10, 31, 17, tzinfo=timezone.utc).timestamp()


# ---------- record schema ----------

def test_record_schema_and_null_for_no_coverage():
    means = {5.0: (4.0, 1.0, 74), 10.0: (2.5, 1.0, 294), 25.0: (float("nan"), 0.0, 1840)}
    r = p.make_rain_record("08158000", H, mrms.PRODUCT, means, "SIMULATION", H + 4000, H + 3420, H + 4001)
    for k in ("site_no", "hour_start", "hour_start_ts", "hour_end", "hour_end_ts", "rain_mm_5km", "rain_mm_10km",
              "rain_mm_25km", "product", "mode", "fetched_ts", "produced_ts", "rain_id",
              "window_end_ts", "accum_hours", "rain_mm", "n_cells", "file_time"):
        assert k in r, k
    assert r["hour_end_ts"] - r["hour_start_ts"] == 3600 and r["hour_end"] == "2024-06-01T06:00:00Z"
    assert r["rain_mm"] == r["rain_mm_10km"] == 2.5 and r["n_cells"] == 294
    assert r["rain_mm_25km"] is None and r["coverage_25km"] == 0.0
    assert r["mode"] == "SIMULATION" and r["feed_delay_s"] is None      # no feed delay claimed for a replay
    live = p.make_rain_record("08158000", H, mrms.PRODUCT, means, "LIVE", H + 200, H + 180, H + 201)
    assert live["feed_delay_s"] == 200.0
    import json
    json.loads(json.dumps(live, allow_nan=False))       # valid JSON, no NaN


def test_rain_id_is_idempotent_and_mode_specific():
    a = p.rain_id("08158000", mrms.PRODUCT, H, "LIVE")
    assert a == p.rain_id("08158000", mrms.PRODUCT, H + 0.0, "LIVE")
    assert a != p.rain_id("08158000", mrms.PRODUCT, H, "SIMULATION")
    assert a != p.rain_id("08158000", mrms.PRODUCT, H + 3600, "LIVE")


def test_hour_tracker_dedupe_revision_roundtrip():
    means = {5.0: (1.0, 1, 1), 10.0: (1.0, 1, 1), 25.0: (1.0, 1, 1)}
    recs = [p.make_rain_record("A", H, "P", means, "LIVE", H, H, H)]
    t = p.HourTracker()
    assert t.classify("P", H, recs) == "new"
    assert t.classify("P", H, recs) == "duplicate"
    t2 = p.HourTracker.loads(t.dumps())
    assert t2.classify("P", H, recs) == "duplicate" and t2.last_hour("P") == H
    changed = [dict(recs[0], rain_mm_10km=2.0)]
    assert t2.classify("P", H, changed) == "revision"
    t2.prune(H + 100 * 3600)
    assert t2.last_hour("P") is None


def test_hours_to_try_backfill_and_cap():
    now = H + 1800
    assert p.hours_to_try(None, now, 3) == [H - 7200, H - 3600, H]
    assert p.hours_to_try(H - 3600, now, 6) == [H]
    assert p.hours_to_try(H, now, 6) == []
    assert len(p.hours_to_try(H - 100 * 3600, now, 6)) == 6


# ---------- replay ----------

def test_parse_when_and_archive_start():
    assert p.parse_when("2024-06-01") == datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp()
    assert p.parse_when("2024-06-01", end=True) == datetime(2024, 6, 1, 23, tzinfo=timezone.utc).timestamp()
    assert p.parse_when("2024-06-01T06") == H
    hrs, before = p.replay_hours(p.parse_when("2019-05-04"), p.parse_when("2019-05-04", end=True))
    assert hrs == [] and before == 24                  # the 2019 demo flood has no MRMS archive
    hrs, before = p.replay_hours(p.parse_when("2020-10-13T22"), p.parse_when("2020-10-14T02"))
    assert before == 2 and len(hrs) == 3


def test_replay_paced_like_replay_producer(monkeypatch):
    body, _, _ = tiny_file(3.0)
    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(p, "now_epoch", lambda: 1.8e9)
    a = SimpleNamespace(product=None, start="2024-06-01T00", end="2024-06-01T05", speed=3600.0, no_sleep=False,
                        workers=2)
    fp = FakeProducer()
    calls = []

    def fetcher(product, h):
        calls.append((product, h))
        return None if h == H - 2 * 3600 else (body, h + 3400)      # 04Z missing in the "archive"

    st = p.run_replay(a, [GAUGE], fp, fetcher=fetcher, sleep=sleep, clock=lambda: clock["t"])
    assert st["hours"] == 6 and st["ok"] == 5 and st["missing"] == 1
    assert all(prod == mrms.PRODUCT for prod, _ in calls)           # replay uses Pass 2
    assert [m[0] for m in fp.msgs] == [config.TOPIC_REPLAY_RAIN] * 5
    assert all(m[2]["mode"] == "SIMULATION" and m[1] == GAUGE["site_no"] for m in fp.msgs)
    ends = [m[2]["hour_end_ts"] for m in fp.msgs]
    assert ends == sorted(ends)
    assert sum(sleeps) == pytest.approx(5.0)           # 00Z..05Z at 3600x = 5 s of wall time
    assert fp.msgs[0][2]["rain_mm_5km"] == pytest.approx(3.0)

    fp2 = FakeProducer()
    sleeps.clear()
    a.no_sleep = True
    p.run_replay(a, [GAUGE], fp2, fetcher=fetcher, sleep=sleep, clock=lambda: clock["t"])
    assert sleeps == [] and len(fp2.msgs) == 5


def test_wall_target():
    assert p.wall_target(H + 7200, H, 3600.0, 10.0) == 12.0
    assert p.wall_target(H - 3600, H, 3600.0, 10.0) == 10.0


# ---------- live poll ----------

def test_poll_once_publishes_newest_then_only_heartbeats(monkeypatch):
    body, _, _ = tiny_file(2.0)
    now = {"t": H + 600}                                # 06:10Z: the 06Z file exists, 07Z does not
    monkeypatch.setattr(p, "now_epoch", lambda: now["t"])
    avail = {H - 3600, H}

    def fetcher(product, h):
        return (body, h + 170) if h in avail else None

    fp, t, cache = FakeProducer(), p.HourTracker(), mrms.CircleCache()
    st = p.poll_once([GAUGE], "RadarOnly_QPE_01H_00.00", t, fp, cache, fetcher=fetcher, persist=False)
    recs = [m for m in fp.msgs if m[2]["type"] == "rain"]
    beats = [m for m in fp.msgs if m[2]["type"] == "heartbeat"]
    assert st["new"] == 1 and len(recs) == 1 and len(beats) == 1 and st["error"] == ""
    assert recs[0][0] == config.TOPIC_RAIN and recs[0][2]["hour_end_ts"] == H and recs[0][2]["mode"] == "LIVE"
    assert recs[0][2]["feed_delay_s"] == 600.0 and st["hours"][0]["publish_latency_min"] == pytest.approx(2.8)
    assert beats[0][2]["last_hour_end_ts"] == H

    fp.msgs.clear()                                     # same hour again: nothing new
    st = p.poll_once([GAUGE], "RadarOnly_QPE_01H_00.00", t, fp, cache, fetcher=fetcher, persist=False)
    assert st["new"] == 0 and [m[2]["type"] for m in fp.msgs] == ["heartbeat"]

    fp.msgs.clear()                                     # 3 h later, 07Z and 08Z arrived, 09Z not yet
    now["t"] = H + 3 * 3600 + 60
    avail |= {H + 3600, H + 7200}
    st = p.poll_once([GAUGE], "RadarOnly_QPE_01H_00.00", t, fp, cache, fetcher=fetcher, persist=False)
    assert [h["hour_end"] for h in st["hours"]] == ["2024-06-01T07:00:00Z", "2024-06-01T08:00:00Z"]


def test_poll_once_error_goes_to_dlq_and_heartbeats_still_sent(monkeypatch):
    monkeypatch.setattr(p, "now_epoch", lambda: H + 600)

    def boom(product, h):
        raise ConnectionError("bucket down")

    fp = FakeProducer()
    st = p.poll_once([GAUGE], "X", p.HourTracker(), fp, mrms.CircleCache(), fetcher=boom, persist=False)
    assert "bucket down" in st["error"]
    assert [m[0] for m in fp.msgs] == [config.TOPIC_DLQ, config.TOPIC_RAIN]


def test_load_gauges_by_site_or_lid():
    gs = p.load_gauges("config/gauge_locations.csv")
    assert len(gs) == 38 and all(-110 < g["lon"] < -90 for g in gs)
    assert p.load_gauges(sites="ACRT2")[0]["site_no"] == "08158000"
    with pytest.raises(SystemExit):
        p.load_gauges(sites="nope")
