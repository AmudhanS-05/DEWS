"""Gauge finder logic with the network replaced by canned responses (made up)."""
from tools import find_gauges as fg

META = {
    "AAAA1": {"lid": "AAAA1", "usgsId": "08171000", "name": "River A", "state": {"abbreviation": "TX"},
              "flood": {"stageUnits": "ft", "categories": {"action": {"stage": 10}, "minor": {"stage": 12},
                                                          "moderate": {"stage": 15}, "major": {"stage": 20}}}},
    "BBBB1": {"lid": "BBBB1", "usgsId": "", "name": "River B (no USGS link)", "state": {"abbreviation": "TX"},
              "flood": {"categories": {"action": {"stage": 5}, "minor": {"stage": 6}}}},
    "CCCC1": {"lid": "CCCC1", "usgsId": "08170000", "name": "River C (no categories)", "state": {"abbreviation": "TX"},
              "flood": {"categories": {"action": {"stage": -9999}, "minor": {"stage": -9999}}}},
}


def fake_get(url):
    if "bbox.xmin" in url:
        return {"gauges": [{"lid": k} for k in META]}
    return META[url.rsplit("/", 1)[1]]


def test_scan_keeps_only_usable(monkeypatch, tmp_path):
    monkeypatch.setattr(fg, "get_json", fake_get)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    assert fg.main(["--bbox=-100,29,-97,31", "--write"]) == 0
    text = (tmp_path / "config" / "candidate_gauges.csv").read_text()
    assert "08171000,AAAA1" in text and "BBBB1" not in text and "CCCC1" not in text


def test_list_response_shapes(monkeypatch):
    monkeypatch.setattr(fg, "get_json", lambda url: [{"lid": "X"}])
    assert fg.list_gauges(0, 0, 1, 1) == [{"lid": "X"}]
