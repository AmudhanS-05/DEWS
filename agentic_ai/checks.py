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
CHECKS = ("numbers", "precision", "category", "official_stage", "category_stage", "threshold", "forecast_category",
          "certainty", "cause", "probability", "horizon", "lead_time", "rain", "missing_value", "raw_field",
          "abbreviation", "context_kind", "markdown", "simulation", "live_label", "provisional", "uncertain",
          "suspect_data", "update", "model_rule", "length")

_PRECISION = re.compile(r"(?<![\w.])\d+\.\d{2,}\s*(?:ft|feet|foot|mm)\b|(?<![\w.])\d+\.\d{3,}")
_MISSING = re.compile(r"\b(?:null|None|NaN|nan|undefined)\b|(?:^|(?<=[\s:=(]))\?(?=[\s,.;)]|$)", re.M)
# FACTS field names (snake_case such as as_of, provisional_data, rise_rate) and JSON booleans must not reach the text.
_RAW_FIELD = re.compile(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b|\b(?:true|false|True|False|TRUE|FALSE)\b"
                        r"|provisional[\s_]*data[\s:=]*(?:not\s+available|none|yes|no)\b", re.I)
# All-caps 2-6 letter tokens must be known (round 4: "NGS impact statement" for NWS passed both reviewers).
ABBREVIATIONS = frozenset({"NWS", "USGS", "NOAA", "MRMS", "NWPS", "SIMULATION", "MINOR", "MODERATE", "MAJOR",
                           "ACTION", "UTC"})
_CAPS = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,6}(?![A-Za-z0-9])")


def allowed_abbreviations(facts: dict) -> set[str]:
    """The fixed set plus every all-caps token in the facts (gauge LIDs, names such as 'FM 1826' or 'US 183')."""
    found: set[str] = set()

    def walk(v):
        if isinstance(v, str):
            found.update(_CAPS.findall(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(facts)
    return set(ABBREVIATIONS) | found


# A downstream/nearby gauge is SUPPORTING context: never a cause or predictor of the rise at this gauge.
_CAUSAL = re.compile(r"\b(?:downstream|nearby|neighbou?ring)\b[^.\n]{0,100}?\b(?:caus\w*|predict\w*|lead(?:s|ing)?\s+to|"
                     r"drive[sn]?|driving|leading\s+(?:signal|indicator)|will\s+(?:push|raise|bring))\b", re.I)
# A3.1: a category named next to a stage must use that category's official stage ("MAJOR flood stage (7.0 ft)" with
# MAJOR = 13.0 ft is wrong even though 7.0 is a real number from the facts).
_CAT_STAGE = re.compile(r"\b(action|minor|moderate|major)\b(?:\s+flood)?(?:\s+(?:stage|level|category))?"
                        r"\s*(?:\(|:|=|at|of|is)?\s*(\d+(?:\.\d+)?)\s*(?:ft|feet|foot)\b", re.I)
# Thresholds other than the official NWPS stages (danger/warning level, "flood threshold", made-up category names).
_THRESHOLD = re.compile(r"\b(?:threshold|danger\s+(?:level|mark|line)|warning\s+(?:level|mark|line)|alert\s+level|"
                        r"critical\s+(?:level|stage)|evacuation\s+(?:level|stage)|safe\s+level|bankfull)\b"
                        r"[^.\n\d]{0,30}(\d+(?:\.\d+)?)(?![\d.]*\s*%)\s*(?:ft|feet|foot)?", re.I)  # % = a probability
_FAKE_CATEGORY = re.compile(r"\b(?:severe|extreme|critical|catastrophic|red|orange|yellow|amber|danger)\s+"
                            r"(?:flood\s+)?(?:category|stage|level|threshold|alert\s+level)\b", re.I)
# The advisory forecasts a crossing; it never asserts why the water rises (no cause in the facts).
_CAUSE = re.compile(r"\b(?:caused\s+by|causing|due\s+to|because\s+of|as\s+a\s+result\s+of|triggered\s+by|"
                    r"result(?:ed|s|ing)?\s+from|dam\s+(?:release|failure|break))\b", re.I)
_CAUSE_OK = re.compile(r"archive|rain\s+data|radar|not\s+available|data\s+(?:are|is)|provisional|revis", re.I)
_SUSPECT = re.compile(r"sensor\s+fault|questionable|suspect|unreliable|may\s+be\s+(?:wrong|faulty|in\s+error)", re.I)
_UPDATE = re.compile(r"\bUpdate\s+(\d+)\b", re.I)
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
                if k == "episode":     # A3.3 guard: earlier alerts are wording only, never a source of numbers
                    continue
                walk(x, k, is_pct or key.endswith("_pct"))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key, is_pct)

    walk(facts)
    plain.extend([abs(x) for x in plain if x < 0])   # "-0.5 ft/h": the draft's number token is 0.5 (sign is text)
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
    """The alert probability appears as a whole percentage (79%) or, accepted too, as a decimal (0.79); value checked.
    The writer is told to use "79%"; a draft that wrote 0.79 is still correct and is saved as written."""
    d = normalize(draft)
    want = p * 100
    for m in _NUM.finditer(d):
        v = float(m.group(1))
        if (m.group(2) and "." not in m.group(1) and abs(v - want) <= 0.5) or (not m.group(2) and v < 1 and abs(v - p) <= 0.006):
            return True
    return False


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
    lv = alert.get("official_levels") or {}
    off = [float(v) for c, v in lv.items() if c in rules.CATEGORIES and v is not None]
    for m in _CAT_STAGE.finditer(draft):
        c, v = m.group(1).lower(), float(m.group(2))
        if lv.get(c) is not None and abs(v - float(lv[c])) > 0.06:
            out.append(("category_stage", f"{c.upper()} is given as {m.group(2)} ft but the official {c.upper()} "
                                          f"stage is {float(lv[c]):.1f} ft"))
            break
    thr = [m.group(0).strip() for m in _THRESHOLD.finditer(draft) if not any(abs(float(m.group(1)) - o) <= 0.06 for o in off)]
    thr += [m.group(0) for m in _FAKE_CATEGORY.finditer(draft)]
    if thr:
        out.append(("threshold", f"threshold that is not an official NWPS stage: {', '.join(repr(t) for t in thr)} "
                                 "(use only the official ACTION/MINOR/MODERATE/MAJOR stages)"))
    out += _forecast_category(draft, alert)
    for ph in rules.FORBIDDEN_PHRASES:
        if ph in low:
            out.append(("certainty", f"certainty language: '{ph}'"))
    causes = [m.group(0) for line in re.split(r"(?<=[.!?])\s+|\n", draft) if not _CAUSE_OK.search(line)
              for m in _CAUSE.finditer(line)]
    if causes:
        out.append(("cause", f"cause claim not in the facts: {', '.join(repr(c) for c in causes)} (state the forecast "
                             "and the signals; do not say why the water rises)"))
    p = alert.get("probability")
    if p is not None and not states_probability(draft, p):
        out.append(("probability", f"probability not stated (write it as a percentage: {round(p * 100)}%)"))
    if p is None and not _near(draft, "probability", _NA):
        out.append(("probability", "the model gives no probability: write 'probability: not available'"))
    if alert.get("horizon_h") is not None and not re.search(r"\b\d+(\.\d+)?\s*h\b|hours?", draft):
        out.append(("horizon", f"forecast horizon not stated (write: within {alert['horizon_h']:g} h)"))
    lt = facts.get("lead_time_h")
    if isinstance(lt, (int, float)):
        if not _near(draft, r"lead[\s-]*time", re.compile(rf"(?<![\d.]){lt:.1f}(?![\d])")):
            out.append(("lead_time", f"lead time not stated (write: Lead time to {cat.upper()}: about {lt:.1f} h)"))
    elif isinstance(lt, str) and lt.startswith("not available"):
        if not _near(draft, r"lead[\s-]*time", _NA):
            out.append(("lead_time", f"lead time must say 'not available' (write: Lead time to {cat.upper()}: {lt})"))
    elif isinstance(lt, str) and lt.startswith("within"):
        h = re.escape(lt.split()[1])
        if not _near(draft, r"lead[\s-]*time", re.compile(rf"within\s+{h}\s*(?:h|hours?)\b")):
            out.append(("lead_time", f"lead time must follow the forecast (write: Lead time to {cat.upper()}: {lt})"))
    elif not _near(draft, r"lead[\s-]*time", _NA):
        out.append(("lead_time", f"lead time must say 'not available' (write: Lead time to {cat.upper()}: not available)"))
    if (facts.get("context") or {}).get("rain") == "rain data not available" and "rain data not available" not in low:
        out.append(("rain", "radar rain is missing: write 'rain data not available'"))
    miss = sorted({m.group(0) for m in _MISSING.finditer(draft)})
    if miss:
        out.append(("missing_value", f"missing value written as {', '.join(repr(x) for x in miss)} "
                                     "(write 'not available')"))
    raw = sorted({m.group(0) for m in _RAW_FIELD.finditer(draft)})
    if raw:
        out.append(("raw_field", f"raw field name or boolean in the text: {', '.join(repr(x) for x in raw)} "
                                 "(use plain words, e.g. 'as of', 'data: approved per USGS')"))
    allowed = allowed_abbreviations(facts)
    unknown = sorted({t for t in _CAPS.findall(draft) if t not in allowed})
    if unknown:
        out.append(("abbreviation", "unknown abbreviation " + ", ".join(unknown) + " (use only: "
                                    + ", ".join(sorted(ABBREVIATIONS)) + ", or names exactly as in FACTS)"))
    m = _CAUSAL.search(draft)          # A3.1: on every alert, not only when the context kind is supporting
    if m:
        out.append(("context_kind", f"downstream/nearby gauges are SUPPORTING context only, not a cause or "
                                    f"predictor: '{m.group(0)}'"))
    if _MARKDOWN.search(draft):
        out.append(("markdown", "Markdown in a plain-text advisory (remove ** / __ / # headings)"))
    if alert["mode"] == "SIMULATION" and "SIMULATION" not in draft:
        out.append(("simulation", "SIMULATION label missing for replayed data (write the word SIMULATION in capitals)"))
    if alert["mode"] != "SIMULATION" and "SIMULATION" in draft:
        out.append(("live_label", "SIMULATION label on live data"))
    if alert.get("provisional_data") and "provisional" not in low:
        out.append(("provisional", "provisional-data note missing (use the word 'provisional')"))
    needs_signals = not facts.get("forecasts_disagree") or facts.get("context_disagreement")
    if confidence == "uncertain" and needs_signals and not ("UNCERTAIN" in draft and "evidence" in low and "context" in low):
        out.append(("uncertain", "uncertain case must say UNCERTAIN and state both the evidence and the context signal"))
    if facts.get("data_quality") and not _SUSPECT.search(draft):
        out.append(("suspect_data", "the gauge readings are flagged as a possible sensor fault: say the data are "
                                    "questionable (" + str(facts["data_quality"])[:120] + ")"))
    n = facts.get("update_n") or 1
    ups = [int(m.group(1)) for m in _UPDATE.finditer(draft)]
    if n > 1 and n not in ups:
        out.append(("update", f"repeat alert in the episode: start with 'Update {n}:'"))
    elif n <= 1 and ups:
        out.append(("update", "first alert of the episode: no 'Update n' wording"))
    elif any(u != n for u in ups):
        out.append(("update", f"wrong update number {ups} (this is update {n})"))
    out += _model_rule(draft, alert, facts)
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


def _model_rule(draft: str, alert: dict, facts: dict) -> list[tuple[str, str]]:
    """Core 1 Nov: an alert raised by the model's probability whose stage forecast stays below the category must say
    the two forecasts disagree (UNCERTAIN, the probability, the stage forecast below the category stage) and must never
    present the stage forecast as reaching the category."""
    if not facts.get("forecasts_disagree"):
        return []
    from agentic_ai.advisory import ft
    cat = alert["category"].upper()
    out = []
    fs = ft(alert.get("forecast_stage"))
    low = draft.lower()
    ok = ("UNCERTAIN" in draft and "disagree" in low and "below" in low and fs is not None
          and any(abs(n - fs) <= 0.06 for n in _nums(draft)) and "probab" in low)
    if not ok:
        out.append(("model_rule", "the model's probability and its stage forecast disagree: write UNCERTAIN, the "
                                  f"probability, and the stage forecast {fs} ft below the {cat} stage (copy: "
                                  f"{facts['forecasts_disagree'][:160]})"))
    if re.search(rf"forecast(?:ed)?\s+(?:to\s+)?(?:reach|cross|exceed|rise\s+(?:to|above))\w*\s+(?:the\s+)?"
                 rf"(?:official\s+)?{cat}\b", draft):
        out.append(("model_rule", f"the stage forecast stays below {cat}: never write that the forecast reaches {cat}"))
    return out


def review(draft: str, alert: dict, facts: dict, confidence: str = "normal") -> list[str]:
    """Deterministic checks. Returns a list of failure reasons (empty = pass)."""
    return [r for _, r in review_detailed(draft, alert, facts, confidence)]
