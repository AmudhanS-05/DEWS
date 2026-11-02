"""Seeded errors for the reviewer (A3.1): take a correct advisory and plant ONE known error in it.

Each corruption is a pure function (text, alert, facts) -> corrupted text, or None when it does not apply (e.g. no
SIMULATION label to remove on live data). `seed_errors()` returns every applicable corruption with the reviewer check
ids that count as catching it. `catch_rate()` runs the deterministic reviewer (`checks.review_detailed`) on each one.

Error kinds (the six core kinds first, then extra ones):
  wrong_number        a stage/rate changed to a value that is not in the facts                     -> numbers
  wrong_probability   probability changed by 15 points                                             -> probability/numbers
  wrong_category      the alert's category word replaced by another official category              -> category
  wrong_category_stage the category's stage replaced by ANOTHER official stage (a real number)     -> category_stage
  invented_threshold  "local danger level X ft" with X not an official stage                       -> threshold/numbers
  invented_threshold_known  same, X copied from the facts (the current stage), so the number check alone passes it
  invented_category   "SEVERE flood category"                                                      -> threshold/abbreviation
  missing_simulation  SIMULATION label removed from replayed data                                  -> simulation
  simulation_on_live  SIMULATION label added to live data                                          -> live_label
  cause_downstream    "the downstream gauge is causing the rise here"                              -> context_kind/cause
  cause_claim         "the rise is caused by a dam release upstream"                               -> cause
  unknown_abbreviation NWS -> NGS (the round-4 live slip), or an invented "NFC" sentence           -> abbreviation
  certainty           "Homes will flood."                                                          -> certainty
  missing_provisional the word provisional removed from provisional data                           -> provisional
  null_value          "not available" written as None                                              -> missing_value
  markdown            headline in **bold**                                                         -> markdown
  wrong_update        "Update 2" on a first alert, or the Update number dropped on a repeat alert  -> update
"""
from __future__ import annotations

import re

from agentic_ai import checks, rules

SLICE_KINDS = ("wrong_number", "wrong_category", "invented_threshold", "missing_simulation", "cause_claim",
               "unknown_abbreviation")


def _fmt(x: float) -> str:
    return f"{x:.1f}"


def _fresh_value(v: float, facts: dict) -> float:
    """A wrong value near v that is not any number the facts allow (so it is a real seeded error)."""
    plain, _ = checks.allowed_numbers(facts)
    for d in (2.3, 3.7, 1.9, 4.6, 6.2, 7.4, 8.9):
        for cand in (v + d, v - d):
            if cand > 0 and all(abs(cand - p) > 0.15 for p in plain):
                return round(cand, 1)
    return round(v + 11.3, 1)


def wrong_number(text, alert, facts):
    for key in ("forecast_stage", "current_stage"):
        v = alert.get(key)
        if v is None:
            continue
        s = _fmt(v)
        if s in text:
            return text.replace(s, _fmt(_fresh_value(float(s), facts)), 1)
    m = re.search(r"\d+\.\d", text)
    return None if not m else text[:m.start()] + _fmt(_fresh_value(float(m.group()), facts)) + text[m.end():]


def wrong_probability(text, alert, facts):
    p = alert.get("probability")
    if p is None:
        return None
    s = f"{round(p * 100)}%"
    if s not in text:
        return None
    q = round(p * 100) + 15 if p < 0.8 else round(p * 100) - 15
    return text.replace(s, f"{q}%")


def wrong_category(text, alert, facts):
    cat = alert["category"].upper()
    if cat not in text:
        return None
    other = {"ACTION": "MINOR", "MINOR": "MODERATE", "MODERATE": "MAJOR", "MAJOR": "MODERATE"}[cat]
    return re.sub(rf"\b{cat}\b", other, text)


def wrong_category_stage(text, alert, facts):
    lv = alert.get("official_levels") or {}
    cat = alert["category"]
    if lv.get(cat) is None:
        return None
    other = next((float(lv[c]) for c in rules.CATEGORIES if lv.get(c) is not None
                  and abs(float(lv[c]) - float(lv[cat])) > 0.5), None)
    s = f"{cat.upper()}"
    m = re.search(rf"\b{s}\b[^\n\d]{{0,25}}?\(?({re.escape(_fmt(float(lv[cat])))})\s*ft", text, re.I)
    if other is None or not m:
        return None
    return text[:m.start(1)] + _fmt(other) + text[m.end(1):]


def _append(text: str, sentence: str) -> str:
    lines = text.splitlines()
    # insert before the "Draft: not sent ..." footer when there is one, else at the end
    idx = next((i for i, ln in enumerate(lines) if ln.lower().startswith("draft:")), len(lines))
    return "\n".join(lines[:idx] + [sentence] + lines[idx:])


def invented_threshold(text, alert, facts):
    v = _fresh_value(float((alert.get("official_levels") or {}).get(alert["category"]) or 10.0), facts)
    return _append(text, f"The local danger level for this crossing is {_fmt(v)} ft.")


def invented_threshold_known(text, alert, facts):
    cur = alert.get("current_stage")
    if cur is None:
        return None
    lv = [float(v) for c, v in (alert.get("official_levels") or {}).items() if c in rules.CATEGORIES and v is not None]
    if any(abs(round(float(cur), 1) - x) <= 0.06 for x in lv):
        return None
    return _append(text, f"The gauge is at the local warning level of {_fmt(float(cur))} ft.")


def invented_category(text, alert, facts):
    return _append(text, "This reaches the SEVERE flood category.")


def missing_simulation(text, alert, facts):
    if alert.get("mode") != "SIMULATION" or "SIMULATION" not in text:
        return None
    return re.sub(r"SIMULATION:?\s*", "", text)


def simulation_on_live(text, alert, facts):
    if alert.get("mode") == "SIMULATION":
        return None
    return "SIMULATION: replayed data.\n" + text


def cause_downstream(text, alert, facts):
    nb = [n for n in (facts.get("context") or {}).get("neighbours") or [] if n.get("relation") in ("downstream", "nearby")]
    name = f"{nb[0]['relation']} {nb[0]['name']}" if nb else "the downstream gauge"
    return _append(text, f"The rise at {name} is causing the rise at this gauge.")


def cause_claim(text, alert, facts):
    return _append(text, "The rise is caused by a dam release upstream.")


def unknown_abbreviation(text, alert, facts):
    if "NWS" in text:
        return text.replace("NWS", "NGS", 1)
    return _append(text, "Check the NFC bulletin for road closures.")


def certainty(text, alert, facts):
    return _append(text, "Homes along the creek will flood.")


def missing_provisional(text, alert, facts):
    if not alert.get("provisional_data") or "provisional" not in text.lower():
        return None
    return "\n".join(ln for ln in text.splitlines() if "provisional" not in ln.lower())


def null_value(text, alert, facts):
    if "not available" not in text:
        return None
    return text.replace("not available", "None", 1)


def markdown(text, alert, facts):
    lines = text.splitlines()
    i = 1 if lines and lines[0].startswith("SIMULATION") and len(lines) > 1 else 0
    lines[i] = f"**{lines[i]}**"
    return "\n".join(lines)


def wrong_update(text, alert, facts):
    n = facts.get("update_n") or 1
    if n > 1:
        return re.sub(rf"\bUpdate {n}:\s*", "", text) if f"Update {n}" in text else None
    lines = text.splitlines()
    i = 1 if lines and lines[0].startswith("SIMULATION") and len(lines) > 1 else 0
    lines[i] = "Update 2: " + lines[i]
    return "\n".join(lines)


CORRUPTIONS = {   # kind -> (function, check ids that count as catching it)
    "wrong_number": (wrong_number, {"numbers", "category_stage", "official_stage", "forecast_category"}),
    "wrong_probability": (wrong_probability, {"probability", "numbers"}),
    "wrong_category": (wrong_category, {"category", "category_stage", "forecast_category"}),
    "wrong_category_stage": (wrong_category_stage, {"category_stage", "official_stage", "forecast_category"}),
    "invented_threshold": (invented_threshold, {"threshold", "numbers"}),
    "invented_threshold_known": (invented_threshold_known, {"threshold"}),
    "invented_category": (invented_category, {"threshold", "abbreviation"}),
    "missing_simulation": (missing_simulation, {"simulation"}),
    "simulation_on_live": (simulation_on_live, {"live_label"}),
    "cause_downstream": (cause_downstream, {"context_kind", "cause"}),
    "cause_claim": (cause_claim, {"cause"}),
    "unknown_abbreviation": (unknown_abbreviation, {"abbreviation"}),
    "certainty": (certainty, {"certainty"}),
    "missing_provisional": (missing_provisional, {"provisional"}),
    "null_value": (null_value, {"missing_value"}),
    "markdown": (markdown, {"markdown"}),
    "wrong_update": (wrong_update, {"update"}),
}


def seed_errors(text: str, alert: dict, facts: dict) -> list[dict]:
    """Every corruption that applies to this text: [{kind, text, expected}] (unchanged results are skipped)."""
    out = []
    for kind, (fn, expected) in CORRUPTIONS.items():
        bad = fn(text, alert, facts)
        if bad is not None and bad != text:
            out.append({"kind": kind, "text": bad, "expected": expected})
    return out


def catch_rate(cases: list[dict]) -> dict:
    """cases = [{name, text, alert, facts, confidence}] holding CORRECT advisories (each must pass the reviewer).
    Returns {rows, by_kind, caught, total, base_false_alarms}."""
    rows, base_fail = [], []
    for c in cases:
        conf = c.get("confidence", "normal")
        if checks.review_detailed(c["text"], c["alert"], c["facts"], conf):
            base_fail.append(c["name"])
        for e in seed_errors(c["text"], c["alert"], c["facts"]):
            found = checks.review_detailed(e["text"], c["alert"], c["facts"], conf)
            ids = {i for i, _ in found}
            rows.append({"case": c["name"], "kind": e["kind"], "caught": bool(ids & e["expected"]),
                         "by": ",".join(sorted(ids & e["expected"])) or "-",
                         "other_checks": ",".join(sorted(ids - e["expected"])) or "-",
                         "reason": next((r for i, r in found if i in e["expected"]), "")[:120]})
    by_kind: dict[str, list[int]] = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[0] += r["caught"]
        k[1] += 1
    return {"rows": rows, "by_kind": by_kind, "caught": sum(r["caught"] for r in rows), "total": len(rows),
            "base_false_alarms": base_fail}
