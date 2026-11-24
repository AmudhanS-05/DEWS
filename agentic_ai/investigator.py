"""Investigator agent: the one step where the LLM chooses the tools.

After Evidence and Context, the investigator LLM reads what they found and decides which extra evidence to fetch, one
MCP tool at a time, up to MAX_CALLS; then it states its findings. Code still runs every call: the LLM picks a tool
name from a fixed read-only list and at most a few numeric arguments, and the code fills in the gauge, the alert's
time and the mode, so the LLM cannot read another gauge, the future, or anything outside the list.

The loop is plain JSON over llm.complete (no provider-specific tool calling), so it runs on Groq, Gemini or Ollama:
    LLM -> {"tool": "get_neighbours", "args": {}, "why": "..."}     code runs it, sends back a compact result
    LLM -> {"done": true, "findings": ["...", "..."]}               end
The same loop serves the console's database chat (agentic_ai.ask), with its own tool list.
"""
from __future__ import annotations

import json
import os
import re

MAX_CALLS = 4

# name -> (what it returns, the numeric args the LLM may set: {arg: (min, max, default)})
TOOLS = {
    "get_recent_windows": ("hourly windows of this gauge before the alert: stage, rise rate, readings", {"hours": (3, 48, 12)}),
    "get_rain": ("radar rain over this gauge's catchment: 1, 3 and 6 h totals", {"hours": (1, 24, 6)}),
    "get_neighbours": ("upstream, downstream and nearby gauges with their 3 h change and whether they rise", {}),
    "get_past_floods": ("past official crossings at this gauge: category, peak, rise time", {}),
    "get_data_quality": ("sensor-fault check on this gauge's recent readings", {"hours": (6, 96, 48)}),
    "get_gauge_info": ("gauge name, river, coordinates", {}),
    "get_impacts": ("official impact statements for a stage or category at this gauge", {}),
}


def mode() -> str:
    """INVESTIGATOR = always (default) | uncertain (only when signals disagree) | off."""
    v = os.getenv("INVESTIGATOR", "always").strip().lower()
    return v if v in ("always", "uncertain", "off") else "always"


def parse_action(text: str | None) -> dict | None:
    """The first JSON object in the reply (models wrap it in prose or code fences)."""
    if not text:
        return None
    for m in re.finditer(r"\{", text):
        depth = 0
        for j in range(m.start(), len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                try:
                    obj = json.loads(text[m.start():j + 1])
                    return obj if isinstance(obj, dict) else None
                except ValueError:
                    break
    return None


def compact(v, depth: int = 0):
    """A tool result small enough to send back: numbers to 2 decimals, at most 6 list items and 12 keys."""
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, dict):
        return {k: compact(x, depth + 1) for k, x in list(v.items())[:12]} if depth < 3 else "..."
    if isinstance(v, (list, tuple)):
        return [compact(x, depth + 1) for x in list(v)[-6:]] if depth < 3 else f"{len(v)} items"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def clamp_args(spec: dict, args) -> dict:
    out = {}
    for k, rule in spec.items():
        if rule == "text":                       # a short search word or id, never SQL: the tool binds it as a parameter
            out[k] = str((args or {}).get(k) or "")[:60]
            continue
        lo, hi, default = rule
        try:
            out[k] = int(min(max(float((args or {}).get(k, default)), lo), hi))
        except (TypeError, ValueError):
            out[k] = default
    return out


def react(complete, system: str, user: str, tools: dict, run_tool, max_calls: int = MAX_CALLS, done_hint: str | None = None,
          required: list[tuple[str, str]] | None = None, nudges: int = 1) -> dict:
    """The loop. run_tool(name, args) -> result. Returns steps, findings, tokens, why it stopped.
    required: (tool, reason) checks the situation calls for; if the LLM stops with one still open and budget left, it is
    reminded `nudges` times (it may still decline, saying why)."""
    steps, tokens, history = [], 0, user
    menu = "\n".join(f"- {n}: {d}" + (f" (args: {', '.join(k + (' text' if r == 'text' else f' {r[0]}-{r[1]}') for k, r in a.items())})" if a else "")
                     for n, (d, a) in tools.items())
    sys = (system + "\n\nTOOLS (read-only):\n" + menu + "\n\nReply with ONE JSON object and nothing else:\n"
           '{"tool": "<name>", "args": {...}, "why": "<one short reason>"}  to fetch one more piece of evidence, or\n'
           + (done_hint or '{"done": true, "findings": ["<short finding with its number>", ...]}  when you have enough.') + "\n"
           f"At most {max_calls} tool calls. Never call the same tool twice with the same args. Use only numbers from tool results.")
    for _ in range(max_calls + 1):
        reply = complete(sys, history)
        tokens += getattr(reply, "tokens", 0) or 0
        if reply.text is None:
            return {"steps": steps, "findings": [], "tokens": tokens, "stopped": "llm_unavailable", "error": reply.error}
        act = parse_action(reply.text)
        if not act:
            return {"steps": steps, "findings": [], "tokens": tokens, "stopped": "unparsed_reply"}
        if act.get("done") or "findings" in act:
            called = {st["tool"] for st in steps if not st.get("rejected")}
            open_checks = [(t, why) for t, why in (required or []) if t not in called]
            if open_checks and nudges > 0 and len(steps) < max_calls:
                nudges -= 1
                history += ("\n\nNOT YET: the situation checklist still has open checks: "
                            + "; ".join(f"{t} ({why})" for t, why in open_checks)
                            + ". Fetch them, or reply done again with a finding that says why they are not needed.")
                continue
            f = act.get("findings") or []
            return {"steps": steps, "findings": [str(x)[:240] for x in (f if isinstance(f, list) else [f])][:6],
                    "tokens": tokens, "stopped": "done"}
        name = act.get("tool")
        if len(steps) >= max_calls:
            return {"steps": steps, "findings": [], "tokens": tokens, "stopped": "budget"}
        if name not in tools:
            steps.append({"tool": name, "why": act.get("why"), "rejected": "not an allowed tool"})
            history += f"\n\nREJECTED {name!r}: not in TOOLS."
            continue
        args = clamp_args(tools[name][1], act.get("args"))
        if any(s["tool"] == name and s.get("args") == args for s in steps):
            steps.append({"tool": name, "args": args, "why": act.get("why"), "rejected": "repeat"})
            history += f"\n\nREJECTED {name}: already called with these args."
            continue
        try:
            res = compact(run_tool(name, args))
        except Exception as e:  # noqa: BLE001 - a failing tool is reported back, the loop goes on
            res = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        steps.append({"tool": name, "args": args, "why": str(act.get("why") or "")[:200], "result": res})
        history += f"\n\nRESULT {name}({json.dumps(args)}): {json.dumps(res, default=str)[:1500]}"
    return {"steps": steps, "findings": [], "tokens": tokens, "stopped": "budget"}


SYSTEM = ("You are the investigator in a flood-warning team. An alert was raised for one river gauge. The Evidence and "
          "Context steps already fetched the basics (below). Decide what extra evidence, if any, would most change how "
          "the advisory should read: e.g. when the gauge and the rain or upstream gauges point different ways, when the "
          "model's probability and its stage forecast disagree, when the data may be faulty, or when the category is "
          "MODERATE or MAJOR. Fetch only what helps; it is fine to stop at once. Findings are short, factual, one number "
          "each, no advice.")


def brief(alert: dict, ev: dict, cx: dict, confidence: str, disagreement: dict | None) -> str:
    """What the investigator is told: the alert and the two workers' outputs, rounded."""
    return json.dumps(compact({
        "alert": {k: alert.get(k) for k in ("site_no", "category", "mode", "horizon_h", "probability", "forecast_stage",
                                             "current_stage", "alert_rule")},
        "evidence": {k: ev.get(k) for k in ("direction", "stage_last", "rise_rate_ft_per_h", "stage_12h_ago", "forecast_stage",
                                            "p_minor", "official_levels", "past_floods_n", "data_quality")},
        "context": {k: cx.get(k) for k in ("direction", "kind", "basis", "summary")},
        "confidence": confidence, "disagreement": disagreement}), default=str)


def checklist(alert: dict, ev: dict, cx: dict, disagreement: dict | None) -> list[tuple[str, str]]:
    """Situation-specific checks a hydrologist would make, from the same facts the investigator sees (first live
    evaluation, recall 0.50: it missed impacts on MAJOR, past floods on a fast rise, recent windows on a falling gauge
    and neighbours when rain data were missing). The LLM still chooses; it is told these and reminded once."""
    out: list[tuple[str, str]] = []
    add = lambda t, why: out.append((t, why)) if t not in [x for x, _ in out] else None   # noqa: E731
    lv = ev.get("official_levels") or alert.get("official_levels") or {}
    fc = ev.get("forecast_stage") if ev.get("forecast_stage") is not None else alert.get("forecast_stage")
    rate = ev.get("rise_rate_ft_per_h")
    dq = ev.get("data_quality") or {}
    cat = str(alert.get("category") or "").lower()
    if dq.get("suspect"):
        add("get_data_quality", "readings flagged as a possible sensor fault")
    if alert.get("alert_rule") == "alert_by_model" and isinstance(fc, (int, float)) and lv.get(cat) is not None and fc < lv[cat]:
        add("get_recent_windows", "probability fired but the stage forecast stays below the category: check the recent trend")
        add("get_past_floods", "how past floods at this gauge developed")
    if disagreement and disagreement.get("kind") != "model":
        if cx.get("kind") == "rain":
            add("get_rain", "rain contradicts the gauge: check the rain totals")
        add("get_neighbours", "a leading signal contradicts the gauge: check upstream gauges")
    if cx.get("kind") == "upstream":
        add("get_neighbours", "upstream gauges are the leading signal")
    if cx.get("kind") in (None, "none") or cx.get("basis") in (None, "none"):
        add("get_neighbours", "no rain data: upstream gauges are the only leading signal")
    if isinstance(rate, (int, float)) and rate >= 2.0:
        add("get_past_floods", f"rising {rate:.1f} ft/h: how fast past floods here rose")
    if ev.get("direction") == "falling" or (isinstance(rate, (int, float)) and rate < 0):
        add("get_recent_windows", "the gauge is falling: check whether the peak has passed")
    if cat in ("moderate", "major") or (isinstance(fc, (int, float)) and any(
            lv.get(c) is not None and fc >= lv[c] for c in ("moderate", "major"))):
        add("get_impacts", "MODERATE or MAJOR: what the stage means locally")
    return out


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def checked_findings(findings: list[str], steps: list[dict], brief_text: str) -> tuple[list[str], list[str]]:
    """(kept, dropped): a finding is kept only when every number in it appears in a tool result or the brief, so the
    investigator cannot hand the writer a number nobody fetched."""
    seen = {round(float(x), 1) for x in _NUM.findall(brief_text + json.dumps([s.get("result") for s in steps], default=str))}
    kept, dropped = [], []
    for f in findings:
        nums = [round(float(x), 1) for x in _NUM.findall(f)]
        (kept if all(n in seen or abs(n) in seen for n in nums) else dropped).append(f)
    return kept, dropped


def investigate(complete, alert: dict, ev: dict, cx: dict, confidence: str, disagreement: dict | None, call) -> dict:
    """call(tool, *args, **kwargs) runs one MCP tool (the graph's run.call, so it is logged). The gauge, time and mode
    always come from the alert."""
    site, until, m = alert["site_no"], alert.get("as_of") or alert["raised_at"], alert["mode"]

    def run_tool(name, args):
        if name == "get_recent_windows":
            return call(name, site, args["hours"], until=until, mode=m)
        if name == "get_rain":
            return call(name, site, args["hours"], until=until, mode=m)
        if name == "get_data_quality":
            return call(name, site, until=until, mode=m, hours=args["hours"])
        if name == "get_neighbours":
            return call(name, site, until=until, mode=m)
        if name == "get_impacts":
            return call(name, site, alert["category"])
        return call(name, site)                       # get_past_floods, get_gauge_info

    req = checklist(alert, ev, cx, disagreement)
    b = brief(alert, ev, cx, confidence, disagreement)
    if req:
        b += "\n\nSITUATION CHECKLIST (checks this situation usually needs): " + "; ".join(f"{t}: {why}" for t, why in req)
    out = react(complete, SYSTEM, b, TOOLS, run_tool, required=req)
    out["checklist"] = [t for t, _ in req]
    out["findings"], out["dropped_findings"] = checked_findings(out["findings"], out["steps"], b)
    return out
