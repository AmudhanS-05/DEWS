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
CHECKS = ("numbers", "precision", "category", "official_stage", "forecast_category", "certainty", "probability",
          "horizon", "lead_time", "rain", "missing_value", "markdown", "simulation", "live_label", "provisional",
          "uncertain", "length")

_PRECISION = re.compile(r"(?<![\w.])\d+\.\d{2,}\s*(?:ft|feet|foot|mm)\b|(?<![\w.])\d+\.\d{3,}")
_MISSING = re.compile(r"\b(?:null|None|NaN|nan|undefined)\b|(?:^|(?<=[\s:=(]))\?(?=[\s,.;)]|$)", re.M)
_MARKDOWN = re.compile(r"\*\*|__|^\s*#{1,6}\s", re.M)
_NA = re.compile(r"not\s+available|unavailable", re.I)

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

    def walk(v, key="", is_pct=False):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            if is_pct or key.endswith("_pct"):
                pct.append(float(v))
            elif key.startswith("p_") or key == "probability":
                pct.append(round(float(v) * 100, 1))
            else:
                plain.append(float(v))
        elif isinstance(v, str):   # every digit run, so "2019-05-04T03:00:00Z" allows 2019, 5, 4, 3 and 0
            plain.extend(float(x) for x in _ANY_NUM.findall(normalize(v)))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k, is_pct or key.endswith("_pct"))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key, is_pct)

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
    """The alert probability appears as a whole percentage (79%)."""
    want = round(p * 100)
    return any(m.group(2) and "." not in m.group(1) and int(float(m.group(1))) == want
               for m in _NUM.finditer(normalize(draft)))


def _near(draft: str, a: str, b: re.Pattern, span: int = 80) -> bool:
    """Regex `a` (case-insensitive) followed within `span` characters on the same line by pattern `b`."""
    return any(b.search(line[m.end():m.end() + span]) for line in draft.splitlines()
               for m in re.finditer(a, line, re.I))


def review_detailed(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[tuple[str, str]]:
    """Deterministic checks on the normalised draft. Returns [(check id, reason)] (empty = pass)."""
    out: list[tuple[str, str]] = []
    draft = normalize(draft)
    low = draft.lower()
    bad = check_numbers(draft, facts)
    if bad:
        out.append(("numbers", f"numbers not in the evidence: {', '.join(bad)} (copy numbers exactly from FACTS)"))
    prec = _PRECISION.findall(draft)
    if prec:
        out.append(("precision", f"too many decimals: {', '.join(x.strip() for x in prec)} "
                                 "(stages and rates to 0.1 ft, e.g. 24.7 ft)"))
    cat = alert["category"]
    if cat.upper() not in draft:
        out.append(("category", f"official category '{cat}' is not named (write it in capitals, e.g. {cat.upper()})"))
    level = (alert.get("official_levels") or {}).get(cat)
    if level is not None and not any(abs(n - level) <= 0.06 for n in _nums(draft)):
        out.append(("official_stage", f"official {cat} stage {level} ft is not stated"))
    out += _forecast_category(draft, alert)
    for ph in rules.FORBIDDEN_PHRASES:
        if ph in low:
            out.append(("certainty", f"certainty language: '{ph}'"))
    p = alert.get("probability")
    if p is not None and not states_probability(draft, p):
        out.append(("probability", f"probability not stated as a whole percentage (write {round(p * 100)}%, "
                                   "not a decimal such as 0.79)"))
    if p is None and not _near(draft, "probability", _NA):
        out.append(("probability", "the model gives no probability: write 'probability: not available'"))
    if alert.get("horizon_h") is not None and not re.search(r"\b\d+(\.\d+)?\s*h\b|hours?", draft):
        out.append(("horizon", f"forecast horizon not stated (write: within {alert['horizon_h']:g} h)"))
    lt = facts.get("lead_time_h")
    if isinstance(lt, (int, float)):
        if not _near(draft, r"lead[\s-]*time", re.compile(rf"(?<![\d.]){lt:.1f}(?![\d])")):
            out.append(("lead_time", f"lead time not stated (write: Lead time to {cat.upper()}: about {lt:.1f} h)"))
    elif not _near(draft, r"lead[\s-]*time", _NA):
        out.append(("lead_time", f"lead time must say 'not available' (write: Lead time to {cat.upper()}: not available)"))
    if (facts.get("context") or {}).get("rain") == "rain data not available" and "rain data not available" not in low:
        out.append(("rain", "radar rain is missing: write 'rain data not available'"))
    miss = sorted({m.group(0) for m in _MISSING.finditer(draft)})
    if miss:
        out.append(("missing_value", f"missing value written as {', '.join(repr(x) for x in miss)} "
                                     "(write 'not available')"))
    if _MARKDOWN.search(draft):
        out.append(("markdown", "Markdown in a plain-text advisory (remove ** / __ / # headings)"))
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


def _forecast_category(draft: str, alert: dict) -> list[tuple[str, str]]:
    """When the forecast crosses a higher official category than the alert's, the draft must name it with its stage
    and must not say the forecast reaches the alert's (lower) category."""
    from agentic_ai.advisory import higher_forecast_category
    hi = higher_forecast_category(alert)
    if not hi:
        return []
    cat, lv = alert["category"], alert["official_levels"]
    out = []
    if hi.upper() not in draft or not any(abs(n - lv[hi]) <= 0.06 for n in _nums(draft)):
        out.append(("forecast_category", f"the forecast {alert['forecast_stage']:.1f} ft crosses the official "
                                         f"{hi.upper()} stage ({lv[hi]:.1f} ft): name {hi.upper()} and its stage"))
    if re.search(rf"forecast(?:ed)?\s+(?:to\s+)?(?:reach|cross|exceed|rise\s+(?:to|above))\w*\s+(?:the\s+)?"
                 rf"(?:official\s+)?{cat.upper()}\b", draft):
        out.append(("forecast_category", f"the forecast is called {cat.upper()} but it crosses {hi.upper()}: say the "
                                         f"forecast reaches {hi.upper()}; {cat.upper()} is the alert's category"))
    return out


def review(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[str]:
    """Deterministic checks. Returns a list of failure reasons (empty = pass)."""
    return [r for _, r in review_detailed(draft, alert, facts, confidence)]
