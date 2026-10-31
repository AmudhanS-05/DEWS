"""Rebuild agentic_ai/fixtures/drwt2_2019-05_replay.json from the measured USGS record (needs network):

    python -m tests.agents.make_replay_fixture

Fetches USGS instantaneous gauge height (00065) for DRWT2 (08158700) and its two neighbours for 2019-05-02..05 from
nwis.waterservices.usgs.gov, then runs core's own pure functions on it, exactly as the replay pipeline does but
without Kafka: `streams.windowing.aggregate` (1 h tumbling windows) and `streams.forecaster.step` with
`ml.predict.predict` (today the 3 h rise-rate baseline, no probabilities). The only difference from a live replay:
`raised_at` is the window end here (the forecaster stamps wall-clock time when it runs on Kafka).
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from agentic_ai.seed import NEIGHBOUR_DOWN, NEIGHBOUR_NEAR, SITE

OUT = Path(__file__).resolve().parents[2] / "agentic_ai" / "fixtures" / "drwt2_2019-05_replay.json"
START, END = "2019-05-02T00:00:00Z", "2019-05-06T00:00:00Z"
URL = ("https://nwis.waterservices.usgs.gov/nwis/iv/?format=json&sites={site}&startDT=2019-05-01&endDT=2019-05-06"
       "&parameterCd=00065&siteStatus=all")
W = 3600


def fetch(site: str) -> list[tuple[float, float, bool]]:
    with urllib.request.urlopen(URL.format(site=site), timeout=60) as r:
        d = json.load(r)
    ts = next(t for t in d["value"]["timeSeries"] if t["variable"]["variableCode"][0]["value"] == "00065")
    return [(datetime.fromisoformat(v["dateTime"]).timestamp(), float(v["value"]), "P" in v["qualifiers"])
            for v in ts["values"][0]["value"]]


def replay(site: str, readings, gauge: dict) -> dict:
    from ml.predict import predict
    from streams import forecaster, windowing
    t0 = datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp()
    t1 = datetime.fromisoformat(END.replace("Z", "+00:00")).timestamp()
    by: dict[float, list] = {}
    for t, v, p in readings:
        by.setdefault(windowing.window_start(t, W), []).append([t, v, p])
    prev = next((v for t, v, _ in sorted(readings, reverse=True) if t < t0), None)
    st, out = forecaster.new_state(), {"windows": [], "risk": [], "alerts": []}
    s = t0
    while s < t1:
        agg = windowing.aggregate(site, "00065", s, W, by.get(s, []), prev, "SIMULATION")
        prev = agg["stage_last"]
        fc, alerts = forecaster.step(st, agg, gauge, predict, now=s + W)
        out["windows"].append({k: agg[k] for k in ("site_no", "window_start", "window_end", "mode", "n_readings",
                                                   "stage_last", "stage_max", "stage_min", "rise_rate_ft_per_h",
                                                   "provisional")} | {"rain_1h": None, "rain_3h": None, "rain_6h": None})
        out["risk"].append({k: fc[k] for k in ("site_no", "window_start", "mode", "model", "horizon_h", "forecast_stage",
                                               "p_action", "p_minor", "p_moderate", "p_major")})
        out["alerts"] += [{k: a[k] for k in ("alert_id", "kind", "site_no", "mode", "category", "probability",
                                             "horizon_h", "forecast_stage", "current_stage", "provisional_data",
                                             "model", "raised_at", "episode_start")} for a in alerts]
        s += W
    return out


def main() -> None:
    from streams import forecaster
    gauges = forecaster.load_gauges()
    data = {"source": "USGS NWIS instantaneous values 00065, " + URL.split("?")[0],
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pipeline": "streams.windowing.aggregate (1 h) + streams.forecaster.step + ml.predict.predict",
            "note": "measured record; raised_at = window end (a Kafka replay stamps wall-clock time)",
            "windows": [], "risk": [], "alerts": []}
    for site in (SITE, NEIGHBOUR_DOWN, NEIGHBOUR_NEAR):
        r = replay(site, fetch(site), gauges[site])
        for k in ("windows", "risk", "alerts"):
            data[k] += r[k]
    OUT.write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(data['windows'])} windows, {len(data['alerts'])} alerts: "
          + ", ".join(a["alert_id"] for a in data["alerts"]) + ")")


if __name__ == "__main__":
    main()
