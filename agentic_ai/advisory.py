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
    "impacts, uncertainty, data note. FACTS keys are field names for you only: never write a field name (words "
    "joined with _ such as as_of or provisional_data) or the words true/false in the advisory. An automatic reviewer checks every numbered REQUIREMENT in the user message; "
    "meet all of them in the first draft. If revision feedback is given, fix exactly those problems and keep the rest.")


NA = "not available"
RAIN_NA = "rain data not available"
MRMS_ARCHIVE_START = "2020-10-14"     # first day of the MRMS radar archive the rain join reads
MODEL_NAMES = {"rise_rate": "rise-rate baseline", "persistence": "persistence baseline"}


def model_name(m) -> str:
    """Model id -> plain words for the advisory (raw ids such as rise_rate must not reach the text)."""
    return MODEL_NAMES.get(m, str(m).replace("_", " ")) if m else NA


def data_source(mode) -> str:
    return ("SIMULATION: a replay of a historical USGS gauge record (mode SIMULATION), not a real-time event"
            if mode == "SIMULATION" else "live USGS gauge readings")


def data_status(provisional) -> str:
    """USGS provisional flag -> plain words (the boolean itself must not reach the text)."""
    if provisional is None:
        return "provisional/approved status not stated by USGS"
    return "provisional per USGS (may be revised)" if provisional else "approved per USGS"


def rain_note(as_of) -> str:
    """Why radar rain is missing, for the writer (the advisory still says 'rain data not available')."""
    if as_of is not None and str(as_of)[:10] < MRMS_ARCHIVE_START:
        return "radar rain archive starts Oct 2020; this event is earlier, so there is no radar rain for it"
    return "radar rain is not joined for this window"


def context_summary(sig: dict, rain: dict, neighbours: list[dict]) -> str:
    """One line on the context signal and its kind. Upstream/rain = LEADING; downstream/nearby = SUPPORTING only:
    never written as a cause or predictor of the rise at this gauge."""
    def g(n):
        h = max((n.get("hours_in_window") or 1) - 1, 1)
        return f"{n['relation']} {_name(n)} {n['change_ft_over_window']:+.1f} ft in {h} h"
    rain_txt = (f"radar rain {ft(rain.get('rain_3h')) or 0.0:.1f} mm in 3 h" if (rain or {}).get("available")
                else RAIN_NA)
    ups = [n for n in neighbours if n.get("relation") == "upstream"]
    up_txt = ("no upstream gauge" if not ups else
              "upstream rising: " + ", ".join(g(n) for n in ups if n.get("rising")) if any(n.get("rising") for n in ups)
              else "upstream not rising")
    sup = [n for n in neighbours if n.get("relation") in ("downstream", "nearby") and n.get("rising")]
    if sig["direction"] == "unknown":
        return f"unknown: {rain_txt}, {up_txt}, no downstream or nearby gauge reading"
    if sig["kind"] == "leading":
        return f"LEADING signal ({rain_txt}; {up_txt})"
    if sig["kind"] == "supporting":
        return (f"SUPPORTING signal only: {', '.join(g(n) for n in sup)} rising in the same window (the storm "
                f"affects the area; this does not cause or predict the rise here); no leading signal ({rain_txt}, "
                f"{up_txt})")
    return f"no rising signal ({rain_txt}; {up_txt}; downstream/nearby gauges not rising)"


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
    as_of = alert.get("as_of") or alert.get("raised_at")
    nb = [{"name": _name(n), "relation": n.get("relation"),
           "signal_kind": "leading" if n.get("relation") == "upstream" else "supporting",
           "distance_km": n.get("distance_km"), "stage_now_ft": ft(n.get("stage_last")),
           "change_ft": ft(n.get("change_ft_over_window")), "hours": max((n.get("hours_in_window") or 1) - 1, 1),
           "rising": "rising" if n.get("rising") else ("not rising" if n.get("rising") is False else NA)}
          for n in (context or {}).get("neighbours") or []]
    return {"data_source": data_source(alert.get("mode")),
            "alert": {"site_no": alert.get("site_no"), "site_name": alert.get("site_name"), "mode": alert.get("mode"),
                      "category": alert["category"].upper(),
                      "probability_pct": NA if alert.get("probability") is None else pct(alert["probability"]),
                      "horizon_h": alert.get("horizon_h"), "forecast_stage_ft": ft(alert.get("forecast_stage")),
                      "current_stage_ft": ft(alert.get("current_stage")),
                      "official_levels_ft": {c: ft(lv.get(c)) for c in rules.CATEGORIES},
                      "data_status": data_status(alert.get("provisional_data")),
                      "model": model_name(alert.get("model")), "readings_time_utc": as_of},
            "highest_category_forecast": fc_cat.upper() if fc_cat else "below all official categories",
            "category_now": cur_cat.upper() if cur_cat else "below all official categories",
            "evidence": {"stage_now_ft": ft(evidence.get("stage_last")),
                         "rise_rate_ft_per_h": ft(evidence.get("rise_rate_ft_per_h")),
                         "stage_12h_ago_ft": ft(evidence.get("stage_12h_ago")), "n_windows": evidence.get("n_windows"),
                         "forecast_stage_ft": ft(evidence.get("forecast_stage")), "horizon_h": evidence.get("horizon_h"),
                         "probability_pct": probs if any(v is not None for v in probs.values()) else NA,
                         "model": model_name(evidence.get("model")), "past_floods_n": evidence.get("past_floods_n"),
                         "direction": evidence.get("direction")},
            "context": {"direction": (context or {}).get("direction") or "unknown",
                        "signal_kind": (context or {}).get("kind", "none"),
                        "summary": (context or {}).get("summary") or "no context signal",
                        "rule": "upstream gauges and radar rain are LEADING signals; downstream and nearby gauges are "
                                "SUPPORTING context only (never a cause or predictor of the rise at this gauge)",
                        "rain": {"rain_3h_mm": ft(rain.get("rain_3h")), "rain_6h_mm": ft(rain.get("rain_6h"))}
                        if rain.get("available") else RAIN_NA,
                        **({} if rain.get("available") else {"rain_note": rain_note(as_of)}),
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
        lines.append("SIMULATION: replay of a historical USGS gauge record, not a real-time event.")
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
    p_txt = f"{p}%" if p is not None else f"not available ({model_name(alert.get('model'))} gives none)"
    lines.append(f"Forecast: {_num(ft(alert['forecast_stage']))} ft{' in ' + format(h, 'g') + ' h' if h is not None else ''}; "
                 f"probability of reaching {cat.upper()}: {p_txt}. Official stages: action {lv['action']:.1f}, "
                 f"minor {lv['minor']:.1f}, moderate {lv['moderate']:.1f}, major {lv['major']:.1f} ft.")
    lt = lead_time_h(evidence, alert)
    lines.append(f"Lead time to {cat.upper()}: about {lt:.1f} h at the current rise rate (an estimate)."
                 if lt is not None else f"Lead time to {cat.upper()}: {NA}.")
    rain = context.get("rain") or {}
    if rain.get("available"):
        lines.append(f"Radar rain over the catchment: {_num(ft(rain['rain_3h']))} mm in 3 h, "
                     f"{_num(ft(rain['rain_6h']))} mm in 6 h.")
    else:
        lines.append(f"Radar rain: {RAIN_NA}"
                     + (" (radar archive starts Oct 2020)." if rain_note(alert.get("as_of") or alert.get("raised_at"))
                        .startswith("radar rain archive") else "."))
    imp = context.get("impacts") or []
    lines.append("Impacts at this level (NWS): " + "; ".join(imp) if imp else
                 "Impacts: no NWS impact statement loaded.")
    if confidence == "uncertain" and disagreement:
        lines.append(f"UNCERTAIN: the signals disagree. Evidence from the gauge: {disagreement['evidence']}. "
                     f"Context: {disagreement['context']}. Re-check at the next window.")
    else:
        lines.append(_context_line(context))
    if alert.get("provisional_data"):
        lines.append("Data are provisional USGS readings and may be revised.")
    elif alert.get("provisional_data") is False:
        lines.append("Data: approved USGS record.")
    lines.append("Draft: not sent until a duty officer approves it.")
    return "\n".join(lines)


def _name(n: dict) -> str:
    return " ".join(str(n.get("name") or n.get("site_no")).split())


def _context_line(context: dict) -> str:
    kind, d = context.get("kind"), context.get("direction") or "unknown"
    if d == "unknown":
        return "Context: based on the gauge alone (no rain, upstream or neighbour data to cross-check)."
    if kind == "leading":
        return "Context: a leading signal (rain or upstream gauges) agrees with the gauge." if d == "rising" else \
            "Context: no leading signal is rising."
    if kind == "supporting":
        sup = [n for n in context.get("neighbours") or [] if n.get("relation") in ("downstream", "nearby") and n.get("rising")]
        names = " and ".join(f"{n['relation']} {_name(n)}" for n in sup[:2])
        return (f"Context: supporting signal only: {names} also rising (storm affects the area; not a forecast for "
                f"this gauge). No leading signal.")
    return "Context: neighbouring gauges are not rising; the rise is at this gauge."


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
    req.append(("category_stage", "When you give a category's stage, use its official stage exactly: "
                + ", ".join(f"{c.upper()} {v:.1f} ft" for c, v in lv.items() if v is not None) + "."))
    req.append(("threshold", "Use no threshold other than these official NWS categories (no 'danger level', "
                             "'warning level', 'flood threshold' or made-up categories such as 'severe')."))
    fc = facts.get("highest_category_forecast", "")
    if fc.lower() in rules.CATEGORIES and rules.rank(fc.lower()) > rules.rank(cat):
        req.append(("forecast_category",
                    f"The forecast stage {a['forecast_stage_ft']:.1f} ft crosses the official {fc} stage "
                    f"({lv[fc.lower()]:.1f} ft), above the alert's {cat.upper()}. Say the forecast reaches {fc} "
                    f"({lv[fc.lower()]:.1f} ft) and that the alert category is {cat.upper()}; never write that the "
                    f"forecast reaches {cat.upper()}."))
    req.append(("certainty", "No certainty language (never: " + ", ".join(f"'{p}'" for p in rules.FORBIDDEN_PHRASES)
                + "); say 'forecast to reach'."))
    req.append(("cause", "Do not say why the water rises (no 'caused by', 'due to', 'because of', dam release): "
                         "the facts give signals, not causes."))
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
    if facts["context"].get("rain") == RAIN_NA:
        req.append(("rain", f"Write 'rain data not available' for the radar rain (reason you may add: "
                            f"{facts['context'].get('rain_note')})."))
    req.append(("missing_value", "Never write null, None, NaN or '?' for a value: write 'not available'."))
    req.append(("raw_field", "Never write a FACTS field name (words joined with _, e.g. as_of, rise_rate) or the "
                             "words true/false; use plain words (e.g. 'as of', 'data: approved per USGS')."))
    from agentic_ai.checks import ABBREVIATIONS
    req.append(("abbreviation", "Capitalised abbreviations only from: " + ", ".join(sorted(ABBREVIATIONS))
                + " (and gauge names exactly as in FACTS); no other all-caps words."))
    kind = facts["context"].get("signal_kind")
    req.append(("context_kind", ("Context: say the downstream/nearby gauges are a SUPPORTING signal (also rising in "
                                 "the same window); n" if kind == "supporting" else "N")
                + "ever say a downstream or nearby gauge causes, leads or predicts the rise at this gauge."))
    req.append(("markdown", "Plain text only: no Markdown (no ** or __, no # headings)."))
    if a.get("mode") == "SIMULATION":
        req.append(("simulation", "Start with the word SIMULATION in capital letters and say it is a replay of a "
                                  "historical USGS gauge record, not a real-time event."))
    else:
        req.append(("live_label", "Live data: do not use the word SIMULATION."))
    if str(a.get("data_status", "")).startswith("provisional per"):
        req.append(("provisional", "Say the data are provisional (use the word 'provisional')."))
    if confidence == "uncertain":
        req.append(("uncertain", "Write UNCERTAIN, then state the gauge evidence signal and the context signal using the "
                                 "words 'evidence' and 'context', and ask for a re-check at the next window."))
    if facts.get("data_quality"):
        req.append(("suspect_data", "The gauge readings are flagged as a possible sensor fault ("
                    + str(facts["data_quality"]) + "): say the data are questionable."))
    n = facts.get("update_n") or 1
    req.append(("update", f"Start with 'Update {n}:' (after the SIMULATION label if there is one); this is update {n} "
                          "of the same flood episode." if n > 1 else "This is the first alert of the episode: no "
                          "'Update n' wording."))
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
