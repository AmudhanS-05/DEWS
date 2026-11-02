"""ml.fetch_history: cache layout matches the existing loader, backoff, blocked-host handling (no network)."""
import json
from datetime import date

import pytest
import requests

from common import config
from ml import fetch_history as fh

FIX = "tests/fixtures/usgs_iv_reconstructed.json"


class _Resp:
    def __init__(self, code, doc=None):
        self.status_code, self._doc = code, doc

    def json(self):
        return self._doc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


class _Sess:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), 0

    def get(self, url, **kw):
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def test_backoff_retries_5xx_then_succeeds():
    waits = []
    s = _Sess([_Resp(503), requests.exceptions.ConnectionError("reset"), _Resp(200, {"ok": 1})])
    assert fh.get_with_backoff("u", session=s, sleep=waits.append) == {"ok": 1}
    assert waits == [2.0, 4.0] and s.calls == 3


def test_proxy_refusal_is_not_retried():
    s = _Sess([requests.exceptions.ProxyError("403 Forbidden")])
    with pytest.raises(fh.Blocked):
        fh.get_with_backoff("u", session=s, sleep=lambda _: None)
    assert s.calls == 1


def test_written_year_is_read_by_the_replay_loader(tmp_path, monkeypatch):
    doc = json.load(open(FIX, encoding="utf-8"))
    site = doc["value"]["timeSeries"][0]["sourceInfo"]["siteCode"][0]["value"]
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    (tmp_path / "raw" / "usgs").mkdir(parents=True)
    monkeypatch.setattr(fh, "get_with_backoff", lambda url: doc)
    st, n = fh.fetch_year(site, 2020, date(2026, 10, 31))
    assert st == "fetched" and n > 0
    assert (tmp_path / "raw" / "usgs" / f"{site}_00065_2020.json").exists()
    from producers.replay_producer import load_readings
    rs = load_readings(site, "1990-01-01", "2100-12-31")
    assert len([r for r in rs if r["parameter"] == "00065"]) == n or len(rs) == n
    # a cached past year is not refetched
    monkeypatch.setattr(fh, "get_with_backoff", lambda url: (_ for _ in ()).throw(AssertionError("refetched")))
    assert fh.fetch_year(site, 2020, date(2026, 10, 31))[0] == "cached"


def test_year_url_ends_today_for_current_year():
    u = fh.year_url("08158700", 2026, date(2026, 10, 31))
    assert "startDT=2026-01-01" in u and "endDT=2026-10-31" in u and "parameterCd=00065" in u
    assert "endDT=2019-12-31" in fh.year_url("08158700", 2019, date(2026, 10, 31))
