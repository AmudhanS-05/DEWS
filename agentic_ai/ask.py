"""Ask the database: a chat agent for the console that answers questions about gauges, rivers, past floods, alerts and
advisories. The LLM picks from fixed read-only queries (the same loop as the investigator); it never writes SQL, every
value it supplies is bound as a parameter, and the connection is read-only.

    python -m agentic_ai.ask "which floods has Shoal Creek had?"
"""
from __future__ import annotations

import json
import sys

from agentic_ai import investigator

MAX_CALLS = 5
TOOLS = {
    "find_gauges": ("gauges whose name (it carries the river or creek), NWS id or USGS site number matches a word", {"query": "text"}),
    "gauge_levels": ("a gauge's official flood stages (action, minor, moderate, major, in ft) and median rise time", {"site_no": "text"}),
    "past_floods": ("official flood crossings recorded at a gauge: date, category, peak stage, rise time", {"site_no": "text"}),
    "high_water_days": ("days a gauge stood at or above its action stage in the stored hourly windows, highest stage first; mode LIVE or SIMULATION",
                        {"site_no": "text", "mode": "text"}),
    "alerts": ("alerts raised for a gauge, newest first: time, category, probability, status, mode", {"site_no": "text"}),
    "advisories": ("advisories written for a gauge, newest first: version, source, status, start of the text", {"site_no": "text"}),
}
SYSTEM = ("You answer questions from a flood duty officer about river gauges in this database (USGS gauges with official "
          "NWS flood stages). Look things up with the tools: start with find_gauges when the question names a river, creek, "
          "place or NWS id. Answer only from tool results; if they hold nothing, say the database has no record of it. "
          "Each answer line is one short sentence naming the gauge and the date, with its number and unit.")
DONE = '{"done": true, "findings": ["<answer line>", ...]}  to answer (at most 6 lines).'

QUERIES = {
    "find_gauges": ("SELECT site_no, nwps_lid, name FROM gauges WHERE name ILIKE %(q)s OR nwps_lid ILIKE %(q)s OR site_no = %(raw)s "
                    "ORDER BY name LIMIT 10"),
    "gauge_levels": "SELECT site_no, nwps_lid, name, action_stage, minor_stage, moderate_stage, major_stage, median_rise_h FROM gauges WHERE site_no = %(raw)s",
    "past_floods": ("SELECT crossing_time, category, peak_stage_ft, rise_time_h FROM past_floods WHERE site_no = %(raw)s "
                    "ORDER BY crossing_time DESC LIMIT 15"),
    "high_water_days": ("SELECT date_trunc('day', w.window_start)::date AS day, max(w.stage_last) AS max_stage_ft, w.mode FROM windowed_aggregates w "
                        "JOIN gauges g USING (site_no) WHERE w.site_no = %(raw)s AND (%(mode)s = '' OR w.mode = upper(%(mode)s)) "
                        "AND g.action_stage IS NOT NULL AND w.stage_last >= g.action_stage GROUP BY 1, 3 ORDER BY 2 DESC LIMIT 10"),
    "alerts": ("SELECT raised_at, category, probability, horizon_h, status, mode FROM alerts WHERE site_no = %(raw)s "
               "ORDER BY raised_at DESC LIMIT 10"),
    "advisories": ("SELECT a.created_at, a.version, a.source, a.status, left(a.text, 200) AS text FROM advisories a JOIN alerts l USING (alert_id) "
                   "WHERE l.site_no = %(raw)s ORDER BY a.created_at DESC LIMIT 5"),
}


def run_query(conn, name: str, args: dict) -> list[dict]:
    raw = (args.get("query") or args.get("site_no") or "").strip()
    params = {"raw": raw, "q": f"%{raw}%", "mode": (args.get("mode") or "").strip()}
    with conn.cursor() as cur:
        cur.execute(QUERIES[name], params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def connect():
    import psycopg2

    from common import config
    conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def answer(question: str, complete=None, conn=None) -> dict:
    """{"answer": [lines], "steps": [tool, args, why, rows], "tokens", "stopped"}. Never raises."""
    from agentic_ai import llm
    complete = complete or llm.complete
    own = conn is None
    try:
        conn = conn or connect()
    except Exception as e:  # noqa: BLE001
        return {"answer": [], "steps": [], "tokens": 0, "stopped": "no_database", "error": str(e).splitlines()[0][:120]}
    try:
        res = investigator.react(complete, SYSTEM, "QUESTION: " + question.strip()[:500], TOOLS,
                                 lambda name, args: run_query(conn, name, args), MAX_CALLS, DONE)
    finally:
        if own:
            conn.close()
    steps = [{"tool": s["tool"], "args": s.get("args"), "why": s.get("why"), "rejected": s.get("rejected"),
              "rows": len(s["result"]) if isinstance(s.get("result"), list) else None} for s in res["steps"]]
    return {"answer": res["findings"], "steps": steps, "tokens": res["tokens"], "stopped": res["stopped"], "error": res.get("error")}


if __name__ == "__main__":
    from agentic_ai.env import load_env
    load_env()
    print(json.dumps(answer(" ".join(sys.argv[1:]) or "which floods has Shoal Creek had?"), indent=2, default=str))
