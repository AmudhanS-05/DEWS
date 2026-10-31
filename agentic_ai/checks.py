"""Deterministic reviewer checks (pure functions, unit-tested). The LLM judgement comes second, never instead.

Every check has an id in CHECKS; `advisory.requirements()` turns the same ids into writer instructions, and a test
asserts the two lists cover each other, so a first draft can normally pass.

Model output often carries typographic Unicode (en dash, non-breaking hyphen U+2011, narrow no-break space U+202F,
no-break space U+00A0, zero-width characters). `normalize()` maps them to plain ASCII before any check runs and
before the draft is saved, so "79 %", "6.8 ft" or "lead‑time" are read the same as their ASCII forms.
"""
from __future__ import annotations

import re
import unicodedata

from agentic_ai import rules

_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*%)?")
_ANY_NUM = re.compile(r"\d+(?:\.\d+)?")

# Check ids, in the order the reviewer reports them.
CHECKS = ("numbers", "category", "official_stage", "certainty", "probability", "horizon", "simulation",
          "live_label", "provisional", "uncertain", "length")

_DASHES = "‐‑‒–—―−⁃﹘﹣－"
_SPACES = "               　"
_DROP = "­​‌‍⁠﻿"
_TRANS = {**{ord(c): "-" for c in _DASHES}, **{ord(c): " " for c in _SPACES}, **{ord(c): None for c in _DROP},
          ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"', ord("≈"): "~",
          ord("∼"): "~", ord("…"): "...", ord("×"): "x"}


def normalize(text: str) -> str:
    """Typographic Unicode -> plain ASCII equivalents (dashes, spaces, quotes); NFKC for full-width digits/percent."""
    return unicodedata.normalize("NFKC", text.translate(_TRANS)).translate(_TRANS)


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
        elif isinstance(v, str):   # every digit run, so "2019-05-04T03:00:00Z" allows 2019, 5, 4, 3 and 0
            plain.extend(float(x) for x in _ANY_NUM.findall(normalize(v)))
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
    for m in _NUM.finditer(normalize(draft)):
        v = float(m.group(1))
        if m.group(2):   # a percentage
            ok = any(abs(v - p) <= 1.0 for p in pct)
        else:
            ok = any(abs(v - p) <= 0.06 for p in plain) or any(abs(v - p / 100) <= 0.0006 for p in pct)
        if not ok:
            bad.append(m.group(0).strip())
    return bad


def states_probability(draft: str, p: float) -> bool:
    """The alert probability appears as a percentage (79%) or, accepted too, as a decimal (0.79)."""
    d = normalize(draft)
    want = p * 100
    for m in _NUM.finditer(d):
        v = float(m.group(1))
        if (m.group(2) and abs(v - want) <= 1.0) or (not m.group(2) and v < 1 and abs(v - p) <= 0.006):
            return True
    return False


def review_detailed(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[tuple[str, str]]:
    """Deterministic checks on the normalised draft. Returns [(check id, reason)] (empty = pass)."""
    out: list[tuple[str, str]] = []
    draft = normalize(draft)
    low = draft.lower()
    bad = check_numbers(draft, facts)
    if bad:
        out.append(("numbers", f"numbers not in the evidence: {', '.join(bad)} (copy numbers exactly from FACTS)"))
    cat = alert["category"]
    if cat.upper() not in draft:
        out.append(("category", f"official category '{cat}' is not named (write it in capitals, e.g. {cat.upper()})"))
    level = (alert.get("official_levels") or {}).get(cat)
    if level is not None and not any(abs(n - level) <= 0.06 for n in _nums(draft)):
        out.append(("official_stage", f"official {cat} stage {level} ft is not stated"))
    for ph in rules.FORBIDDEN_PHRASES:
        if ph in low:
            out.append(("certainty", f"certainty language: '{ph}'"))
    p = alert.get("probability")
    if p is not None and not states_probability(draft, p):
        out.append(("probability", f"probability not stated (write it as a percentage: {round(p * 100)}%)"))
    if alert.get("horizon_h") is not None and not re.search(r"\b\d+(\.\d+)?\s*h\b|hours?", draft):
        out.append(("horizon", f"forecast horizon not stated (write: within {alert['horizon_h']:g} h)"))
    if alert["mode"] == "SIMULATION" and "SIMULATION" not in draft:
        out.append(("simulation", "SIMULATION label missing for replayed data (write the word SIMULATION in capitals)"))
    if alert["mode"] != "SIMULATION" and "SIMULATION" in draft:
        out.append(("live_label", "SIMULATION label on live data"))
    if alert.get("provisional_data") and "provisional" not in low:
        out.append(("provisional", "provisional-data note missing (use the word 'provisional')"))
    if confidence == "uncertain" and not ("UNCERTAIN" in draft and "evidence" in low and "context" in low):
        out.append(("uncertain", "uncertain case must say UNCERTAIN and state both the evidence and the context signal"))
    if len(draft) > rules.MAX_ADVISORY_CHARS:
        out.append(("length", f"too long: {len(draft)} > {rules.MAX_ADVISORY_CHARS} characters"))
    return out


def review(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[str]:
    """Deterministic checks. Returns a list of failure reasons (empty = pass)."""
    return [r for _, r in review_detailed(draft, alert, facts, confidence)]
