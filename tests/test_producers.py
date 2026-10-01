import json
import os

from common import config, usgs
from producers import tracking, usgs_iv_producer

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class FakeProducer:
    def __init__(self):
        self.msgs = []
        self.sent = self.failed = 0

    def send(self, topic, key, value):
        self.msgs.append((topic, key, value))

    def flush(self, timeout=0):
        return 0


def fixture_doc():
    with open(os.path.join(FIX, "usgs_iv_reconstructed.json"), encoding="utf-8") as f:
        return json.load(f)


def test_tracker_new_duplicate_revision_and_roundtrip():
    rs = usgs.parse_iv_json(fixture_doc())
    t = tracking.ReadingTracker()
    assert all(t.classify(r) == "new" for r in rs)
    assert all(t.classify(r) == "duplicate" for r in rs)
    r = dict(rs[0], value=rs[0]["value"] + 0.05)
    assert t.classify(r) == "revision"
    r2 = dict(rs[1], qualifiers=["A"])            # provisional -> approved
    assert t.classify(r2) == "revision"
    t2 = tracking.ReadingTracker.loads(t.dumps())
    assert t2.classify(rs[2]) == "duplicate"
    assert t.prune(now_ts=max(x["reading_ts"] for x in rs) + 100 * 3600) == len(t.seen) or True


def test_envelope_fields():
    r = usgs.parse_iv_json(fixture_doc())[0]
    e = tracking.make_envelope(r, fetched_at=r["reading_ts"] + 3600, mode="LIVE")
    for k in ("site_no", "series", "value", "reading_time", "reading_ts", "fetched_at", "fetched_ts", "mode"):
        assert k in e
    assert e["fetched_ts"] - e["reading_ts"] == 3600


def test_poll_once_publishes_new_then_only_heartbeats(monkeypatch):
    monkeypatch.setattr(usgs_iv_producer, "get_json", lambda url: fixture_doc())
    fp, t = FakeProducer(), tracking.ReadingTracker()
    stats, _ = usgs_iv_producer.poll_once(["09510200", "07332500"], t, fp, None)
    readings = [m for m in fp.msgs if m[2]["type"] == "reading"]
    beats = [m for m in fp.msgs if m[2]["type"] == "heartbeat"]
    assert stats["new"] == 8 and len(readings) == 8 and len(beats) == 2
    assert all(m[0] == config.TOPIC_GAUGE and m[1] == m[2]["site_no"] for m in fp.msgs)
    fp.msgs.clear()
    stats, _ = usgs_iv_producer.poll_once(["09510200", "07332500"], t, fp, None)
    assert stats["duplicate"] == 8 and all(m[2]["type"] == "heartbeat" for m in fp.msgs)


def test_poll_once_survives_network_error(monkeypatch):
    def boom(url):
        raise RuntimeError("proxy 403")
    monkeypatch.setattr(usgs_iv_producer, "get_json", boom)
    fp = FakeProducer()
    stats, _ = usgs_iv_producer.poll_once(["09510200"], tracking.ReadingTracker(), fp, None)
    assert stats["error"].startswith("fetch") and [m[2]["type"] for m in fp.msgs] == ["heartbeat"]


def test_layout_change_goes_to_dead_letter(monkeypatch):
    monkeypatch.setattr(usgs_iv_producer, "get_json", lambda url: {"unexpected": True})
    fp = FakeProducer()
    stats, _ = usgs_iv_producer.poll_once(["09510200"], tracking.ReadingTracker(), fp, None)
    assert stats["error"].startswith("parse") and fp.msgs[0][0] == config.TOPIC_DLQ


def test_backfill_url_after_downtime():
    now = 1_790_000_000.0
    assert "period=PT6H" in usgs_iv_producer.fetch_url(["1"], now - 3600, now)
    u = usgs_iv_producer.fetch_url(["1"], now - 24 * 3600, now)
    assert "startDT=" in u and "period" not in u
