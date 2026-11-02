"""In-memory Tools over agentic_ai.seed.build() so graph tests need no PostgreSQL. Same public methods."""
import copy
from datetime import timedelta

from agentic_ai import seed
from agentic_ai.tools import Tools, _csv_gauge, _row, _ts


class FakeTools(Tools):
    def __init__(self, rain=True, flat=False):
        d = seed.build()
        self.windows = copy.deepcopy(d["windows"])
        self.risk = d["risk"]
        self.alerts = {a["alert_id"]: a for a in d["alerts"]}
        self.advisories, self.events, self.runs = [], [], []
        self.impacts: list[str] = []
        self.edits: list[dict] = []                       # officer edits (A3.3 memory)
        self.quality = {"suspect": None, "reasons": [], "source": "fake: not checked"}
        if not rain:
            for w in self.windows:
                w["rain_1h"] = w["rain_3h"] = w["rain_6h"] = None
        if flat:   # gauge reading flat and no forecast rise: the "rain but flat gauge" case
            for w in self.windows:
                if w["site_no"] == seed.SITE:
                    w["stage_last"] = 1.3
                    w["rise_rate_ft_per_h"] = 0.0
            for r in self.risk:
                if r["site_no"] == seed.SITE:
                    r["forecast_stage"] = 1.3

    def get_official_levels(self, site):
        g = _csv_gauge(site)
        return {c: float(g[c]) for c in ("action", "minor", "moderate", "major")} | {"units": "ft", "source": "fake"}

    def get_gauge_info(self, site):
        g = _csv_gauge(site)
        return {"site_no": site, "name": g["name"], "lid": g["lid"], "county": g["county"], "state": g["state"],
                "lat": float(g["lat"]), "lon": float(g["lon"]), "median_rise_h": 10.9}

    def get_recent_windows(self, site, hours=12, until=None, mode=None):
        u = _ts(until)
        rows = [w for w in self.windows if w["site_no"] == site and (u is None or w["window_end"] <= u)
                and (mode is None or w["mode"] == mode)]
        rows.sort(key=lambda w: w["window_start"])
        return [_row(r) for r in rows[-int(hours):]]

    def get_forecast(self, site, until=None, mode=None, horizon_h=None):
        u = _ts(until)
        rows = [r for r in self.risk if r["site_no"] == site and (u is None or r["window_start"].timestamp() + 3600 <= u.timestamp())
                and (mode is None or r["mode"] == mode) and (horizon_h is None or r["horizon_h"] == horizon_h)]
        rows.sort(key=lambda r: r["window_start"])
        return _row(rows[-1]) if rows else None

    def _forecast_rows(self, site, mode, start, end, horizon_h):
        s, e = _ts(start), _ts(end)
        stage = {(w["site_no"], w["window_start"], w["mode"]): w["stage_last"] for w in self.windows}
        rows = [dict(r, stage_last=stage.get((r["site_no"], r["window_start"], r["mode"]))) for r in self.risk
                if r["site_no"] == site and r["mode"] == mode and r["window_start"] >= s
                and r["window_start"] + timedelta(hours=1) <= e and (horizon_h is None or r["horizon_h"] == horizon_h)]
        rows.sort(key=lambda r: r["window_start"])
        return [_row(r) for r in rows]

    def _alert_record(self, alert_id):
        a = self.alerts.get(alert_id)
        return _row(dict(a)) if a else None

    def get_past_floods(self, site):
        return {"n": 0, "median_rise_h": 10.9, "recent": []}

    def get_edit_examples(self, site=None, mode=None, limit=2):
        return [dict(e) for e in self.edits][:limit]

    def get_data_quality(self, site, until=None, mode=None, hours=48):
        return dict(self.quality)

    def get_impacts(self, site, level):
        return list(self.impacts)

    def get_episode_history(self, alert_id):
        site, episode = alert_id.split("|")[0], alert_id.split("|")[-1]
        al = sorted((a for a in self.alerts.values() if a["site_no"] == site and a["alert_id"].endswith(episode)),
                    key=lambda a: a["raised_at"])
        ids = {a["alert_id"] for a in al}
        return {"alerts": [_row(dict(a)) for a in al], "advisories": [dict(a) for a in self.advisories if a["alert_id"] in ids]}

    def save_advisory(self, alert_id, text, confidence="normal", reviewer_verdict=None, source="agents", version=None):
        version = version or max([x["version"] for x in self.advisories if x["alert_id"] == alert_id] + [1]) + 1
        for x in self.advisories:
            if x["alert_id"] == alert_id and x["source"] == "agents" and x["status"] == "draft":
                x["status"] = "superseded"
        row = {"advisory_id": f"{alert_id}|{version}", "alert_id": alert_id, "version": version, "source": source,
               "text": text, "confidence": confidence, "status": "draft", "reviewer_verdict": reviewer_verdict}
        self.advisories.append(row)
        return {"advisory_id": row["advisory_id"], "version": version}

    def log_agent_run(self, run_id, alert_id, agent, input_summary="", output=None, tool_calls=None, started_at=None,
                      ended_at=None, tokens=None, verdict=None, trace_run_id=None):
        self.runs.append({"run_id": run_id, "agent": agent, "tokens": tokens, "verdict": verdict, "tool_calls": tool_calls,
                          "trace_run_id": trace_run_id, "output": output})

    def set_trace_url(self, run_id, trace_url):
        for r in self.runs:
            if r["run_id"] == run_id:
                r["trace_url"] = trace_url

    def log_agent_event(self, run_id, alert_id, agent, kind, detail=None, ts=None):
        self.events.append({"run_id": run_id, "agent": agent, "kind": kind, "detail": detail})
