"""Check that a slice is really done, against the running stack (C1.8, runbook step 15).

    python -m tools.verify_slice S1                  # all workstreams
    python -m tools.verify_slice S1 --part core      # only your part (core | ml | agents)
    python -m tools.verify_slice S1 --skip-tests     # without the pytest run
    python -m tools.verify_slice S2 --part core      # rain: AHOT2 2023-10-26 replay with replay.rain (RUNBOOK S2 rows)

Every check looks at the real thing: Kafka metadata, consumer groups, PostgreSQL rows, the console, the files. Each
result is PASS or FAIL with what was measured. Steps only a person can confirm are listed as MANUAL: they never count
as a pass and do not fail the run either, and the summary says how many are open.
Exit code 0 only when nothing FAILED. A later slice has no checks until its code exists; asking for one exits with 2.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from common import config
from common.progress import Task

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
DRWT2 = "08158700"
FLOOD = ("2019-05-01", "2019-06-01")                     # the May 2019 Onion Creek replay, S1's story
AHOT2 = "08156800"                                       # S2's story: Shoal Creek, minor crossing 2023-10-26 15:25Z
RAIN_EVENT = ("2023-10-25T00:00:00+00:00", "2023-10-27T00:00:00+00:00")   # replay window (MRMS archive from 2020-10-14)
SCENARIOS = ("faust", "broker", "sink", "postgres", "notifier", "producer")
GROUPS = {config.GROUP_WINDOWING: 3, config.GROUP_SINK: 1, config.GROUP_FORECASTER: 1, config.GROUP_NOTIFIER: 1}
TOPICS = {config.TOPIC_GAUGE, config.TOPIC_RAIN, config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN, config.TOPIC_AGG,
          config.TOPIC_FORECASTS, config.TOPIC_ALERTS, config.TOPIC_APPROVED, config.TOPIC_DLQ}
MANUAL = {"S1": ["Power BI page 1: a new replayed reading appears within one page refresh (runbook step 12)",
                 "the S1 scene in docs/DEMO.md runs twice in a row",
                 "the approved email reached a person's inbox (delivered_at only says SMTP accepted it)"],
          "S2": ["Map: rain rings around AHOT2 grow before its stage rises (C2.3)",
                 "Power BI page 2 shows rain next to stage for the AHOT2 replay (C2.4)"]}


# ---- pure logic (tests/test_verify_slice.py) ----------------------------------------------------------------------

def declared_objects(sql: str) -> tuple[set[str], set[str]]:
    """(tables, views) a SQL file creates."""
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    views = set(re.findall(r"CREATE OR REPLACE VIEW (\w+)", sql))
    return tables, views


def missing(expected, found) -> str:
    gone = sorted(set(expected) - set(found))
    return "missing: " + ", ".join(gone) if gone else ""


def latest_drills(rows: list[tuple[str, float | None, str]]) -> dict[str, tuple[float | None, str]]:
    """Newest result per scenario from (run_label, recovery_time_s, notes) rows, oldest row first."""
    return {label.split(":", 1)[1]: (rec, notes) for label, rec, notes in rows}


def drills_verdict(latest: dict[str, tuple[float | None, str]], scenarios=SCENARIOS) -> tuple[bool, str]:
    bad = [s for s in scenarios if s not in latest or latest[s][0] is None or "FAILED:" in latest[s][1]]
    text = ", ".join(f"{s} {latest[s][0]:.1f} s" for s in scenarios if s in latest and latest[s][0] is not None)
    return not bad, text + (f"; not passed: {', '.join(bad)}" if bad else "")


def episode_lead_times(alerts: list[tuple[str, str]], windows: list[tuple[str, float | None]], level: float,
                       feed_delay_h: float = 1.0) -> tuple[bool, str]:
    """alerts = (alert_id, kind); windows = (window_start, stage_last) ascending, ISO in one format. S1 asks for one `new`
    alert per episode and a measured lead time, negative allowed. Lead = first window with stage at `level` minus the window
    that raised the alert, at window resolution; net subtracts the feed delay. Alerting before the crossing is S3's bar."""
    from datetime import datetime
    new: dict[str, int] = {}
    for alert_id, kind in alerts:
        ep = alert_id.split("|")[2]
        new[ep] = new.get(ep, 0) + (kind == "new")
    if not new:
        return False, "no alert"
    bad = sorted(ep for ep, n in new.items() if n != 1)
    parts = []
    for ep in sorted(new):
        start = _iso(ep)
        cross = next((w for w, stage in windows if stage is not None and stage >= level and w >= start), None)
        if cross is None:
            parts.append(f"episode {ep}: stage never reached {level:g} ft")
            continue
        gross = (datetime.fromisoformat(cross) - datetime.fromisoformat(start)).total_seconds() / 3600
        parts.append(f"episode {ep}: lead {gross:+.1f} h gross, {gross - feed_delay_h:+.1f} h net of feed delay")
    return not bad, "; ".join(parts) + (f"; not exactly one new alert: {', '.join(bad)}" if bad else "")


def alert_before_crossing(windows: list[tuple[str, float | None]], alert_window: str | None, level: float) -> tuple[bool, str]:
    """S3 acceptance, against the trained model. windows = (window_start, stage_last) ascending. True if the alert's window
    comes before the first stage at `level`."""
    crossing = next((w for w, stage in windows if stage is not None and stage >= level), None)
    if alert_window is None:
        return False, "no alert"
    if crossing is None:
        return True, f"alert {alert_window}, stage never reached {level:g} ft"
    return alert_window < crossing, f"alert window {alert_window}, first stage at {level:g} ft in window {crossing}"


def rain_before_stage(windows: list[tuple[str, float | None, float | None]], level: float,
                      before_h: float = 24.0, after_h: float = 6.0) -> tuple[bool, str]:
    """S2: windows = (window_start ISO, stage_max, rain_1h) ascending. The first window whose stage reaches the official
    `level` is the crossing; the wettest hour (largest rain_1h) between before_h before and after_h after it must lie in
    an EARLIER window. No rain threshold is invented: only the order of the peak rain and the crossing is checked."""
    from datetime import datetime
    cross = next((w for w, st, _ in windows if st is not None and st >= level), None)
    if cross is None:
        return False, f"stage never reached {level:g} ft"
    c = datetime.fromisoformat(cross)
    near = [(w, r) for w, _, r in windows if r is not None
            and -before_h <= (datetime.fromisoformat(w) - c).total_seconds() / 3600 <= after_h]
    if not near:
        return False, f"crossing window {cross}, but no window with rain_1h within -{before_h:g}/+{after_h:g} h"
    peak_w, peak_r = max(near, key=lambda x: x[1])
    lead = (c - datetime.fromisoformat(peak_w)).total_seconds() / 3600
    ok = peak_r > 0 and lead > 0
    return ok, (f"wettest hour {peak_r:.2f} mm in window {peak_w}, first stage at {level:g} ft in window {cross}: "
                f"rain peak {lead:+.0f} h before the crossing ({len(near)} windows with rain)")


def verdict(results: list[tuple[str, str, bool, str]], manual: int) -> tuple[bool, str]:
    failed = [r for r in results if not r[2]]
    text = f"{len(results) - len(failed)} of {len(results)} checks passed"
    if failed:
        return False, "FAIL: " + text + "; failed: " + ", ".join(r[1] for r in failed) + f"; {manual} manual step(s) open"
    return True, "PASS: " + text + f"; {manual} manual step(s) still need a person"


# ---- checks: each returns (passed, what was measured) --------------------------------------------------------------

def _query(sql: str, args=()) -> list[tuple]:
    import psycopg2
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def _one(sql: str, args=()):
    return _query(sql, args)[0][0]


def check_brokers():
    from tools.fault_drill import brokers_up, under_replicated
    n, ur = brokers_up(), under_replicated()
    return n == 3 and ur == 0, f"{n} brokers up, {ur} under-replicated partitions"


def check_topics():
    from tools.fault_drill import admin
    md = admin().list_topics(timeout=10).topics
    gone = missing(TOPICS, md)
    rf = {len(p.replicas) for t in TOPICS if t in md for p in md[t].partitions.values()}
    return not gone and rf == {config.TOPIC_REPLICATION_FACTOR}, gone or f"{len(TOPICS)} topics, replication factor {sorted(rf)}"


def check_schema():
    t1, v1 = declared_objects((ROOT / "storage" / "schema.sql").read_text(encoding="utf-8"))
    t2, v2 = declared_objects((ROOT / "storage" / "views.sql").read_text(encoding="utf-8"))
    tables = {r[0] for r in _query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")}
    views = {r[0] for r in _query("SELECT table_name FROM information_schema.views WHERE table_schema = 'public'")}
    gone = missing((t1 | t2) | (v1 | v2), tables | views)
    return not gone, gone or f"{len(tables)} tables, {len(views)} views"


def check_groups():
    from tools.fault_drill import group_members
    got = {g: group_members(g) for g in GROUPS}
    return all(got[g] >= n for g, n in GROUPS.items()), ", ".join(f"{g} {got[g]}/{n}" for g, n in GROUPS.items())


def check_replay_windows():
    n, filled, live = _query("SELECT count(*), count(*) FILTER (WHERE n_readings > 0), count(*) FILTER (WHERE mode <> 'SIMULATION') "
                             "FROM windowed_aggregates WHERE site_no = %s AND window_start >= %s AND window_start < %s", (DRWT2, *FLOOD))[0]
    return filled > 0 and live == 0, f"{n} windows of the May 2019 replay in PostgreSQL, {filled} with readings, {live} not marked SIMULATION"


def check_one_forecast_per_window():
    n, without, extra = _query(
        """SELECT count(*), count(*) FILTER (WHERE r.n = 0), count(*) FILTER (WHERE r.n > 1) FROM windowed_aggregates w,
           LATERAL (SELECT count(*) AS n FROM risk_scores f WHERE f.site_no = w.site_no AND f.window_start = w.window_start
                    AND f.mode = w.mode AND f.model = 'rise_rate') r
           WHERE w.site_no = %s AND w.mode = 'SIMULATION' AND w.window_start >= %s AND w.window_start < %s""", (DRWT2, *FLOOD))[0]
    return n > 0 and without == 0 and extra == 0, f"{n} windows: {without} without a rise_rate forecast, {extra} with more than one"


def check_alert_and_v1():
    rows = _query("SELECT a.alert_id, count(v.*) FROM alerts a LEFT JOIN advisories v ON v.alert_id = a.alert_id AND v.version = 1 "
                  "AND v.source = 'template' WHERE a.site_no = %s AND a.mode = 'SIMULATION' AND a.alert_id LIKE %s AND a.status <> 'retracted' GROUP BY 1",
                  (DRWT2, f"{DRWT2}|%|2019-05-%"))
    with_v1 = sum(1 for _, n in rows if n)
    return with_v1 > 0, f"{len(rows)} alert(s) of the May 2019 replay, {with_v1} with a template v1 advisory"


def check_alert_once_per_episode():
    """Runbook step 10. The rise-rate baseline alerts late (a measured finding): the lead time is reported, negative allowed."""
    from streams.forecaster import load_gauges
    minor = load_gauges()[DRWT2]["levels"]["minor"]
    alerts = _query("SELECT alert_id, COALESCE(details->>'kind', 'new') FROM alerts WHERE site_no = %s AND mode = 'SIMULATION' AND alert_id LIKE %s ORDER BY 1",
                    (DRWT2, f"{DRWT2}|%|2019-05-%"))
    windows = [(w.isoformat(), s) for w, s in _query("SELECT window_start, stage_last FROM windowed_aggregates WHERE site_no = %s "
                                                     "AND mode = 'SIMULATION' AND window_start >= %s AND window_start < %s ORDER BY 1", (DRWT2, *FLOOD))]
    return episode_lead_times(alerts, windows, minor)


def _iso(ts: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()


def check_delivered_after_approval():
    sent, unapproved = _query("SELECT count(*) FILTER (WHERE delivered_at IS NOT NULL AND approved_at IS NOT NULL AND approved_by IS NOT NULL), "
                              "count(*) FILTER (WHERE delivered_at IS NOT NULL AND approved_at IS NULL) FROM advisories")[0]
    return sent > 0 and unapproved == 0, f"{sent} advisories approved by a person and delivered, {unapproved} delivered without approval"


def check_sink_matches():
    r = subprocess.run([PY, "-m", "tools.check_sink", "--mode", "SIMULATION"], cwd=ROOT, capture_output=True, text=True, errors="replace")
    return r.returncode == 0, (r.stdout.strip().splitlines() or ["no output"])[-1]


def check_no_duplicate_windows():
    r = subprocess.run([PY, "-m", "tools.check_windows", "--mode", "SIMULATION", "--site", DRWT2], cwd=ROOT, capture_output=True, text=True, errors="replace")
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return last.startswith("PASS"), last


def check_drills():
    rows = _query("SELECT run_label, recovery_time_s, notes FROM evaluation_runs WHERE run_label LIKE 'fault_drill:%%' ORDER BY created_at, run_id")
    return drills_verdict(latest_drills(rows))


def check_console():
    with urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=5) as r:
        st = json.load(r)
    page = (ROOT / "dashboard" / "flow.html").read_text(encoding="utf-8")
    tabs = [t for t in ("Pipeline flow", "Map", "Readings &amp; alerts", "Metrics", "Agents") if re.search(rf">\s*{t}\s*<", page)]
    gone = missing(("Pipeline flow", "Map", "Readings &amp; alerts", "Metrics", "Agents"), tabs)
    return not gone and not st.get("demo"), gone or f"console answering on live data, tabs: {len(tabs)}"


def check_files():
    gone = missing(("scripts/start_demo.ps1", "scripts/stop_demo.ps1", "dashboard/powerbi/README.md", "docs/DEMO.md", "tools/fault_drill.py"),
                   [p for p in ("scripts/start_demo.ps1", "scripts/stop_demo.ps1", "dashboard/powerbi/README.md", "docs/DEMO.md", "tools/fault_drill.py") if (ROOT / p).exists()])
    return not gone, gone or "demo scripts, Power BI guide and DEMO.md present"


def check_pytest():
    r = subprocess.run([PY, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True, errors="replace")
    return r.returncode == 0, (r.stdout.strip().splitlines() or ["no output"])[-1]


def check_predict_contract():
    from ml.predict import predict
    res = predict({"site_no": DRWT2, "stage_last": 4.0, "rise_rate_ft_per_h": 0.5, "history": [], "official_levels": {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}, "horizon_h": 3})
    gone = missing(("forecast_stage", "p_action", "p_minor", "p_moderate", "p_major", "horizon_h", "model"), res)
    return not gone, gone or f"model {res['model']}, forecast {res['forecast_stage']} ft"


def check_baseline_rows():
    n = _one("SELECT count(*) FROM evaluation_runs WHERE run_label NOT LIKE 'fault_drill:%%' AND model IN ('rise_rate', 'persistence')")
    return n > 0, f"{n} baseline rows in this database's evaluation_runs"


def check_agent_events():
    runs, events = _query("SELECT count(DISTINCT run_id), count(*) FROM agent_events")[0]
    return runs > 0, f"{runs} agent runs, {events} events"


def check_agent_v2():
    n = _one("SELECT count(*) FROM advisories WHERE source = 'agents' AND version >= 2")
    return n > 0, f"{n} v2+ advisories from the agents"


def _rain_windows(site=AHOT2, span=RAIN_EVENT):
    return [(w.isoformat(), st, r) for w, st, r in _query(
        "SELECT window_start, stage_max, rain_1h FROM windowed_aggregates WHERE site_no = %s AND mode = 'SIMULATION' "
        "AND window_start >= %s AND window_start < %s ORDER BY 1", (site, *span))]


def check_rain_topic_replay():
    from tools.check_sink import read_topic
    from common.timeutil import to_epoch
    t0, t1 = to_epoch(RAIN_EVENT[0]), to_epoch(RAIN_EVENT[1])
    recs = [m for m in read_topic(config.TOPIC_REPLAY_RAIN) if m.get("type") == "rain" and m.get("site_no") == AHOT2
            and t0 < float(m["hour_end_ts"]) <= t1]
    hours = {int(m["hour_end_ts"]) for m in recs}
    live = sum(1 for m in recs if m.get("mode") != "SIMULATION")
    return len(hours) > 0 and live == 0, (f"replay.rain: {len(recs)} AHOT2 records, {len(hours)} distinct hours of "
                                          f"{int((t1 - t0) // 3600)} in the replay window, {live} not SIMULATION")


def check_rain_rows():
    n, nn, live = _query("SELECT count(*), count(rain_mm), count(*) FILTER (WHERE mode <> 'SIMULATION') FROM catchment_rain "
                         "WHERE site_no = %s AND window_end > %s AND window_end <= %s AND mode = 'SIMULATION'", (AHOT2, *RAIN_EVENT))[0]
    return n > 0 and live == 0, f"{n} AHOT2 rain hours in catchment_rain ({nn} with radar coverage)"


def check_windows_carry_rain():
    """Windows carry rain, and each window's rain_1h equals catchment_rain for the hour ending at the window end."""
    n, with_rain, mismatch = _query(
        """SELECT count(*), count(w.rain_1h), count(*) FILTER (WHERE w.rain_1h IS NOT NULL AND c.rain_mm IS NOT NULL
                  AND abs(w.rain_1h - c.rain_mm) > 0.001)
           FROM windowed_aggregates w LEFT JOIN catchment_rain c ON c.site_no = w.site_no AND c.mode = w.mode
                AND c.window_end = w.window_end AND c.accum_hours = 1
           WHERE w.site_no = %s AND w.mode = 'SIMULATION' AND w.window_start >= %s AND w.window_start < %s""",
        (AHOT2, *RAIN_EVENT))[0]
    return with_rain > 0 and mismatch == 0, f"{n} AHOT2 windows, {with_rain} with rain_1h, {mismatch} differ from catchment_rain"


def check_rain_before_stage():
    from streams.forecaster import load_gauges
    minor = load_gauges()[AHOT2]["levels"]["minor"]
    return rain_before_stage(_rain_windows(), minor)


def check_no_duplicate_windows_ahot2():
    r = subprocess.run([PY, "-m", "tools.check_windows", "--mode", "SIMULATION", "--site", AHOT2], cwd=ROOT,
                       capture_output=True, text=True, errors="replace")
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return last.startswith("PASS"), last


CHECKS = {"S1": [
    ("core", "tests green", check_pytest),
    ("core", "3 brokers, nothing under-replicated", check_brokers),
    ("core", "9 topics at replication factor 3", check_topics),
    ("core", "tables and views from storage/*.sql", check_schema),
    ("core", "windowing, sink, forecaster, notifier running", check_groups),
    ("core", "May 2019 replay windows in PostgreSQL", check_replay_windows),
    ("core", "one forecast per window", check_one_forecast_per_window),
    ("core", "alert with template v1 advisory", check_alert_and_v1),
    ("core", "one alert per episode, lead time reported", check_alert_once_per_episode),
    ("core", "delivery only after human approval", check_delivered_after_approval),
    ("core", "topics match PostgreSQL", check_sink_matches),
    ("core", "no window emitted twice", check_no_duplicate_windows),
    ("core", "all six fault drills passed", check_drills),
    ("core", "console with all tabs", check_console),
    ("core", "demo scripts and guides exist", check_files),
    ("ml", "ml.predict contract", check_predict_contract),
    ("ml", "baseline rows in evaluation_runs", check_baseline_rows),
    ("agents", "agent runs in agent_events", check_agent_events),
    ("agents", "agents' v2 advisory saved", check_agent_v2),
], "S2": [
    ("core", "tests green", check_pytest),
    ("core", "replay.rain has the AHOT2 replay hours", check_rain_topic_replay),
    ("core", "rain hours in catchment_rain", check_rain_rows),
    ("core", "windows carry rain (= catchment_rain)", check_windows_carry_rain),
    ("core", "rain peaks before the stage crosses minor (AHOT2 2023-10-26)", check_rain_before_stage),
    ("core", "topics match PostgreSQL (incl. catchment_rain)", check_sink_matches),
    ("core", "no window emitted twice (AHOT2)", check_no_duplicate_windows_ahot2),
]}


def run_check_safely(fn) -> tuple[bool, str]:
    try:
        ok, detail = fn()
        return bool(ok), detail
    except Exception as e:  # noqa: BLE001 - a stack that is down is a FAIL, not a crash
        return False, f"{type(e).__name__}: {str(e).strip()[:160]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slice")
    ap.add_argument("--part", choices=("core", "ml", "agents"))
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    if a.slice not in CHECKS:
        print(f"{a.slice}: no checks yet (built so far: {', '.join(CHECKS)})")
        sys.exit(2)
    checks = [c for c in CHECKS[a.slice] if (not a.part or c[0] == a.part) and not (a.skip_tests and c[2] is check_pytest)]
    manual = MANUAL.get(a.slice, []) if a.part in (None, "core") else []
    results = []
    with Task(f"verify_slice {a.slice}" + (f" ({a.part})" if a.part else ""), total=len(checks), unit=" checks") as t:
        for i, (part, name, fn) in enumerate(checks, 1):
            ok, detail = run_check_safely(fn)
            results.append((part, name, ok, detail))
            t.update(i, f"{'PASS' if ok else 'FAIL'} [{part}] {name}: {detail}")
        for m in manual:
            print(f"  MANUAL {m}", flush=True)
        passed, text = verdict(results, len(manual))
        t.summary(text)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
