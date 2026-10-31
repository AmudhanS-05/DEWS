"""Thresholds and wording rules shared by the workers, writer and reviewer.

Direction thresholds (rising / not rising) are first values; the EDA tuning is a later step (S2/S3).
"""
from __future__ import annotations

CATEGORIES = ("action", "minor", "moderate", "major")


def category_of(stage: float | None, levels: dict | None) -> str | None:
    """Highest official NWPS category whose stage is at or below `stage` (None below action or without levels)."""
    if stage is None or not levels:
        return None
    hit = [c for c in CATEGORIES if levels.get(c) is not None and stage >= levels[c]]
    return hit[-1] if hit else None


def rank(cat: str | None) -> int:
    return CATEGORIES.index(cat) if cat in CATEGORIES else -1

# Evidence says "rising" when either holds.
EVIDENCE_RISE_RATE_FT_H = 0.3
# Context says "rising" when either holds.
CONTEXT_RAIN_3H_MM = 10.0
CONTEXT_UPSTREAM_RISE_FT_3H = 0.5

# Known stream topology (downstream/upstream gauge site numbers). Onion Creek flows east: Driftwood -> US 183.
TOPOLOGY = {
    "08158700": {"upstream": [], "downstream": ["08159000"]},
    "08159000": {"upstream": ["08158700"], "downstream": []},
}

MAX_ADVISORY_CHARS = 900
MAX_REVISIONS = 2

# The advisory forecasts crossing an official category; it never states a flood as certain.
FORBIDDEN_PHRASES = ("will flood", "will definitely", "is certain", "certainly", "guaranteed", "no doubt",
                     "will be flooded", "predicts a flood", "predicted flood")


# --- Disagreement rule (A2.2): pure functions, used by the evidence/context workers and the coordinator. ---

def evidence_direction(rise_rate_ft_h: float | None, forecast_stage: float | None, level: float | None) -> str:
    """'rising' when the gauge rises at >= EVIDENCE_RISE_RATE_FT_H or the forecast reaches the category stage."""
    rising = (rise_rate_ft_h is not None and rise_rate_ft_h >= EVIDENCE_RISE_RATE_FT_H) or \
             (forecast_stage is not None and level is not None and forecast_stage >= level)
    return "rising" if rising else "not rising"


def context_direction(rain_3h_mm: float | None, upstream_rising: list[bool | None]) -> tuple[str, bool, bool]:
    """(direction, rain_signal, upstream_signal). 'unknown' when there is neither radar rain nor an upstream reading."""
    known_up = [r for r in upstream_rising if r is not None]
    rain_sig = rain_3h_mm is not None and rain_3h_mm >= CONTEXT_RAIN_3H_MM
    up_sig = any(known_up)
    if rain_3h_mm is None and not known_up:
        return "unknown", False, False
    return ("rising" if (rain_sig or up_sig) else "not rising"), rain_sig, up_sig


def disagreement(evidence_dir: str, context_dir: str) -> bool:
    """The signals disagree when context is known and points the other way. Unknown context never disagrees."""
    return context_dir != "unknown" and evidence_dir != context_dir
