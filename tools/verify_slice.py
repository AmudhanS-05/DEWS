"""Check that a slice is really done, against the running stack (C1.8, runbook step 15).

    python -m tools.verify_slice S1                  # all workstreams
    python -m tools.verify_slice S1 --part core      # only your part (core | ml | agents)
    python -m tools.verify_slice S1 --skip-tests     # without the pytest run

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
SCENARIOS = ("faust", "broker", "sink", "postgres", "notifier", "producer", "llm")
S1_SCENARIOS = SCENARIOS[:-1]
GROUPS = {config.GROUP_WINDOWING: 3, config.GROUP_SINK: 1, config.GROUP_FORECASTER: 1, config.GROUP_NOTIFIER: 1}
TOPICS = {config.TOPIC_GAUGE, config.TOPIC_RAIN, config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN, config.TOPIC_AGG,
          config.TOPIC_FORECASTS, config.TOPIC_ALERTS, config.TOPIC_APPROVED, config.TOPIC_DLQ}
MANUAL = {"S1": ["Power BI page 1: a new replayed reading appears within one page refresh (runbook step 12)",
                 "the S1 scene in docs/DEMO.md runs twice in a row",
                 "the approved email reached a person's inbox (delivered_at only says SMTP accepted it)"],
          "S3": ["Power BI page 3 built from dashboard/powerbi/README.md"],
          "S4": ["the Agents tab followed a real run live (nodes change while it runs)", "Power BI page 4 built from dashboard/powerbi/README.md"],
          "S5": ["two clean rehearsals of docs/DEMO.md on the presentation laptop", "Ollama fallback with Groq off, run by hand"]}


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
                       feed_delay_h: float = 1.0, window_h: float = 0.0) -> tuple[bool, str]:
    """alerts = (alert_id, kind); windows = (window_start, stage_last) ascending, ISO in one format. S1 asks for one `new`
    alert per episode and a measured lead time, negative allowed. Lead = first window with stage at `level` minus the window
    that raised the alert, at window resolution; net subtracts the feed delay. Alerting before the crossing is S3's bar.
    Pass readings as `windows` and window_h = 1 for the lead from when the raising window closed to the first reading at `level`."""
    from datetime import datetime
    new: dict[str, int] = {}
    for alert_id, kind in alerts:
        ep = alert_id.split("|")[2]
        new[ep] = new.get(ep, 0) + (kind in ("new", "retract"))     # a withdrawn alert's row holds its retract record: still that episode's one alert
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
        gross = (datetime.fromisoformat(cross) - datetime.fromisoformat(start)).total_seconds() / 3600 - window_h
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
                    AND f.mode = w.mode) r
           WHERE w.site_no = %s AND w.mode = 'SIMULATION' AND w.window_start >= %s AND w.window_start < %s""", (DRWT2, *FLOOD))[0]
    return n > 0 and without == 0 and extra == 0, f"{n} windows: {without} without a forecast, {extra} with more than one"


SCENE = {"08156800": "AHOT2", "08151500": "LLAT2"}                    # the main demo scene, 2023-10-25..27, with radar rain
SCENE_LIKE = "%|2023-10-%"


def check_alert_and_v1():
    rows = _query("SELECT a.alert_id, count(v.*) FROM alerts a LEFT JOIN advisories v ON v.alert_id = a.alert_id AND v.version = 1 "
                  "AND v.source = 'template' WHERE a.site_no = ANY(%s) AND a.mode = 'SIMULATION' AND a.alert_id LIKE %s AND a.status <> 'retracted' GROUP BY 1",
                  (list(SCENE), SCENE_LIKE))
    sites = {r[0].split("|")[0] for r in rows if r[1]}
    return sites == set(SCENE), f"{len(rows)} standing alert(s) of the main scene, template v1 on {', '.join(SCENE[x] for x in sorted(sites)) or 'none'}"


def check_alert_once_per_episode():
    """Runbook step 10, on the main scene. Each gauge: one alert per episode, the rule that fired, lead time gross and net, negative allowed."""
    from streams.forecaster import load_gauges
    gauges, oks, parts = load_gauges(), [], []
    for site, lid in SCENE.items():
        alerts = _query("SELECT alert_id, COALESCE(details->>'kind', 'new') FROM alerts WHERE site_no = %s AND mode = 'SIMULATION' AND alert_id LIKE %s ORDER BY 1", (site, SCENE_LIKE))
        rules = sorted({r[0] for r in _query("SELECT DISTINCT details->>'alert_rule' FROM alerts WHERE site_no = %s AND mode = 'SIMULATION' AND alert_id LIKE %s", (site, SCENE_LIKE)) if r[0]})
        readings = [(t.isoformat(), v) for t, v in _query("SELECT reading_time, value FROM gauge_readings WHERE site_no = %s AND mode = 'SIMULATION' AND parameter = %s AND reading_time >= '2023-10-25' AND reading_time < '2023-10-28' ORDER BY 1", (site, config.PARAM_STAGE))]
        ok, text = episode_lead_times(alerts, readings, gauges[site]["levels"]["minor"], window_h=1.0)
        oks.append(ok)
        parts.append(f"{lid} ({', '.join(rules) or 'no rule'}): {text}")
    return all(oks), "; ".join(parts)


def too_fast_report(alerts: int, tau, rows: list[tuple[str, float | None, float | None]], level: float) -> str:
    """rows = (window_start, stage_last, p_minor) ascending: what the model said in the last window below `level`."""
    cross = next((r for r in rows if r[1] is not None and r[1] >= level), None)
    if cross is None:
        return f"{alerts} alert(s), stage never reached {level:g} ft"
    below = [r for r in rows if r[0] < cross[0] and r[1] is not None]
    last = below[-1] if below else None
    said = f"p_minor {last[2]:.3f} in the last window below minor ({last[0]})" if last and last[2] is not None else "no p_minor below minor"
    return f"{alerts} alert(s) under the model rule; {said}, tau {tau if tau is None else round(tau, 3)}; stage first at {level:g} ft in window {cross[0]}, {cross[1]:.2f} ft"


def check_may_2019_reported():
    """DRWT2 May 2019 stays S1's end-to-end check and the example of a rise too fast to warn: forecasts must be there; an alert or none is reported, not required."""
    from ml.predict import _load_model
    from streams.forecaster import load_gauges
    rows = _query("SELECT w.window_start, w.stage_last, r.p_minor FROM windowed_aggregates w JOIN risk_scores r USING (site_no, window_start, mode) "
                  "WHERE w.site_no = %s AND w.mode = 'SIMULATION' AND w.window_start >= %s AND w.window_start < %s ORDER BY 1", (DRWT2, *FLOOD))
    n = _one("SELECT count(*) FROM alerts WHERE site_no = %s AND mode = 'SIMULATION' AND alert_id LIKE %s AND details->>'alert_rule' = 'alert_by_model'", (DRWT2, f"{DRWT2}|%|2019-05-%"))
    m = _load_model(3.0)
    return bool(rows), too_fast_report(n, m and float(m["tau"]), [(w.isoformat(), st, p) for w, st, p in rows], load_gauges()[DRWT2]["levels"]["minor"])


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
    return drills_verdict(latest_drills(rows), S1_SCENARIOS)


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


def _api(path: str):
    with urllib.request.urlopen("http://127.0.0.1:8765" + path, timeout=10) as r:
        return json.load(r)


def rain_leads(windows: list[tuple[str, float | None, float | None]], level: float) -> tuple[bool, str]:
    """windows = (window_start, stage_last, rain_1h) ascending. True if rain shows in a window before the stage first reaches `level`."""
    rain = next((w for w, _, r in windows if r is not None and r > 0), None)
    cross = next((w for w, s, _ in windows if s is not None and s >= level), None)
    if rain is None:
        return False, "no window with rain"
    if cross is None:
        return True, f"first rain in window {rain}, stage never reached {level:g} ft"
    return rain < cross, f"first rain in window {rain}, stage first at {level:g} ft in window {cross}"


RAIN_SPAN = ("2023-10-25", "2023-10-28")   # the main scene


def check_rain_rows():
    n, sim = _query("SELECT count(*), count(*) FILTER (WHERE mode = 'SIMULATION') FROM catchment_rain")[0]
    return sim > 0, f"{n} rows in catchment_rain, {sim} SIMULATION"


def check_rain_leads_stage():
    """Each main-scene gauge whose replay is in PostgreSQL. A gauge not replayed is named, not reported as "no rain"."""
    from streams.forecaster import load_gauges
    levels, oks, parts = load_gauges(), [], []
    for site, lid in SCENE.items():
        rows = _query("SELECT window_start, stage_last, rain_1h FROM windowed_aggregates WHERE site_no = %s AND mode = 'SIMULATION' AND window_start >= %s AND window_start < %s ORDER BY 1", (site, *RAIN_SPAN))
        if not rows:
            parts.append(f"{lid}: gauge not replayed")
            continue
        ok, text = rain_leads([(w.isoformat(), st, r) for w, st, r in rows], levels[site]["levels"]["minor"])
        oks.append(ok)
        parts.append(f"{lid}: {text}")
    return bool(oks) and all(oks), "; ".join(parts)


def check_rain_api():
    d = _api("/api/rain?mode=replay")
    return d.get("ok") and len(d["rain"]) > 0, f"Map rain layer has {len(d.get('rain', {}))} gauges in replay"


def check_forecasts_carry_model():
    active = json.loads((ROOT / "ml" / "models" / "active.json").read_text(encoding="utf-8"))["name"]
    used = _query("SELECT model, count(*), count(p_minor) FROM risk_scores WHERE site_no = ANY(%s) AND mode = 'SIMULATION' AND horizon_h = 3 AND window_start >= %s AND window_start < %s GROUP BY 1", (list(SCENE), *RAIN_SPAN))
    return any(m == active and p > 0 for m, _, p in used), f"main scene forecasts at 3 h {[(m, n, p) for m, n, p in used]} (model, windows, with p_minor); active {active}"


def check_trained_alert_before_crossing():
    """S3 acceptance on the main scene, under the rule the model was evaluated with. May 2019 is reported apart (check_may_2019_reported)."""
    from streams.forecaster import load_gauges
    gauges, active = load_gauges(), json.loads((ROOT / "ml" / "models" / "active.json").read_text(encoding="utf-8"))["name"]
    oks, parts = [], []
    for site, lid in SCENE.items():
        ids = [r[0] for r in _query("SELECT alert_id FROM alerts WHERE site_no = %s AND mode = 'SIMULATION' AND alert_id LIKE %s AND details->>'model' = %s", (site, SCENE_LIKE, active))]
        windows = [(w.isoformat(), st) for w, st in _query("SELECT window_start, stage_last FROM windowed_aggregates WHERE site_no = %s AND mode = 'SIMULATION' AND window_start >= %s AND window_start < %s ORDER BY 1", (site, *RAIN_SPAN))]
        ok, text = alert_before_crossing(windows, min((_iso(i.split("|")[2]) for i in ids), default=None), gauges[site]["levels"]["minor"])
        oks.append(ok)
        parts.append(f"{lid}: {text}")
    return all(oks), "; ".join(parts)


def check_detection_panel():
    det = _api("/api/metrics").get("detection") or {}
    n = len(det.get("rows", []))
    return n > 0 and det.get("active") is not None, f"{n} pooled rows on the Metrics panel, active model {(det.get('active') or {}).get('name')}"


def check_model_card():
    return (ROOT / "ml" / "MODEL_CARD.md").exists(), "ml/MODEL_CARD.md"


def check_llm_drill():
    rows = _query("SELECT status, recovery_time_s FROM evaluation_runs WHERE run_label = 'fault_drill:llm' ORDER BY created_at DESC, run_id DESC LIMIT 1")
    return bool(rows) and rows[0][0] == "passed", f"newest LLM down drill: {rows[0][0]}, {rows[0][1]:.1f} s" if rows else "no LLM down drill recorded"


def check_agents_tab_and_panel():
    a, m = _api("/api/agents"), _api("/api/metrics")
    return a.get("ok") and bool(a.get("run")) and bool(m.get("agents")), f"Agents tab run {(a.get('run') or {}).get('run_id')}, {len(a.get('runs', []))} runs to step through; {len(m.get('agents', []))} mode(s) on the Metrics agent panel"


def check_mcp_server():
    return (ROOT / "agentic_ai" / "mcp_server.py").exists(), "agentic_ai/mcp_server.py"


def check_all_drills():
    rows = _query("SELECT run_label, recovery_time_s, notes FROM evaluation_runs WHERE run_label LIKE 'fault_drill:%%' ORDER BY created_at, run_id")
    return drills_verdict(latest_drills(rows))


def check_throughput():
    rows = _query("SELECT status, throughput_msgs_s, notes FROM evaluation_runs WHERE run_label = 'throughput:replay' ORDER BY created_at DESC, run_id DESC LIMIT 1")
    return bool(rows) and rows[0][0] == "passed", f"{rows[0][1]:g} msg/s, {rows[0][0]}" if rows else "no throughput run recorded"


def check_demo_fallbacks():
    demo = (ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")
    have = {"demo checklist": "## Demo checklist" in demo, "bundled world image": (ROOT / "dashboard" / "static" / "world_bluemarble.jpg").exists(),
            "reset tool": (ROOT / "tools" / "reset_replay.py").exists(), "Power BI project": (ROOT / "dashboard" / "powerbi" / "DEWS.pbip").exists()}
    gone = [k for k, v in have.items() if not v]
    return not gone, ("missing: " + ", ".join(gone)) if gone else ", ".join(have)


CHECKS = {"S1": [
    ("core", "tests green", check_pytest),
    ("core", "3 brokers, nothing under-replicated", check_brokers),
    ("core", "9 topics at replication factor 3", check_topics),
    ("core", "tables and views from storage/*.sql", check_schema),
    ("core", "windowing, sink, forecaster, notifier running", check_groups),
    ("core", "May 2019 replay windows in PostgreSQL", check_replay_windows),
    ("core", "one forecast per window", check_one_forecast_per_window),
    ("core", "May 2019: forecasts there, alert or too fast to warn reported", check_may_2019_reported),
    ("core", "main scene: alert with template v1 advisory", check_alert_and_v1),
    ("core", "main scene: one alert per episode, rule and lead time reported", check_alert_once_per_episode),
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
    ("core", "rain rows in catchment_rain", check_rain_rows),
    ("core", "rain rises before stage in the replay", check_rain_leads_stage),
    ("core", "Map rain layer has data", check_rain_api),
], "S3": [
    ("core", "forecasts carry the active model, p_minor at 3 h", check_forecasts_carry_model),
    ("core", "main scene: trained model alerts before the minor crossing", check_trained_alert_before_crossing),
    ("core", "May 2019 reported apart: no lead under the evaluated rule, not tuned", check_may_2019_reported),
    ("core", "Metrics detection panel", check_detection_panel),
    ("ml", "model card", check_model_card),
], "S4": [
    ("core", "LLM down drill passed", check_llm_drill),
    ("core", "Agents tab and Metrics agent panel", check_agents_tab_and_panel),
    ("agents", "agent runs recorded", check_agent_events),
    ("agents", "MCP server", check_mcp_server),
], "S5": [
    ("core", "every fault drill passed, newest run", check_all_drills),
    ("core", "throughput run recorded", check_throughput),
    ("core", "demo checklist and offline fallbacks", check_demo_fallbacks),
    ("core", "tests green", check_pytest),
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
