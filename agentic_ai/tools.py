"""Agent tools as plain functions on PostgreSQL (docs/CONTRACTS.md, "Agent tools").

Read-only: get_alert, get_recent_windows, get_forecast, get_official_levels, get_past_floods, get_rain,
get_neighbours, get_gauge_info, get_impacts, get_episode_history. Write (agents only): save_advisory,
log_agent_run, log_agent_event. Nothing here touches Kafka or sends email.

Every read takes optional `until` (ISO string or datetime) and `mode`. For a replayed flood "now" is the alert's
`raised_at`, not the wall clock, and SIMULATION rows must not mix with LIVE ones.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg2.extras import Json, RealDictCursor

from agentic_ai import rules
from agentic_ai.db import connect

GAUGE_CSV = Path(__file__).resolve().parents[1] / "config" / "gauge_locations.csv"


def _iso(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return v


def _row(r: dict | None) -> dict | None:
    return None if r is None else {k: _iso(v) for k, v in r.items()}


def _ts(until) -> datetime | None:
    if until is None:
        return None
    if isinstance(until, datetime):
        return until
    return datetime.fromisoformat(str(until).replace("Z", "+00:00"))


class Tools:
    """The tool set bound to one database connection. The graph only ever calls these methods."""

    def __init__(self, conn=None, dsn: str | None = None):
        self.conn = conn or connect(dsn)

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)

    # ------------------------------------------------------------------ read-only
    def get_official_levels(self, site: str) -> dict:
        """Official NWPS categories (never invented): {action, minor, moderate, major, units, source}."""
        rows = self._q("SELECT action_stage, minor_stage, moderate_stage, major_stage, stage_units "
                       "FROM gauges WHERE site_no = %s", (site,))
        if rows:
            r = rows[0]
            return {"action": r["action_stage"], "minor": r["minor_stage"], "moderate": r["moderate_stage"],
                    "major": r["major_stage"], "units": r["stage_units"] or "ft", "source": "gauges table"}
        info = _csv_gauge(site)
        if info:
            return {c: _f(info[c]) for c in rules.CATEGORIES} | {"units": info["units"], "source": "gauge_locations.csv"}
        return {}

    def get_gauge_info(self, site: str) -> dict:
        info = _csv_gauge(site) or {}
        rows = self._q("SELECT name, nwps_lid, median_rise_h FROM gauges WHERE site_no = %s", (site,))
        g = rows[0] if rows else {}
        return {"site_no": site, "name": g.get("name") or info.get("name"), "lid": g.get("nwps_lid") or info.get("lid"),
                "county": info.get("county"), "state": info.get("state"),
                "lat": _f(info.get("lat")), "lon": _f(info.get("lon")), "median_rise_h": g.get("median_rise_h")}

    def get_recent_windows(self, site: str, hours: int = 12, until=None, mode: str | None = None) -> list[dict]:
        """Hourly windows for the gauge, oldest first, ending at `until` (default: latest available)."""
        u = _ts(until)
        rows = self._q(
            "SELECT window_start, window_end, mode, n_readings, stage_last, stage_max, stage_min, "
            "rise_rate_ft_per_h, rain_1h, rain_3h, rain_6h, provisional FROM windowed_aggregates "
            "WHERE site_no = %s AND (%s::timestamptz IS NULL OR window_end <= %s) "
            "AND (%s::text IS NULL OR mode = %s) ORDER BY window_start DESC LIMIT %s",
            (site, u, u, mode, mode, int(hours)))
        return [_row(r) for r in reversed(rows)]

    def get_forecast(self, site: str, until=None, mode: str | None = None) -> dict | None:
        """Latest forecast row (risk_scores) at or before `until`."""
        u = _ts(until)
        rows = self._q(
            "SELECT window_start, mode, model, horizon_h, forecast_stage, p_action, p_minor, p_moderate, p_major "
            "FROM risk_scores WHERE site_no = %s AND (%s::timestamptz IS NULL OR window_start + interval '1 hour' <= %s) "
            "AND (%s::text IS NULL OR mode = %s) ORDER BY window_start DESC, id DESC LIMIT 1",
            (site, u, u, mode, mode))
        return _row(rows[0]) if rows else None

    def get_past_floods(self, site: str) -> dict:
        """Summary of past floods at the gauge. n = 0 when the table is not loaded: say so, do not guess."""
        rows = self._q("SELECT category, peak_stage_ft, rise_time_h, crossing_time FROM past_floods "
                       "WHERE site_no = %s ORDER BY crossing_time DESC", (site,))
        med = self._q("SELECT median_rise_h FROM gauges WHERE site_no = %s", (site,))
        return {"n": len(rows), "median_rise_h": med[0]["median_rise_h"] if med else None,
                "recent": [_row(r) for r in rows[:5]]}

    def get_rain(self, site: str, hours: int = 6, until=None, mode: str | None = None) -> dict:
        """Catchment radar rain from the windows (rain_1h/3h/6h). `available` is False before rain is joined (S2)."""
        w = self.get_recent_windows(site, hours, until, mode)
        with_rain = [x for x in w if x.get("rain_1h") is not None]
        if not with_rain:
            return {"available": False, "rain_1h": None, "rain_3h": None, "rain_6h": None, "hourly_mm": []}
        last = with_rain[-1]
        return {"available": True, "rain_1h": last["rain_1h"], "rain_3h": last["rain_3h"], "rain_6h": last["rain_6h"],
                "hourly_mm": [x["rain_1h"] for x in with_rain], "as_of": last["window_end"]}

    def get_neighbours(self, site: str, until=None, mode: str | None = None, n: int = 3, max_km: float = 60.0) -> list[dict]:
        """Known upstream/downstream gauges first, then the nearest others, each with its recent stage change."""
        me = _csv_gauge(site)
        topo = rules.TOPOLOGY.get(site, {"upstream": [], "downstream": []})
        picks: list[tuple[str, str, float | None]] = []
        for rel in ("upstream", "downstream"):
            for s in topo[rel]:
                picks.append((s, rel, _dist_km(me, _csv_gauge(s))))
        if me:
            near = sorted(((_dist_km(me, g), g["site_no"]) for g in _csv_all() if g["site_no"] != site
                           and g["site_no"] not in {p[0] for p in picks}), key=lambda t: t[0])
            for d, s in near:
                if d <= max_km and len(picks) < n + len(topo["upstream"]) + len(topo["downstream"]):
                    picks.append((s, "nearby", d))
        out = []
        for s, rel, d in picks:
            w = self.get_recent_windows(s, 4, until, mode)
            stages = [x["stage_last"] for x in w if x.get("stage_last") is not None]
            change = round(stages[-1] - stages[0], 2) if len(stages) >= 2 else None
            out.append({"site_no": s, "name": (_csv_gauge(s) or {}).get("name"), "relation": rel,
                        "distance_km": None if d is None else round(d, 1),
                        "stage_last": stages[-1] if stages else None, "change_ft_over_window": change,
                        "hours_in_window": len(stages),
                        "rising": None if change is None else change >= rules.CONTEXT_UPSTREAM_RISE_FT_3H})
        return out

    def get_impacts(self, site: str, level: str | float) -> list[str]:
        """NWS impact statements at or below the given category or stage. [] when none are loaded."""
        if isinstance(level, str):
            stage = self.get_official_levels(site).get(level)
            if stage is None:
                return []
        else:
            stage = float(level)
        rows = self._q("SELECT statement FROM gauge_impacts WHERE site_no = %s AND stage_ft <= %s "
                       "ORDER BY stage_ft DESC LIMIT 3", (site, stage))
        return [r["statement"] for r in rows]

    def get_alert(self, alert_id: str) -> dict | None:
        """The alert as `alerts.flood` carried it, rebuilt from the tables (kind, levels, current stage, forecast)."""
        rows = self._q("SELECT * FROM alerts WHERE alert_id = %s", (alert_id,))
        if not rows:
            return None
        a = rows[0]
        site = a["site_no"]
        window = self.get_recent_windows(site, 1, a["raised_at"], a["mode"])
        fc = self.get_forecast(site, a["raised_at"], a["mode"])
        gi = self.get_gauge_info(site)
        episode = alert_id.split("|")[-1]
        history = self.get_episode_history(alert_id)["alerts"]
        earlier = [h for h in history if h["raised_at"] < _iso(a["raised_at"])]
        return {"alert_id": alert_id, "kind": "upgrade" if earlier else "new", "site_no": site,
                "site_name": gi["name"], "mode": a["mode"], "category": a["category"],
                "probability": a["probability"], "horizon_h": a["horizon_h"],
                "forecast_stage": fc["forecast_stage"] if fc else None,
                "current_stage": window[-1]["stage_last"] if window else None,
                "official_levels": self.get_official_levels(site),
                "provisional_data": a["provisional_data"], "model": fc["model"] if fc else None,
                "raised_at": _iso(a["raised_at"]), "episode_start": episode, "status": a["status"]}

    def get_open_alerts(self, mode: str | None = None) -> list[dict]:
        rows = self._q("SELECT alert_id, site_no, category, mode, raised_at, status FROM alerts "
                       "WHERE status = 'draft' AND (%s::text IS NULL OR mode = %s) ORDER BY raised_at", (mode, mode))
        return [_row(r) for r in rows]

    def get_episode_history(self, alert_id: str) -> dict:
        """Alerts of the same flood episode (same gauge and episode start, any category) and their advisories."""
        parts = alert_id.split("|")
        site, episode = parts[0], parts[-1]
        alerts = self._q("SELECT alert_id, category, probability, raised_at, status FROM alerts "
                         "WHERE site_no = %s AND alert_id LIKE %s ORDER BY raised_at", (site, f"{site}|%|{episode}"))
        ids = [a["alert_id"] for a in alerts]
        advs = self._q("SELECT advisory_id, alert_id, version, source, status, confidence, reviewer_verdict, "
                       "edited_text, created_at FROM advisories WHERE alert_id = ANY(%s) ORDER BY created_at, version",
                       (ids,)) if ids else []
        return {"alerts": [_row(a) for a in alerts], "advisories": [_row(a) for a in advs]}

    # ------------------------------------------------------------------ writes (agents only)
    def save_advisory(self, alert_id: str, text: str, confidence: str = "normal",
                      reviewer_verdict: str | None = None, source: str = "agents", version: int | None = None) -> dict:
        """Write the agents' version (2+) as a draft; earlier undecided agent drafts become superseded."""
        if confidence not in ("normal", "uncertain"):
            raise ValueError("confidence must be normal or uncertain")
        if version is None:
            r = self._q("SELECT COALESCE(MAX(version), 1) AS v FROM advisories WHERE alert_id = %s", (alert_id,))
            version = max(2, r[0]["v"] + 1) if source == "agents" else r[0]["v"] + 1
        adv_id = f"{alert_id}|{version}"
        self._x("UPDATE advisories SET status = 'superseded' WHERE alert_id = %s AND source = 'agents' "
                "AND status = 'draft' AND version <> %s", (alert_id, version))
        self._x("INSERT INTO advisories (advisory_id, alert_id, version, source, text, confidence, status, reviewer_verdict) "
                "VALUES (%s,%s,%s,%s,%s,%s,'draft',%s) ON CONFLICT (advisory_id) DO UPDATE SET text = EXCLUDED.text, "
                "confidence = EXCLUDED.confidence, reviewer_verdict = EXCLUDED.reviewer_verdict, status = 'draft'",
                (adv_id, alert_id, version, source, text, confidence, reviewer_verdict))
        return {"advisory_id": adv_id, "version": version}

    def log_agent_run(self, run_id: str, alert_id: str, agent: str, input_summary: str = "", output=None,
                      tool_calls=None, started_at=None, ended_at=None, tokens: int | None = None,
                      verdict: str | None = None) -> None:
        self._x("INSERT INTO agent_runs (run_id, alert_id, agent, input_summary, output, tool_calls, started_at, "
                "ended_at, tokens, verdict) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, alert_id, agent, input_summary, Json(output), Json(tool_calls), started_at, ended_at,
                 tokens, verdict))

    def log_agent_event(self, run_id: str, alert_id: str, agent: str, kind: str, detail: dict | None = None,
                        ts: datetime | None = None) -> None:
        self._x("INSERT INTO agent_events (run_id, alert_id, agent, kind, detail, ts) "
                "VALUES (%s,%s,%s,%s,%s, COALESCE(%s, now()))", (run_id, alert_id, agent, kind, Json(detail or {}), ts))


# ---------------------------------------------------------------------- gauge csv helpers
_CSV_CACHE: list[dict] | None = None


def _csv_all() -> list[dict]:
    global _CSV_CACHE
    if _CSV_CACHE is None:
        with open(GAUGE_CSV, newline="", encoding="utf-8") as f:
            _CSV_CACHE = list(csv.DictReader(f))
    return _CSV_CACHE


def _csv_gauge(site: str) -> dict | None:
    return next((g for g in _csv_all() if g["site_no"] == site), None)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dist_km(a: dict | None, b: dict | None) -> float | None:
    if not a or not b:
        return None
    la1, lo1, la2, lo2 = (math.radians(_f(x)) for x in (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))
