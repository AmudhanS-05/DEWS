"""India replay + Table 2 on the hand-made fixture (fictitious station; test-only threshold, not a CWC level)."""
import json
import shutil

from india import replay_india as R

FIX = "tests/india/fixtures/indiawris_sample.xlsx"


def test_one_command_on_fixture(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    shutil.copy(FIX, d / "FIXTURE01_2019.xlsx")
    th = tmp_path / "th.csv"
    th.write_text(open("india/thresholds.csv").read().strip() + "\nFIXTURE01,Fixture Ghat,Test,Test,Test,,,103.0,"
                  "104.0,,,test only,,\n")
    out = tmp_path / "t2.csv"
    R.main([str(d), "--thresholds", str(th), "--replay-out", str(tmp_path / "r.jsonl"), "--out", str(out)])
    envs = [json.loads(x) for x in open(tmp_path / "r.jsonl")]
    assert len(envs) == 11 and all(e["mode"] == "SIMULATION" and e["source"] == "INDIA-WRIS" for e in envs)
    rows = list(__import__("csv").DictReader(open(out)))
    assert [r["stage"] for r in rows] == R.STAGES
    by = {r["stage"]: r["measured"] for r in rows}
    assert "1 of 1 stations with a danger level" in by["official thresholds"]
    assert "1 crossings" in by["events (danger crossings)"]
    assert "median reading interval 1440.0 min" in by["hourly windows"]
    assert (tmp_path / "t2.md").read_text().startswith("| Pipeline stage")


def test_station_without_threshold_is_reported_not_estimated():
    from india.indiawris_loader import load_file
    r = R.station_report("FIXTURE01", load_file(FIX), None)
    assert r["danger_level_m"] is None and r["crossings"] is None and r["zero_filled_share"] > 0.9
