"""Advisory text: the deterministic template writer (always available) and the LLM prompt. Fixed schema:
headline, gauge and current stage, forecast and probability, lead time, official category, impacts, uncertainty,
provisional-data note, SIMULATION label. Wording: "forecast of crossing an official category", never "will flood"."""
from __future__ import annotations

import json

from agentic_ai import rules

SYSTEM_PROMPT = (
    "You write flood advisories for a duty officer to approve. Use ONLY the facts given; copy numbers exactly; "
    "never add, compute or convert a number that is not in the facts. It is a forecast of crossing an official NWS "
    "category, never a statement that a flood will happen. Plain language and plain ASCII characters (normal "
    "hyphens and spaces, no markdown). Fixed order: headline, gauge and stage, forecast and probability, lead time, "
    "impacts, uncertainty, data note. An automatic reviewer checks every numbered REQUIREMENT in the user message; "
    "meet all of them in the first draft. If revision feedback is given, fix exactly those problems and keep the rest.")


def lead_time_h(evidence: dict, alert: dict) -> float | None:
    """Hours until the official category stage at the current rise rate (an estimate, stated as one)."""
    level = (alert.get("official_levels") or {}).get(alert["category"])
    rate, stage = evidence.get("rise_rate_ft_per_h"), evidence.get("stage_last")
    if level is None or not rate or rate <= 0 or stage is None or stage >= level:
        return None
    return round((level - stage) / rate, 1)


def build_facts(alert: dict, evidence: dict, context: dict, update_n: int) -> dict:
    return {"alert": {k: alert.get(k) for k in ("site_no", "site_name", "mode", "category", "probability", "horizon_h",
                                                "forecast_stage", "current_stage", "official_levels",
                                                "provisional_data", "model", "raised_at")},
            "evidence": evidence, "context": context, "update_n": update_n,
            "lead_time_h": lead_time_h(evidence, alert)}


def template_text(alert: dict, evidence: dict, context: dict, confidence: str, update_n: int, disagreement: dict | None) -> str:
    cat = alert["category"]
    lv = alert["official_levels"]
    name = alert.get("site_name") or alert["site_no"]
    sim = alert["mode"] == "SIMULATION"
    lines = []
    head = f"Update {update_n}: " if update_n > 1 else ""
    if sim:
        lines.append("SIMULATION: replayed historical data, not a real event.")
    lines.append(f"{head}{name}: forecast to reach {cat.upper()} flood stage ({lv[cat]} ft) within {alert['horizon_h']:g} h.")
    rate = evidence.get("rise_rate_ft_per_h")
    rate_txt = f", changing {rate:+.1f} ft/h" if rate is not None else ""
    lines.append(f"Gauge now: {alert['current_stage']} ft{rate_txt} (as of {alert['raised_at']}).")
    lines.append(f"Forecast: {alert['forecast_stage']} ft in {alert['horizon_h']:g} h; probability of reaching "
                 f"{cat.upper()}: {round(alert['probability'] * 100)}% ({alert.get('model')} model). "
                 f"Official stages: action {lv['action']}, minor {lv['minor']}, moderate {lv['moderate']}, major {lv['major']} ft.")
    lt = lead_time_h(evidence, alert)
    if lt is not None:
        lines.append(f"Estimated time to {cat.upper()} at the current rate: about {lt} h (an estimate, not a promise).")
    rain = context.get("rain") or {}
    if rain.get("available"):
        lines.append(f"Radar rain over the catchment: {rain['rain_3h']} mm in 3 h, {rain['rain_6h']} mm in 6 h.")
    imp = context.get("impacts") or []
    lines.append("Impacts at this level (NWS): " + "; ".join(imp) if imp else
                 "Impacts at this level: no NWS impact statement loaded for this gauge.")
    if confidence == "uncertain" and disagreement:
        lines.append(f"UNCERTAIN: the signals disagree. Evidence from the gauge: {disagreement['evidence']}. "
                     f"Context: {disagreement['context']}. Re-check at the next window.")
    else:
        lines.append("Confidence: the gauge evidence and the surrounding context agree.")
    if alert.get("provisional_data"):
        lines.append("Data are provisional USGS readings and may be revised.")
    lines.append("Draft for approval by a duty officer; not sent until approved.")
    return "\n".join(lines)


def requirements(facts: dict, confidence: str) -> list[tuple[str, str]]:
    """Writer instructions, one per deterministic reviewer check (ids as in checks.CHECKS) that applies here."""
    a = facts["alert"]
    cat = a["category"]
    level = (a.get("official_levels") or {}).get(cat)
    req = [("numbers", "Copy every number exactly as it appears in FACTS (stages in ft, times in UTC as given); do not "
                       "round differently, convert units or compute new numbers. For the lead time use lead_time_h."),
           ("category", f"Name the official category in capital letters: {cat.upper()}.")]
    if level is not None:
        req.append(("official_stage", f"State the official {cat.upper()} flood stage: {level:g} ft."))
    req.append(("certainty", "No certainty language (never: " + ", ".join(f"'{p}'" for p in rules.FORBIDDEN_PHRASES)
                + "); say 'forecast to reach' and give the probability."))
    if a.get("probability") is not None:
        req.append(("probability", f"State the probability as a percentage with the % sign: {round(a['probability'] * 100)}%."))
    if a.get("horizon_h") is not None:
        req.append(("horizon", f"State the forecast horizon in hours: within {a['horizon_h']:g} h."))
    if a.get("mode") == "SIMULATION":
        req.append(("simulation", "Start with the word SIMULATION in capital letters and say it is replayed historical "
                                  "data, not a real event."))
    else:
        req.append(("live_label", "Live data: do not use the word SIMULATION."))
    if a.get("provisional_data"):
        req.append(("provisional", "Say the data are provisional (use the word 'provisional')."))
    if confidence == "uncertain":
        req.append(("uncertain", "Write UNCERTAIN, then state the gauge evidence signal and the context signal using the "
                                 "words 'evidence' and 'context', and ask for a re-check at the next window."))
    req.append(("length", f"At most {rules.MAX_ADVISORY_CHARS - 100} characters in total."))
    return req


def llm_user_prompt(facts: dict, confidence: str, disagreement: dict | None, feedback: list[str]) -> str:
    parts = [f"FACTS (JSON):\n{json.dumps(facts, default=str)}", f"Confidence: {confidence}."]
    if disagreement:
        parts.append(f"The signals disagree. Evidence: {disagreement['evidence']}. Context: {disagreement['context']}.")
    if facts["update_n"] > 1:
        parts.append(f"This is update {facts['update_n']} of the same episode: start with 'Update {facts['update_n']}:' "
                     "(after the SIMULATION label if there is one).")
    parts.append("REQUIREMENTS (each one is checked):\n" +
                 "\n".join(f"{i}. {text}" for i, (_, text) in enumerate(requirements(facts, confidence), 1)))
    if feedback:
        parts.append("REVISION FEEDBACK (the reviewer rejected the last draft; fix these): " + " | ".join(feedback))
    return "\n\n".join(parts)
