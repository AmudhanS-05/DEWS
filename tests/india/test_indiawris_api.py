"""india.indiawris_api with a mocked HTTP layer. The real response layout is UNVERIFIED (host unreachable from the
cloud); these tests pin the request shape from the OpenAPI spec and the tolerant parsing, not the real body."""
import json

import requests

from india import indiawris_api as A
from india import indiawris_loader as L


class Resp:
    def __init__(self, code=200, doc=None, text=None):
        self.status_code, self._doc = code, doc
        self.text = text if text is not None else json.dumps(doc)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._doc is None:
            raise ValueError("not json")
        return self._doc


class Sess:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def post(self, url, params=None, **kw):
        self.calls.append((url, dict(params)))
        r = self.fn(url, params)
        if isinstance(r, Exception):
            raise r
        return r


def rec(code, day, v):
    return {"stationCode": code, "stationName": "Fixture", "dataTime": f"2019-08-{day:02d} 08:00:00",
            "dataValue": v, "unit": "m"}


def test_endpoint_and_query_follow_the_spec():
    assert A.endpoint("water_level", "basin") == "https://indiawris.gov.in/Dataset/Basin/River%20WaterLevel"
    assert A.endpoint("water_level", "state") == "https://indiawris.gov.in/Dataset/River%20Water%20Level"
    q = A.query("basin", "2019-01-01", "2019-12-31", 0, 1000, "CWC", basin="Godavari")
    assert q == {"agencyName": "CWC", "startdate": "2019-01-01", "enddate": "2019-12-31", "page": 0, "size": 1000,
                 "download": "false", "basinName": "Godavari", "tributaryName": "Godavari"}
    try:
        A.query("basin", "a", "b", 0, 1001, "CWC", basin="G")
        raise AssertionError("size > 1000 accepted")
    except ValueError:
        pass


def test_date_chunks_cover_range_without_overlap():
    ch = A.date_chunks("2019-12-30", "2021-01-02", days=365)
    assert ch[0] == ("2019-12-30", "2020-12-28") and ch[-1][1] == "2021-01-02"
    assert all(a[1] < b[0] for a, b in zip(ch, ch[1:]))


def test_backoff_then_success():
    seq = [requests.exceptions.ConnectionError("dns"), Resp(503, {}), Resp(200, {"ok": 1})]
    waits = []
    r = A.post_with_backoff("u", {}, session=Sess(lambda u, p: seq.pop(0)), sleep=waits.append)
    assert r.json() == {"ok": 1} and waits == [2.0, 4.0]


def test_extract_records_shapes():
    rs = [rec("X", 1, 1.0)]
    for doc in (rs, {"statusCode": 200, "data": rs}, {"data": {"content": rs, "last": True}}, {"content": rs}):
        assert A.extract_records(doc) == rs
    assert A.extract_records({"statusCode": 200, "message": "ok", "data": {}}) == []
    assert A.extract_records(None) == []


def test_fetch_pages_saves_raw_and_feeds_the_loader(tmp_path):
    pages = {0: [rec("X1", d, 100 + d) for d in range(1, 4)], 1: [rec("X1", 4, 104.0)]}
    s = Sess(lambda u, p: Resp(200, {"statusCode": 200, "message": "ok", "data": pages[p["page"]]}))
    res = A.fetch("water_level", "basin", "2019-08-01", "2019-08-10", "CWC", str(tmp_path), size=3, session=s,
                  basin="Godavari", tributary="Godavari")
    assert [c[1]["page"] for c in s.calls] == [0, 1]           # stops on the short page
    assert res["pages"] == 2 and len(res["records"]) == 4 and not res["errors"]
    assert all((tmp_path / "raw" / "indiawris" / "api").rglob("*.json"))
    rs = A.to_readings(res["records"])
    assert rs[0]["reading_time"] == "2019-08-01T02:30:00Z" and rs[0]["mode"] == "SIMULATION"
    paths = A.write_loader_csv(rs, str(tmp_path / "raw" / "indiawris" / "api_csv"))
    got = L.load_folder(str(tmp_path / "raw" / "indiawris"))          # raw JSON pages are ignored by the loader
    assert list(got) == ["X1"] and [r["value"] for r in got["X1"]] == [101.0, 102.0, 103.0, 104.0]
    assert got["X1"][0]["reading_time"] == "2019-08-01T02:30:00Z" and len(paths) == 1


def test_http_error_and_non_json_are_kept(tmp_path):
    s = Sess(lambda u, p: Resp(404, None, text="<html>not found</html>"))
    res = A.fetch("water_level", "basin", "2019-08-01", "2019-08-02", "CWC", str(tmp_path), session=s, basin="G")
    assert res["errors"] == ["2019-08-01..2019-08-02 p0: HTTP 404"]
    assert res["raw_files"][0].endswith(".txt")


def test_probe_prints_status_headers_and_body(tmp_path):
    out = []
    s = Sess(lambda u, p: Resp(200, {"statusCode": 200, "message": "ok", "data": [rec("X", 1, 1.5)]}))
    code = A.probe(end="2019-08-07", data_dir=str(tmp_path), session=s, printer=out.append)
    assert code == 0 and s.calls[0][1]["startdate"] == "2019-08-01" and s.calls[0][1]["size"] == 10
    text = "\n".join(out)
    assert "HTTP 200" in text and "Content-Type: application/json" in text and '"statusCode": 200' in text
    assert "1 record(s) found" in text and "1 parsed as readings" in text


def test_probe_reports_unreachable_host(tmp_path):
    out = []
    s = Sess(lambda u, p: requests.exceptions.ConnectionError("Name or service not known"))
    import india.indiawris_api as mod
    orig = mod.post_with_backoff
    mod.post_with_backoff = lambda *a, **k: orig(*a, **{**k, "sleep": lambda _: None})
    try:
        assert A.probe(data_dir=str(tmp_path), session=s, printer=out.append) == 2
    finally:
        mod.post_with_backoff = orig
    assert any("FAILED" in x for x in out)
