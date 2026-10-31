"""Coordinator-Worker-Delegator graph in LangGraph (A1.3).

  coordinator -> [evidence || context] -> reconcile -> writer -> reviewer --pass--> save_advisory (v2, draft)
                                                          ^---- fail (max 2 revisions) ----'  \\-- still failing --> keep v1

Agents draft and check only. The only writes are advisories, agent_runs and agent_events, through Tools.
Any failure leaves the template v1 in place (outcome `v1_kept`). The human approval gate is the console.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agentic_ai import advisory, checks, llm, rules, tracing


class State(TypedDict, total=False):
    run_id: str
    alert_id: str
    alert: dict
    force: bool
    outcome: str            # saved | v1_kept | dropped
    drop_reason: str
    urgent: bool
    plan: list[str]
    update_n: int
    evidence: dict
    context: dict
    disagreement: dict | None
    confidence: str
    attempt: int
    feedback: list[str]
    draft: str
    reasons: list[str]
    verdict: str
    advisory_id: str
    version: int
    tokens: int
    llm_error: str | None


class Run:
    """Per-run helper: timestamps, event log (also kept in memory), run rows and traced tool calls."""

    def __init__(self, tools, run_id: str, alert_id: str, on_event=None):
        self.tools, self.run_id, self.alert_id = tools, run_id, alert_id
        self.events: list[dict] = []
        self.tool_calls: dict[str, list[dict]] = {}
        self.on_event = on_event          # optional callback (CLI progress lines); never allowed to break the run
        self.t_start = time.monotonic()
        self._agent_t0: dict[str, float] = {}

    def event(self, agent: str, kind: str, detail: dict | None = None) -> None:
        ts = datetime.now(timezone.utc)
        detail = dict(detail or {})
        now = time.monotonic()
        if kind == "start":
            self._agent_t0[agent] = now
        elif kind == "end" and agent in self._agent_t0:
            detail.setdefault("ms", round((now - self._agent_t0[agent]) * 1000))   # duration since this agent's start
        row = {"agent": agent, "kind": kind, "detail": detail, "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
               "t_s": round(now - self.t_start, 3)}
        self.events.append(row)
        self.tools.log_agent_event(self.run_id, self.alert_id, agent, kind, detail, ts)
        if self.on_event:
            try:
                self.on_event(row)
            except Exception:  # noqa: BLE001 - progress output must not change the outcome
                pass

    def call(self, agent: str, name: str, *args, **kwargs):
        t0 = time.monotonic()
        res = getattr(self.tools, name)(*args, **kwargs)
        info = {"tool": name, "args": [str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()],
                "rows": len(res) if isinstance(res, (list, dict)) else (0 if res is None else 1),
                "ms": round((time.monotonic() - t0) * 1000)}
        self.tool_calls.setdefault(agent, []).append(info)
        self.event(agent, "tool_call", info)
        return res

    def row(self, agent: str, started: datetime, input_summary: str, output: Any, tokens: int = 0, verdict: str | None = None):
        self.tools.log_agent_run(self.run_id, self.alert_id, agent, input_summary, output,
                                 self.tool_calls.pop(agent, []), started, datetime.now(timezone.utc), tokens, verdict)


def _now():
    return datetime.now(timezone.utc)


def build_graph(tools, run: Run):
    """Compile the graph. `tools` is a Tools-like object; `run` carries the event log for this alert."""

    def coordinator(s: State) -> dict:
        t0 = _now()
        run.event("coordinator", "start", {"alert_id": s["alert_id"]})
        alert = run.call("coordinator", "get_alert", s["alert_id"])
        if alert is None:
            return _drop(s, t0, "alert not found")
        if alert["kind"] == "retract":
            return _drop(s, t0, "retraction: nothing to advise")
        existing = [a for a in run.call("coordinator", "get_episode_history", s["alert_id"])["advisories"]
                    if a["alert_id"] == s["alert_id"] and a["source"] == "agents" and a["status"] in ("draft", "approved", "sent")]
        if existing and not s.get("force"):
            adv = existing[-1]["advisory_id"]
            return _drop(s, t0, f"duplicate: v{adv.rsplit('|', 1)[-1]} exists ({adv}), use --force to re-run")
        hist = run.call("coordinator", "get_episode_history", s["alert_id"])["alerts"]
        update_n = 1 + sum(1 for h in hist if h["raised_at"] < alert["raised_at"])
        urgent = alert["category"] == "major"
        plan = ["evidence", "context", "writer", "reviewer"]
        run.event("coordinator", "end", {"plan": plan, "urgent": urgent, "update_n": update_n, "mode": alert["mode"]})
        run.row("coordinator", t0, f"alert {s['alert_id']} ({alert['category']}, {alert['mode']})",
                {"plan": plan, "urgent": urgent, "update_n": update_n})
        return {"alert": alert, "urgent": urgent, "plan": plan, "update_n": update_n, "attempt": 0, "feedback": [],
                "tokens": 0, "outcome": "pending"}

    def _drop(s: State, t0, reason: str) -> dict:
        run.event("coordinator", "end", {"dropped": reason})
        run.row("coordinator", t0, f"alert {s['alert_id']}", {"dropped": reason})
        return {"outcome": "dropped", "drop_reason": reason}

    def route_after_coordinator(s: State):
        return END if s.get("outcome") == "dropped" else ["evidence", "context"]

    def evidence(s: State) -> dict:
        t0, a = _now(), s["alert"]
        run.event("evidence", "start", {"site": a["site_no"]})
        site, until, mode = a["site_no"], a["raised_at"], a["mode"]
        w = run.call("evidence", "get_recent_windows", site, 12, until=until, mode=mode)
        fc = run.call("evidence", "get_forecast", site, until=until, mode=mode)
        lv = run.call("evidence", "get_official_levels", site)
        pf = run.call("evidence", "get_past_floods", site)
        stages = [x["stage_last"] for x in w if x.get("stage_last") is not None]
        last = w[-1] if w else {}
        rate = last.get("rise_rate_ft_per_h")
        level = (lv or a["official_levels"]).get(a["category"])
        fstage = fc["forecast_stage"] if fc else a.get("forecast_stage")
        ev = {"stage_last": last.get("stage_last"), "rise_rate_ft_per_h": rate, "stage_12h_ago": stages[0] if stages else None,
              "n_windows": len(w), "forecast_stage": fstage, "horizon_h": fc["horizon_h"] if fc else a["horizon_h"],
              "p_action": fc and fc["p_action"], "p_minor": fc and fc["p_minor"], "p_moderate": fc and fc["p_moderate"],
              "p_major": fc and fc["p_major"], "model": fc["model"] if fc else a.get("model"),
              "official_levels": lv, "past_floods_n": pf["n"], "median_rise_h": pf["median_rise_h"],
              "direction": rules.evidence_direction(rate, fstage, level)}
        run.event("evidence", "end", {"direction": ev["direction"], "stage_last": ev["stage_last"], "rise_rate_ft_per_h": rate,
                                      "forecast_stage": fstage})
        run.row("evidence", t0, f"gauge {site} up to {until}", ev)
        return {"evidence": ev}

    def context(s: State) -> dict:
        t0, a = _now(), s["alert"]
        run.event("context", "start", {"site": a["site_no"]})
        site, until, mode = a["site_no"], a["raised_at"], a["mode"]
        rain = run.call("context", "get_rain", site, 6, until=until, mode=mode)
        nb = run.call("context", "get_neighbours", site, until=until, mode=mode)
        info = run.call("context", "get_gauge_info", site)
        imp = run.call("context", "get_impacts", site, a["category"])
        up = [n["rising"] for n in nb if n["relation"] == "upstream"]
        rain_3h = (rain["rain_3h"] or 0.0) if rain["available"] else None
        direction, rain_sig, up_sig = rules.context_direction(rain_3h, up)
        cx = {"rain": rain, "neighbours": nb, "gauge": info, "impacts": imp, "direction": direction,
              "rain_signal": bool(rain_sig), "upstream_signal": bool(up_sig)}
        run.event("context", "end", {"direction": direction, "rain_3h": rain.get("rain_3h"), "n_neighbours": len(nb)})
        run.row("context", t0, f"surroundings of {site} up to {until}", {k: cx[k] for k in ("direction", "rain_signal", "upstream_signal")})
        return {"context": cx}

    def reconcile(s: State) -> dict:
        """Conflict resolution: the coordinator does not let one worker win silently."""
        ev, cx = s["evidence"], s["context"]
        dis = None
        if rules.disagreement(ev["direction"], cx["direction"]):
            dis = {"evidence": ev["direction"] + f" (rise {ev['rise_rate_ft_per_h']} ft/h, forecast {ev['forecast_stage']} ft)",
                   "context": cx["direction"] + (f" (rain 3h {cx['rain']['rain_3h']} mm)" if cx["rain"]["available"] else " (upstream gauges)")}
            run.event("coordinator", "disagreement", dis)
            t0 = _now()
            run.row("coordinator", t0, "disagreement between evidence and context", dis, verdict="uncertain")
        return {"disagreement": dis, "confidence": "uncertain" if dis else "normal"}

    def writer(s: State) -> dict:
        t0, a = _now(), s["alert"]
        attempt = s["attempt"] + 1
        run.event("writer", "start", {"attempt": attempt})
        if s["feedback"]:
            run.event("writer", "revision", {"attempt": attempt, "feedback": s["feedback"]})
        facts = advisory.build_facts(a, s["evidence"], s["context"], s["update_n"])
        fallback = advisory.template_text(a, s["evidence"], s["context"], s["confidence"], s["update_n"], s["disagreement"])
        reply = llm.complete(advisory.SYSTEM_PROMPT, advisory.llm_user_prompt(facts, s["confidence"], s["disagreement"], s["feedback"]))
        text, err = (reply.text, None) if reply.text else (fallback, reply.error)
        text = checks.normalize(text)   # plain ASCII dashes/spaces in what is reviewed, saved and shown
        if reply.fallback_from:
            run.event("writer", "revision", {"llm_fallback": reply.provider, "primary": reply.fallback_from,
                                             "primary_error": reply.primary_error, "ok": bool(reply.text)})
        if reply.provider != "none" and not reply.text:
            run.event("writer", "revision", {"llm_failed": reply.error, "using": "deterministic template text"})
        for line in text.splitlines():
            run.event("writer", "token_chunk", {"text": line, "llm": bool(reply.text)})
        run.event("writer", "end", {"attempt": attempt, "max_attempts": rules.MAX_REVISIONS + 1, "chars": len(text),
                                    "provider": reply.provider, "model": reply.model, "llm": bool(reply.text),
                                    "tokens": reply.tokens, "latency_s": reply.latency_s, "fallback_from": reply.fallback_from})
        run.row("writer", t0, f"attempt {attempt}, confidence {s['confidence']}", {"text": text, "llm": bool(reply.text)}, reply.tokens)
        return {"draft": text, "attempt": attempt, "tokens": s["tokens"] + reply.tokens, "llm_error": err or s.get("llm_error")}

    def reviewer(s: State) -> dict:
        t0, a = _now(), s["alert"]
        run.event("reviewer", "start", {"attempt": s["attempt"]})
        facts = advisory.build_facts(a, s["evidence"], s["context"], s["update_n"])
        reasons = checks.review(s["draft"], a, facts, s["confidence"])
        tokens, judge = 0, None
        if not reasons and llm.provider() != "none":   # LLM judgement second, only on a draft that passed the rules
            r = llm.complete("You review flood advisories. Reply 'PASS' or 'FAIL: <one reason>'. FAIL only if the text "
                             "is misleading, certain-sounding, or inconsistent with the facts.",
                             f"FACTS:{facts}\n\nDRAFT:\n{s['draft']}")
            tokens = r.tokens
            judge = (r.text or f"no reply ({r.error})")[:200]
            if r.text and r.text.upper().startswith("FAIL"):
                reasons.append("LLM review: " + r.text[5:].strip(" :"))
        verdict = "pass" if not reasons else "fail"
        run.event("reviewer", "verdict", {"verdict": verdict, "reasons": reasons, "attempt": s["attempt"],
                                          "max_attempts": rules.MAX_REVISIONS + 1, "llm_judgement": judge})
        run.event("reviewer", "end", {"attempt": s["attempt"]})
        run.row("reviewer", t0, f"draft attempt {s['attempt']}", {"reasons": reasons}, tokens, verdict)
        return {"verdict": verdict, "reasons": reasons, "feedback": reasons, "tokens": s["tokens"] + tokens}

    def route_after_reviewer(s: State):
        if s["verdict"] == "pass":
            return "save_advisory"
        return "writer" if s["attempt"] <= rules.MAX_REVISIONS else "keep_v1"

    def save_advisory(s: State) -> dict:
        res = run.call("coordinator", "save_advisory", s["alert_id"], s["draft"], s["confidence"], "pass")
        run.event("coordinator", "end", {"saved": res["advisory_id"], "confidence": s["confidence"]})
        run.row("coordinator", _now(), "assemble final package", {"advisory_id": res["advisory_id"]}, verdict="pass")
        return {"outcome": "saved", "advisory_id": res["advisory_id"], "version": res.get("version")}

    def keep_v1(s: State) -> dict:
        run.event("coordinator", "end", {"kept": "template v1", "reasons": s["reasons"]})
        run.row("coordinator", _now(), "reviewer failed after max revisions", {"reasons": s["reasons"]}, verdict="fail")
        return {"outcome": "v1_kept"}

    g = StateGraph(State)
    for name, fn in (("coordinator", coordinator), ("evidence", evidence), ("context", context), ("reconcile", reconcile),
                     ("writer", writer), ("reviewer", reviewer), ("save_advisory", save_advisory), ("keep_v1", keep_v1)):
        g.add_node(name, fn)
    g.add_edge(START, "coordinator")
    g.add_conditional_edges("coordinator", route_after_coordinator, ["evidence", "context", END])
    g.add_edge(["evidence", "context"], "reconcile")
    g.add_edge("reconcile", "writer")
    g.add_edge("writer", "reviewer")
    g.add_conditional_edges("reviewer", route_after_reviewer, ["save_advisory", "writer", "keep_v1"])
    g.add_edge("save_advisory", END)
    g.add_edge("keep_v1", END)
    return g.compile()


def run_alert(tools, alert_id: str, force: bool = False, on_event=None) -> dict:
    """Run the graph on one alert. Never raises: any failure leaves template v1 (outcome `v1_kept`, error logged).
    `on_event(row)` is called for every agent event as it happens (CLI progress)."""
    tr = tracing.configure()
    run = Run(tools, uuid.uuid4().hex[:12], alert_id, on_event)
    app = build_graph(tools, run)
    trace_id = uuid.uuid4()   # LangChain uses it as the root run id, so the LangSmith trace can be linked
    base = {"run_id": run.run_id, "alert_id": alert_id, "trace_run_id": str(trace_id) if tr["enabled"] else None,
            "trace_project": tr["project"]}
    try:
        a = tools.get_alert(alert_id)
        cfg = {**tracing.run_config(run.run_id, alert_id, a and a["mode"]), "run_id": trace_id}
        final = app.invoke({"run_id": run.run_id, "alert_id": alert_id, "force": force}, cfg)
    except Exception as e:  # noqa: BLE001
        run.event("coordinator", "end", {"error": f"{type(e).__name__}: {str(e)[:200]}", "kept": "template v1"})
        return {**base, "outcome": "v1_kept", "error": str(e), "events": run.events,
                "elapsed_s": round(time.monotonic() - run.t_start, 2)}
    return {**base, "outcome": final.get("outcome"), "version": final.get("version"),
            "elapsed_s": round(time.monotonic() - run.t_start, 2),
            "advisory_id": final.get("advisory_id"), "confidence": final.get("confidence"),
            "verdict": final.get("verdict"), "reasons": final.get("reasons", []), "drop_reason": final.get("drop_reason"),
            "attempts": final.get("attempt"), "tokens": final.get("tokens", 0), "draft": final.get("draft"),
            "events": run.events}
