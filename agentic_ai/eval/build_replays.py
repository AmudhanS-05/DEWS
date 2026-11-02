"""Build the A4.2 evaluation set from the MEASURED USGS record (needs network once):

    python -m agentic_ai.eval.build_replays --write     # refresh agentic_ai/eval/replay_events.json
    python -m agentic_ai.eval.build_replays             # dry run into a temp file

For each event (gauge + crossing time of an official MINOR stage, from ml/figures/demo_flood_candidates.csv, plus the
May 2019 DRWT2 seed flood): USGS instantaneous gauge height 00065 for the gauge and its nearest gauges (<= 60 km, at
most 3, as the context worker sees them), 48 h before to 12 h after the crossing; then core's own pure functions,
exactly as a replay does without Kafka: `streams.windowing.aggregate` (1 h) and `streams.forecaster.step` with
`ml.predict.predict` (s3_v0 at 3 h: p_minor; active.json). Radar rain: ml's measured MRMS cache
(ml/rain_cache/mrms_circle.parquet, 10 km circle, from 2020-10-14) joined as rain_1h/3h/6h. Every row mode
SIMULATION. raised_at = the window end (a Kafka replay stamps wall-clock time; window_start is kept in details).
The target gauge's raw readings are kept too (gauge_readings), so the sensor-fault check runs on real data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "replay_events.json"
W = 3600
# (lid, site_no, MINOR crossing UTC) — post-2020 events from ml/figures/demo_flood_candidates.csv + the 2019 seed
EVENTS = [("DRWT2", "08158700", "2019-05-04 03:00"),
          ("AHOT2", "08156800", "2023-10-26 15:25"),
          ("LLAT2", "08151500", "2023-10-26 10:20"),
          ("LCPT2", "08172400", "2024-01-23 00:00"),
          ("JNCT2", "08150000", "2024-07-23 19:25"),
          ("AHOT2", "08156800", "2025-05-29 00:35"),
          ("KRRT2", "08166200", "2025-07-04 10:45"),
          ("HNTT2", "08165500", "2025-07-04 08:00"),
          # added 1 Nov for the alert_by_model re-run (more alerts; the 8 above stay for the before/after comparison)
          ("GBCT2", "08169500", "2021-10-14 07:45"),
          ("AHOT2", "08156800", "2024-05-02 02:20"),
          ("LLAT2", "08151500", "2024-07-24 04:25"),
          ("SIOT2", "08177700", "2025-06-12 08:45")]
URL = ("https://nwis.waterservices.usgs.gov/nwis/iv/?format=json&sites={site}&startDT={a}&endDT={b}"
       "&parameterCd=00065&siteStatus=all")


def _utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def fetch(site: str, t0: datetime, t1: datetime) -> list[tuple[float, float, bool]]:
    url = URL.format(site=site, a=(t0 - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%MZ"),
                     b=t1.strftime("%Y-%m-%dT%H:%MZ"))
    import time
    for i in range(5):        # the USGS service sometimes cuts a chunked reply: retry with backoff
        try:
            try:
                import requests       # urllib's chunked read fails behind some proxies; requests does not
                d = requests.get(url, timeout=90).json()
            except ImportError:
                with urllib.request.urlopen(url, timeout=90) as r:
                    d = json.load(r)
            break
        except Exception:  # noqa: BLE001
            if i == 4:
                raise
            time.sleep(2 ** (i + 1))
    out = []
    for ts in d["value"]["timeSeries"]:
        if ts["variable"]["variableCode"][0]["value"] != "00065":
            continue
        for v in ts["values"][0]["value"]:     # first series only (one sensor), as the producer's default
            try:
                out.append((datetime.fromisoformat(v["dateTime"]).timestamp(), float(v["value"]), "P" in v["qualifiers"]))
            except ValueError:
                pass
        break
    return sorted(out)


def neighbours(site: str) -> list[str]:
    from agentic_ai import rules
    from agentic_ai.tools import _csv_all, _csv_gauge, _dist_km
    me = _csv_gauge(site)
    topo = rules.TOPOLOGY.get(site, {"upstream": [], "downstream": []})
    picks = list(topo["upstream"]) + list(topo["downstream"])
    near = sorted((_dist_km(me, g), g["site_no"]) for g in _csv_all() if g["site_no"] != site and g["site_no"] not in picks)
    picks += [s for d, s in near if d <= 60.0][:3]
    return picks


def rain_hours(sites: list[str], t0: float, t1: float) -> dict[str, dict[float, float]]:
    """{site: {hour_end_ts: rain_mm}} from ml's MRMS cache (10 km circle); {} before 2020-10-14 or without pandas."""
    try:
        import pandas as pd
        d = pd.read_parquet(Path(__file__).resolve().parents[2] / "ml" / "rain_cache" / "mrms_circle.parquet")
    except Exception:  # noqa: BLE001
        return {}
    d = d[(d.radius_km == 10.0) & d.site_no.isin(sites) & (d.hour_ts > t0 - 7 * W) & (d.hour_ts <= t1 + W)]
    out: dict[str, dict[float, float]] = {}
    for s, h, mm in zip(d.site_no, d.hour_ts, d.rain_mm):
        out.setdefault(s, {})[float(h)] = float(mm)
    return out


def replay(site: str, readings, gauge: dict, t0: float, t1: float, rain: dict[float, float]) -> dict:
    from ml.predict import predict
    from streams import forecaster, windowing
    by: dict[float, list] = {}
    for t, v, p in readings:
        by.setdefault(windowing.window_start(t, W), []).append([t, v, p])
    prev = next((v for t, v, _ in reversed(readings) if t < t0), None)
    st, out = forecaster.new_state(), {"windows": [], "risk": [], "alerts": []}
    s = t0
    while s < t1:
        agg = windowing.aggregate(site, "00065", s, W, by.get(s, []), prev, "SIMULATION")
        prev = agg["stage_last"]
        end = s + W     # MRMS hour ending at the window end; None when the cache has no value (no rain known)
        r = {k: (round(sum(rain.get(end - i * W, 0.0) for i in range(n)), 2) if rain.get(end) is not None else None)
             for k, n in (("rain_1h", 1), ("rain_3h", 3), ("rain_6h", 6))}
        agg.update(r)
        fc, alerts = forecaster.step(st, agg, gauge, predict, now=s + W)
        out["windows"].append({k: agg[k] for k in ("site_no", "window_start", "window_end", "mode", "n_readings",
                                                   "stage_last", "stage_max", "stage_min", "rise_rate_ft_per_h",
                                                   "provisional", "rain_1h", "rain_3h", "rain_6h")})
        out["risk"].append({k: fc[k] for k in ("site_no", "window_start", "mode", "model", "horizon_h", "forecast_stage",
                                               "p_action", "p_minor", "p_moderate", "p_major")})
        out["alerts"] += [{k: a[k] for k in ("alert_id", "kind", "site_no", "mode", "category", "probability",
                                             "horizon_h", "forecast_stage", "current_stage", "provisional_data",
                                             "model", "raised_at", "window_start", "window_start_ts",
                                             "episode_start")} | {"alert_rule": a.get("alert_rule"), "tau": a.get("tau")}
                             for a in alerts]
        s += W
    return out


def build(fetch_fn=None) -> dict:
    from streams import forecaster
    fetch_fn = fetch_fn or fetch
    gauges = forecaster.load_gauges()
    data = {"source": "USGS NWIS instantaneous values 00065 + ml/rain_cache/mrms_circle.parquet (10 km)",
            "pipeline": "streams.windowing.aggregate (1 h) + streams.forecaster.step + ml.predict.predict",
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "events": []}
    for lid, site, cross in EVENTS:
        c = _utc(cross)
        t0 = (c - timedelta(hours=48)).replace(minute=0)
        t1 = (c + timedelta(hours=12)).replace(minute=0)
        sites = [site] + neighbours(site)
        rain = rain_hours(sites, t0.timestamp(), t1.timestamp())
        ev = {"lid": lid, "site_no": site, "crossing_utc": cross, "start": t0.isoformat(), "end": t1.isoformat(),
              "windows": [], "risk": [], "alerts": [], "readings": [], "sites": sites,
              "rain_known": bool(rain.get(site))}
        for s in sites:
            if s not in gauges:
                continue
            rd = fetch_fn(s, t0, t1)
            r = replay(s, rd, gauges[s], t0.timestamp(), t1.timestamp(), rain.get(s, {}))
            for k in ("windows", "risk"):
                ev[k] += r[k]
            if s == site:
                ev["alerts"] = r["alerts"]
                ev["readings"] = [[round(t), v, p] for t, v, p in rd if t >= t0.timestamp() - 48 * W]
        data["events"].append(ev)
        print(f"{lid} {cross}: {len(ev['sites'])} gauges, {len(ev['windows'])} windows, rain "
              f"{'yes' if ev['rain_known'] else 'no'}, alerts: " +
              (", ".join(f"{a['kind']} {a['category']} p={a['probability']}" for a in ev["alerts"]) or "none"),
              flush=True)
    return data


def main(out: Path | None = None) -> Path:
    out = Path(out) if out is not None else Path(tempfile.gettempdir()) / "replay_events.json"
    data = build()
    out.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return out


if __name__ == "__main__":
    main(OUT if "--write" in sys.argv[1:] else None)
