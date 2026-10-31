"""Insert the May 2019 DRWT2 (Onion Creek near Driftwood) flood into PostgreSQL, so the agents can run before (or
without) a Kafka replay:  python -m agentic_ai.seed [--dsn ...] [--drop-illustrative]

Default: the MEASURED record (agentic_ai/fixtures/drwt2_2019-05_replay.json): USGS gauge height for DRWT2 and its two
neighbours, 2019-05-02..05, put through core's own windowing and forecaster functions (rebuild with
`python -m tests.agents.make_replay_fixture`). These are the same rows a core replay writes, so seeding before or
after a replay gives the same numbers. Alert: 08158700|major|2019-05-04T01:00:00Z (gauge 8.16 ft, rise-rate forecast
24.72 ft in 3 h, no probability: the baseline gives none). No radar rain is joined (rain fields are empty).

`build()` (in-memory tests only) is an ILLUSTRATIVE shape with made-up rain and probabilities, NOT the measured
record; it shares keys with the real replay, so it is never written to a database that holds replay rows.
`--drop-illustrative` removes what an earlier version of this seed wrote (illustrative alerts, their advisories and
agent rows, and any illustrative window or forecast row a replay has not overwritten). Every row: mode = SIMULATION.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentic_ai.db import connect, ensure_schema

SITE = "08158700"
NEIGHBOUR_DOWN = "08159000"   # Onion Creek at US 183 (downstream)
NEIGHBOUR_NEAR = "08158840"   # Slaughter Creek (nearby, stays flat)
REPLAY_FIXTURE = Path(__file__).with_name("fixtures") / "drwt2_2019-05_replay.json"
REPLAY_ALERT = "08158700|major|2019-05-04T01:00:00Z"
ILLUSTRATIVE_ALERTS = ("08158700|minor|2019-05-04T03:00:00Z", "08158700|moderate|2019-05-04T03:00:00Z")
START = datetime(2019, 5, 2, tzinfo=timezone.utc)
HOURS = 96
LEVELS = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}
HORIZON_H = 6

# (hours since START, stage ft) for DRWT2; linear in between
_DRWT2 = [(0, 1.2), (48, 1.4), (49, 1.6), (50, 1.9), (51, 2.6), (52, 3.5), (53, 4.4), (54, 5.4), (55, 6.6), (56, 7.9),
          (57, 8.9), (58, 9.4), (59, 9.2), (60, 8.7), (66, 6.0), (72, 4.0), (80, 2.5), (95, 1.6)]
# downstream gauge lags by 5 h and is lower; the neighbour stays flat
_DOWN_LAG_H = 5
RAIN_1H_MM = {44: 2.0, 45: 6.0, 46: 11.0, 47: 15.0, 48: 18.0, 49: 14.0, 50: 9.0, 51: 4.0, 52: 1.0}


def _interp(points, h):
    for (h0, v0), (h1, v1) in zip(points, points[1:]):
        if h0 <= h <= h1:
            return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
    return points[-1][1]


def _sig(x):
    return 1 / (1 + math.exp(-x))


def build() -> dict:
    """The seed rows as plain data (also used by the tests as the in-memory fixture)."""
    drw = [round(_interp(_DRWT2, h), 2) for h in range(HOURS)]
    down = [round(2.0 + 0.55 * (_interp(_DRWT2, h - _DOWN_LAG_H) - 1.2), 2) for h in range(HOURS)]
    flat = [1.1] * HOURS
    windows, risk = [], []
    for site, series in ((SITE, drw), (NEIGHBOUR_DOWN, down), (NEIGHBOUR_NEAR, flat)):
        for h in range(HOURS):
            ws = START + timedelta(hours=h)
            rise = round(series[h] - series[h - 1], 2) if h else 0.0
            r1 = RAIN_1H_MM.get(h, 0.0) if site == SITE else None
            r3 = round(sum(RAIN_1H_MM.get(k, 0.0) for k in range(h - 2, h + 1)), 1) if site == SITE else None
            r6 = round(sum(RAIN_1H_MM.get(k, 0.0) for k in range(h - 5, h + 1)), 1) if site == SITE else None
            windows.append({"site_no": site, "window_start": ws, "window_end": ws + timedelta(hours=1),
                            "mode": "SIMULATION", "n_readings": 12, "stage_last": series[h],
                            "stage_max": series[h], "stage_min": round(series[h] - max(rise, 0), 2),
                            "rise_rate_ft_per_h": rise, "rain_1h": r1, "rain_3h": r3, "rain_6h": r6,
                            "provisional": True})
            fc = round(series[h] + HORIZON_H * max(rise, 0.0), 2)
            risk.append({"site_no": site, "window_start": ws, "mode": "SIMULATION", "model": "rise_rate",
                         "horizon_h": HORIZON_H, "forecast_stage": fc,
                         **{f"p_{c}": round(_sig(fc - LEVELS[c]), 3) for c in LEVELS}})
    episode = "2019-05-04T03:00:00Z"
    alerts = []
    for hour, cat in ((51, "minor"), (54, "moderate")):   # window starting 03:00 / 06:00; raised at its end
        ws = START + timedelta(hours=hour)
        r = next(x for x in risk if x["site_no"] == SITE and x["window_start"] == ws)
        alerts.append({"alert_id": f"{SITE}|{cat}|{episode}", "site_no": SITE, "mode": "SIMULATION", "category": cat,
                       "probability": round(r[f"p_{cat}"], 2), "horizon_h": HORIZON_H, "provisional_data": True,
                       "status": "draft", "raised_at": ws + timedelta(hours=1)})
    return {"windows": windows, "risk": risk, "alerts": alerts, "primary_alert_id": alerts[0]["alert_id"]}


def _dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def build_replay() -> dict:
    """The measured record as rows (window_start etc. as datetimes). Retractions mark their alert retracted."""
    d = json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))
    windows = [{**w, "window_start": _dt(w["window_start"]), "window_end": _dt(w["window_end"])} for w in d["windows"]]
    risk = [{**r, "window_start": _dt(r["window_start"])} for r in d["risk"]]
    alerts: dict[str, dict] = {}
    for a in d["alerts"]:
        if a["kind"] == "retract":
            alerts[a["alert_id"]]["status"] = "retracted"
        else:
            alerts[a["alert_id"]] = {k: a[k] for k in ("alert_id", "site_no", "mode", "category", "probability",
                                                       "horizon_h", "provisional_data")} | {
                "status": "draft", "raised_at": _dt(a["raised_at"])}
    return {"windows": windows, "risk": risk, "alerts": list(alerts.values()), "primary_alert_id": REPLAY_ALERT}


_W_SQL = ("INSERT INTO windowed_aggregates (site_no, window_start, window_end, mode, n_readings, stage_last, "
          "stage_max, stage_min, rise_rate_ft_per_h, rain_1h, rain_3h, rain_6h, provisional) "
          "VALUES (%(site_no)s,%(window_start)s,%(window_end)s,%(mode)s,%(n_readings)s,%(stage_last)s,"
          "%(stage_max)s,%(stage_min)s,%(rise_rate_ft_per_h)s,%(rain_1h)s,%(rain_3h)s,%(rain_6h)s,%(provisional)s) ")
_R_SQL = ("INSERT INTO risk_scores (site_no, window_start, mode, model, horizon_h, forecast_stage, p_action, p_minor, "
          "p_moderate, p_major) VALUES (%(site_no)s,%(window_start)s,%(mode)s,%(model)s,%(horizon_h)s,"
          "%(forecast_stage)s,%(p_action)s,%(p_minor)s,%(p_moderate)s,%(p_major)s) ")
_A_SQL = ("INSERT INTO alerts (alert_id, site_no, mode, category, probability, horizon_h, provisional_data, status, "
          "raised_at) VALUES (%(alert_id)s,%(site_no)s,%(mode)s,%(category)s,%(probability)s,%(horizon_h)s,"
          "%(provisional_data)s,%(status)s,%(raised_at)s) ON CONFLICT (alert_id) DO NOTHING")


def drop_illustrative(conn) -> dict:
    """Delete what the illustrative seed wrote; rows a replay has overwritten (different values) are kept."""
    d = build()
    n = {"alerts": 0, "windows": 0, "risk": 0}
    with conn.cursor() as cur:
        ids = list(ILLUSTRATIVE_ALERTS)
        for t in ("agent_events", "agent_runs", "advisories"):
            cur.execute(f"DELETE FROM {t} WHERE alert_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM alerts WHERE alert_id = ANY(%s)", (ids,))
        n["alerts"] = cur.rowcount
        for w in d["windows"]:
            cur.execute("DELETE FROM windowed_aggregates WHERE site_no=%(site_no)s AND window_start=%(window_start)s "
                        "AND mode=%(mode)s AND n_readings=%(n_readings)s AND stage_last=%(stage_last)s "
                        "AND rise_rate_ft_per_h=%(rise_rate_ft_per_h)s", w)
            n["windows"] += cur.rowcount
        for r in d["risk"]:
            cur.execute("DELETE FROM risk_scores WHERE site_no=%(site_no)s AND window_start=%(window_start)s "
                        "AND mode=%(mode)s AND model=%(model)s AND horizon_h=%(horizon_h)s "
                        "AND forecast_stage=%(forecast_stage)s", r)
            n["risk"] += cur.rowcount
    return n


def _replay_rows_present(conn) -> bool:
    """True when the database holds windows for the seed gauge that differ from the illustrative shape."""
    ill = {(w["window_start"], w["stage_last"]) for w in build()["windows"] if w["site_no"] == SITE}
    with conn.cursor() as cur:
        cur.execute("SELECT window_start, stage_last FROM windowed_aggregates WHERE site_no=%s AND mode='SIMULATION' "
                    "AND window_start >= %s AND window_start < %s", (SITE, START, START + timedelta(hours=HOURS)))
        return any((ws, st) not in ill for ws, st in cur.fetchall())


def load(conn, illustrative: bool = False) -> str:
    """Seed the database; returns the alert id to run. Default: the measured record (see the module docstring).
    `illustrative=True` (tests on a throwaway schema) writes build() and refuses if replay rows are present."""
    ensure_schema(conn)
    if not illustrative:
        drop_illustrative(conn)
        d = build_replay()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO gauges (site_no, nwps_lid, name, stage_units, action_stage, minor_stage, "
                        "moderate_stage, major_stage, median_rise_h) VALUES (%s,'DRWT2','Onion Creek near Driftwood',"
                        "'ft',%s,%s,%s,%s,10.9) ON CONFLICT (site_no) DO NOTHING",
                        (SITE, LEVELS["action"], LEVELS["minor"], LEVELS["moderate"], LEVELS["major"]))
            for w in d["windows"]:   # same values a core replay writes, so overwriting is harmless
                cur.execute(_W_SQL + "ON CONFLICT (site_no, window_start, mode) DO UPDATE SET n_readings = "
                            "EXCLUDED.n_readings, stage_last = EXCLUDED.stage_last, stage_max = EXCLUDED.stage_max, "
                            "stage_min = EXCLUDED.stage_min, rise_rate_ft_per_h = EXCLUDED.rise_rate_ft_per_h, "
                            "provisional = EXCLUDED.provisional", w)
            for r in d["risk"]:
                cur.execute(_R_SQL + "ON CONFLICT (site_no, window_start, mode, model, horizon_h) DO UPDATE SET "
                            "forecast_stage = EXCLUDED.forecast_stage", r)
            for a in d["alerts"]:
                cur.execute(_A_SQL, a)
        return d["primary_alert_id"]
    if _replay_rows_present(conn):
        raise RuntimeError("replay rows for 08158700 are in this database: the illustrative seed would mix with them")
    return _load_illustrative(conn)


def _load_illustrative(conn) -> str:
    d = build()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO gauges (site_no, nwps_lid, name, stage_units, action_stage, minor_stage, moderate_stage, "
                    "major_stage, median_rise_h) VALUES (%s,'DRWT2','Onion Creek near Driftwood','ft',%s,%s,%s,%s,10.9) "
                    "ON CONFLICT (site_no) DO NOTHING",
                    (SITE, LEVELS["action"], LEVELS["minor"], LEVELS["moderate"], LEVELS["major"]))
        for w in d["windows"]:
            cur.execute("INSERT INTO windowed_aggregates (site_no, window_start, window_end, mode, n_readings, stage_last, "
                        "stage_max, stage_min, rise_rate_ft_per_h, rain_1h, rain_3h, rain_6h, provisional) "
                        "VALUES (%(site_no)s,%(window_start)s,%(window_end)s,%(mode)s,%(n_readings)s,%(stage_last)s,"
                        "%(stage_max)s,%(stage_min)s,%(rise_rate_ft_per_h)s,%(rain_1h)s,%(rain_3h)s,%(rain_6h)s,%(provisional)s) "
                        "ON CONFLICT (site_no, window_start, mode) DO NOTHING", w)
        cur.execute("SELECT count(*) FROM risk_scores WHERE mode='SIMULATION' AND site_no = %s", (SITE,))
        if cur.fetchone()[0] == 0:
            for r in d["risk"]:
                cur.execute("INSERT INTO risk_scores (site_no, window_start, mode, model, horizon_h, forecast_stage, "
                            "p_action, p_minor, p_moderate, p_major) VALUES (%(site_no)s,%(window_start)s,%(mode)s,"
                            "%(model)s,%(horizon_h)s,%(forecast_stage)s,%(p_action)s,%(p_minor)s,%(p_moderate)s,%(p_major)s)", r)
        for a in d["alerts"]:
            cur.execute("INSERT INTO alerts (alert_id, site_no, mode, category, probability, horizon_h, provisional_data, "
                        "status, raised_at) VALUES (%(alert_id)s,%(site_no)s,%(mode)s,%(category)s,%(probability)s,"
                        "%(horizon_h)s,%(provisional_data)s,%(status)s,%(raised_at)s) ON CONFLICT (alert_id) DO NOTHING", a)
    return d["primary_alert_id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--drop-illustrative", action="store_true",
                    help="only delete what the earlier illustrative seed wrote (alerts 08158700|minor|... and "
                         "|moderate|..., their advisories and agent rows), then stop")
    args = ap.parse_args()
    conn = connect(args.dsn)
    if args.drop_illustrative:
        ensure_schema(conn)
        print(f"removed illustrative seed rows: {drop_illustrative(conn)}")
        return
    alert_id = load(conn)
    print(f"seeded DRWT2 May 2019, measured USGS record (SIMULATION). Run: python -m agentic_ai.run --alert-id '{alert_id}'")


if __name__ == "__main__":
    main()
