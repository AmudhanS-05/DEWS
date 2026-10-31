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


NA = "not available"
RAIN_NA = "rain data not available"


def ft(x) -> float | None:
    """Stages and rates are reported to 0.1 ft (the gauge reads to 0.01 ft; more digits only look precise)."""
    return None if x is None else round(float(x), 1)


def pct(p) -> int | None:
    """Probabilities as a whole percentage."""
    return None if p is None else int(round(float(p) * 100))


def lead_time_h(evidence: dict, alert: dict) -> float | None:
    """Hours until the alert's official category stage at the current rise rate (an estimate, stated as one)."""
    level = (alert.get("official_levels") or {}).get(alert["category"])
    rate, stage = evidence.get("rise_rate_ft_per_h"), evidence.get("stage_last")
    if level is None or not rate or rate <= 0 or stage is None or stage >= level:
        return None
    return round((level - stage) / rate, 1)


def forecast_category(alert: dict) -> str | None:
    """Highest official category the forecast stage reaches (may be above the alert's own category)."""
    return rules.category_of(alert.get("forecast_stage"), alert.get("official_levels"))


def higher_forecast_category(alert: dict) -> str | None:
    """The forecast's category when it is above the alert's category, else None."""
    fc = forecast_category(alert)
    return fc if rules.rank(fc) > rules.rank(alert["category"]) else None


def build_facts(alert: dict, evidence: dict, context: dict, update_n: int) -> dict:
    """The only facts the writer sees, already in report precision (0.1 ft, whole %), missing values spelled out."""
    lv = alert.get("official_levels") or {}
    lt = lead_time_h(evidence, alert)
    fc_cat, cur_cat = forecast_category(alert), rules.category_of(alert.get("current_stage"), lv)
    probs = {c: pct(evidence.get(f"p_{c}")) for c in rules.CATEGORIES}
    rain = (context or {}).get("rain") or {}
    nb = [{"name": n.get("name"), "relation": n.get("relation"), "distance_km": n.get("distance_km"),
           "stage_now_ft": ft(n.get("stage_last")), "change_ft": ft(n.get("change_ft_over_window")),
           "rising": n.get("rising")} for n in (context or {}).get("neighbours") or []]
    return {"alert": {"site_no": alert.get("site_no"), "site_name": alert.get("site_name"), "mode": alert.get("mode"),
                      "category": alert["category"].upper(),
                      "probability_pct": NA if alert.get("probability") is None else pct(alert["probability"]),
                      "horizon_h": alert.get("horizon_h"), "forecast_stage_ft": ft(alert.get("forecast_stage")),
                      "current_stage_ft": ft(alert.get("current_stage")),
                      "official_levels_ft": {c: ft(lv.get(c)) for c in rules.CATEGORIES},
                      "provisional_data": alert.get("provisional_data"), "model": alert.get("model"),
                      "as_of": alert.get("as_of") or alert.get("raised_at")},
            "highest_category_forecast": fc_cat.upper() if fc_cat else "below all official categories",
            "category_now": cur_cat.upper() if cur_cat else "below all official categories",
            "evidence": {"stage_now_ft": ft(evidence.get("stage_last")),
                         "rise_rate_ft_per_h": ft(evidence.get("rise_rate_ft_per_h")),
                         "stage_12h_ago_ft": ft(evidence.get("stage_12h_ago")), "n_windows": evidence.get("n_windows"),
                         "forecast_stage_ft": ft(evidence.get("forecast_stage")), "horizon_h": evidence.get("horizon_h"),
                         "probability_pct": probs if any(v is not None for v in probs.values()) else NA,
                         "model": evidence.get("model"), "past_floods_n": evidence.get("past_floods_n"),
                         "direction": evidence.get("direction")},
            "context": {"direction": (context or {}).get("direction"),
                        "rain": {"rain_3h_mm": ft(rain.get("rain_3h")), "rain_6h_mm": ft(rain.get("rain_6h"))}
                        if rain.get("available") else RAIN_NA,
                        "neighbours": nb,
                        "impacts": (context or {}).get("impacts") or "no NWS impact statement loaded"},
            "update_n": update_n, "lead_time_h": NA if lt is None else lt}


def _num(x) -> str:
    return "not available" if x is None else f"{x:.1f}"


def template_text(alert: dict, evidence: dict, context: dict, confidence: str, update_n: int, disagreement: dict | None) -> str:
    cat = alert["category"]
    lv = alert["official_levels"]
    name = alert.get("site_name") or alert["site_no"]
    sim = alert["mode"] == "SIMULATION"
    higher = higher_forecast_category(alert)
    reach = higher or cat
    h = alert.get("horizon_h")
    within = f" within {h:g} h" if h is not None else ""
    lines = []
    head = f"Update {update_n}: " if update_n > 1 else ""
    if sim:
        lines.append("SIMULATION: replayed historical data, not a real event.")
    lines.append(f"{head}{name}: forecast to reach {reach.upper()} flood stage ({lv[reach]:.1f} ft){within}.")
    if higher:
        lines.append(f"Alert category: {cat.upper()} ({lv[cat]:.1f} ft); the forecast stage is above the official "
                     f"{higher.upper()} stage, the highest official category it crosses.")
    rate = ft(evidence.get("rise_rate_ft_per_h"))
    rate_txt = f", changing {rate:+.1f} ft/h" if rate is not None else ""
    now_cat = rules.category_of(alert.get("current_stage"), lv)
    now_txt = f", already above {now_cat.upper()}" if now_cat else ""
    lines.append(f"Gauge now: {_num(ft(alert['current_stage']))} ft{rate_txt}{now_txt} "
                 f"(as of {alert.get('as_of') or alert['raised_at']}).")
    p = pct(alert.get("probability"))
    p_txt = f"{p}%" if p is not None else f"not available ({alert.get('model')} model gives none)"
    lines.append(f"Forecast: {_num(ft(alert['forecast_stage']))} ft{' in ' + format(h, 'g') + ' h' if h is not None else ''}; "
                 f"probability of reaching {cat.upper()}: {p_txt}. Official stages: action {lv['action']:.1f}, "
                 f"minor {lv['minor']:.1f}, moderate {lv['moderate']:.1f}, major {lv['major']:.1f} ft.")
    lt = lead_time_h(evidence, alert)
    lines.append(f"Lead time to {cat.upper()}: about {lt:.1f} h at the current rise rate (an estimate, not a promise)."
                 if lt is not None else f"Lead time to {cat.upper()}: {NA}.")
    rain = context.get("rain") or {}
    if rain.get("available"):
        lines.append(f"Radar rain over the catchment: {_num(ft(rain['rain_3h']))} mm in 3 h, "
                     f"{_num(ft(rain['rain_6h']))} mm in 6 h.")
    else:
        lines.append(f"Radar rain: {RAIN_NA}.")
    imp = context.get("impacts") or []
    lines.append("Impacts at this level (NWS): " + "; ".join(imp) if imp else
                 "Impacts: no NWS impact statement loaded for this gauge.")
    if confidence == "uncertain" and disagreement:
        lines.append(f"UNCERTAIN: the signals disagree. Evidence from the gauge: {disagreement['evidence']}. "
                     f"Context: {disagreement['context']}. Re-check at the next window.")
    else:
        lines.append("Confidence: the gauge evidence and the surrounding context agree."
                     if (context.get("direction") or "unknown") != "unknown" else
                     "Confidence: based on the gauge alone (no rain or upstream data to cross-check).")
    if alert.get("provisional_data"):
        lines.append("Data are provisional USGS readings and may be revised.")
    lines.append("Draft for approval by a duty officer; not sent until approved.")
    return "\n".join(lines)


def requirements(facts: dict, confidence: str) -> list[tuple[str, str]]:
    """Writer instructions, one per deterministic reviewer check (ids as in checks.CHECKS) that applies here."""
    a = facts["alert"]
    cat = a["category"].lower()
    lv = a.get("official_levels_ft") or {}
    level = lv.get(cat)
    req = [("numbers", "Copy every number exactly as it appears in FACTS (stages in ft, times in UTC as given); do not "
                       "round differently, convert units or compute new numbers."),
           ("precision", "Stages and rates with one decimal (e.g. 24.7 ft, 5.5 ft/h), as in FACTS."),
           ("category", f"Name the alert's official category in capital letters: {cat.upper()}.")]
    if level is not None:
        req.append(("official_stage", f"State the official {cat.upper()} flood stage: {level:.1f} ft."))
    fc = facts.get("highest_category_forecast", "")
    if fc.lower() in rules.CATEGORIES and rules.rank(fc.lower()) > rules.rank(cat):
        req.append(("forecast_category",
                    f"The forecast stage {a['forecast_stage_ft']:.1f} ft crosses the official {fc} stage "
                    f"({lv[fc.lower()]:.1f} ft), above the alert's {cat.upper()}. Say the forecast reaches {fc} "
                    f"({lv[fc.lower()]:.1f} ft) and that the alert category is {cat.upper()}; never write that the "
                    f"forecast reaches {cat.upper()}."))
    req.append(("certainty", "No certainty language (never: " + ", ".join(f"'{p}'" for p in rules.FORBIDDEN_PHRASES)
                + "); say 'forecast to reach'."))
    if a.get("probability_pct") == "not available":
        req.append(("probability", "Write 'probability: not available' (the model gives none); write no percentage."))
    else:
        req.append(("probability", f"State the probability as a whole percentage with the % sign: "
                                   f"{a['probability_pct']}% (not a decimal such as 0.79)."))
    if a.get("horizon_h") is not None:
        req.append(("horizon", f"State the forecast horizon in hours: within {a['horizon_h']:g} h."))
    lt = facts.get("lead_time_h")
    req.append(("lead_time", f"Write 'Lead time to {cat.upper()}: about {lt:.1f} h' (an estimate)." if isinstance(lt, float)
                else f"Write 'Lead time to {cat.upper()}: not available'."))
    if facts["context"].get("rain") == "rain data not available":
        req.append(("rain", "Write 'rain data not available' for the radar rain."))
    req.append(("missing_value", "Never write null, None, NaN or '?' for a value: write 'not available'."))
    req.append(("markdown", "Plain text only: no Markdown (no ** or __, no # headings)."))
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
