"""Deterministic reviewer checks (pure functions, unit-tested). The LLM judgement comes second, never instead."""
from __future__ import annotations

import re

from agentic_ai import rules

_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*%)?")


def _nums(s: str) -> list[float]:
    return [float(m.group(1)) for m in _NUM.finditer(s)]


def allowed_numbers(facts: dict) -> tuple[list[float], list[float]]:
    """(plain numbers, percentages) the draft may contain, taken only from the current evidence/context/alert."""
    plain: list[float] = []
    pct: list[float] = []

    def walk(v, key=""):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            if key.startswith("p_") or key == "probability":
                pct.append(round(float(v) * 100, 1))
            else:
                plain.append(float(v))
        elif isinstance(v, str):
            plain.extend(_nums(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)

    walk(facts)
    plain.extend([1, 2, 3, 4, 5, 6])   # list numbering / "Update n" are checked separately by wording
    return plain, pct


def check_numbers(draft: str, facts: dict) -> list[str]:
    """Every number in the draft must appear in the facts (within rounding). Returns the offending tokens."""
    plain, pct = allowed_numbers(facts)
    bad = []
    for m in _NUM.finditer(draft):
        v = float(m.group(1))
        if m.group(2):   # a percentage
            ok = any(abs(v - p) <= 1.0 for p in pct)
        else:
            ok = any(abs(v - p) <= 0.06 for p in plain) or any(abs(v - p / 100) <= 0.0006 for p in pct)
        if not ok:
            bad.append(m.group(0).strip())
    return bad


def review(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[str]:
    """Deterministic checks. Returns a list of failure reasons (empty = pass)."""
    reasons: list[str] = []
    low = draft.lower()
    bad = check_numbers(draft, facts)
    if bad:
        reasons.append(f"numbers not in the evidence: {', '.join(bad)}")
    cat = alert["category"]
    if cat.upper() not in draft:
        reasons.append(f"official category '{cat}' is not named (write it in capitals, e.g. {cat.upper()})")
    level = (alert.get("official_levels") or {}).get(cat)
    if level is not None and not any(abs(n - level) <= 0.06 for n in _nums(draft)):
        reasons.append(f"official {cat} stage {level} ft is not stated")
    for ph in rules.FORBIDDEN_PHRASES:
        if ph in low:
            reasons.append(f"certainty language: '{ph}'")
    if alert.get("probability") is not None and "%" not in draft:
        reasons.append("probability not stated")
    if alert.get("horizon_h") is not None and not re.search(r"\b\d+(\.\d+)?\s*h\b|hours?", draft):
        reasons.append("forecast horizon not stated")
    if alert["mode"] == "SIMULATION" and "SIMULATION" not in draft:
        reasons.append("SIMULATION label missing for replayed data")
    if alert["mode"] != "SIMULATION" and "SIMULATION" in draft:
        reasons.append("SIMULATION label on live data")
    if alert.get("provisional_data") and "provisional" not in low:
        reasons.append("provisional-data note missing")
    if confidence == "uncertain" and not ("UNCERTAIN" in draft and "evidence" in low and "context" in low):
        reasons.append("uncertain case must say UNCERTAIN and state both the evidence and the context signal")
    if len(draft) > rules.MAX_ADVISORY_CHARS:
        reasons.append(f"too long: {len(draft)} > {rules.MAX_ADVISORY_CHARS} characters")
    return reasons
