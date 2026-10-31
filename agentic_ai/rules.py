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


def context_signals(rain_3h_mm: float | None, upstream_rising: list[bool | None],
                    supporting_rising: list[bool | None] = ()) -> dict:
    """Context worker rule (pure). Two kinds of signal, never mixed up:

    - LEADING: radar rain >= CONTEXT_RAIN_3H_MM or an upstream gauge rising. Water that is on its way to the gauge.
    - SUPPORTING: a downstream or nearby gauge rising in the same window. It shows the storm is affecting the area,
      it does not cause or predict the rise at this gauge (downstream water cannot flow upstream).

    direction: 'rising' if any leading or supporting signal is up; 'not rising' if at least one reading exists and
    none is up; 'unknown' when there is no radar rain and no gauge reading at all.
    basis: 'leading' when rain or an upstream reading is known, else 'supporting' when only downstream/nearby
    readings are known, else 'none'. Only a leading basis can disagree with the gauge evidence (`disagreement`)."""
    up = [r for r in upstream_rising if r is not None]
    sup = [r for r in supporting_rising if r is not None]
    rain_sig = rain_3h_mm is not None and rain_3h_mm >= CONTEXT_RAIN_3H_MM
    up_sig, sup_sig = any(up), any(sup)
    if rain_3h_mm is not None or up:
        basis = "leading"
    elif sup:
        basis = "supporting"
    else:
        return {"direction": "unknown", "basis": "none", "rain_signal": False, "upstream_signal": False,
                "supporting_signal": False, "kind": "none"}
    direction = "rising" if (rain_sig or up_sig or sup_sig) else "not rising"
    kind = "leading" if (rain_sig or up_sig) else ("supporting" if sup_sig else "none")
    return {"direction": direction, "basis": basis, "rain_signal": rain_sig, "upstream_signal": up_sig,
            "supporting_signal": sup_sig, "kind": kind}


def context_direction(rain_3h_mm: float | None, upstream_rising: list[bool | None]) -> tuple[str, bool, bool]:
    """(direction, rain_signal, upstream_signal) from the leading signals only (kept for callers of the S1 rule)."""
    c = context_signals(rain_3h_mm, upstream_rising)
    return c["direction"], c["rain_signal"], c["upstream_signal"]


def disagreement(evidence_dir: str, context_dir: str, basis: str = "leading") -> bool:
    """The signals disagree when context is known, rests on a LEADING signal (rain or upstream gauges) and points the
    other way. Unknown context, or context resting only on downstream/nearby gauges, never disagrees: a quiet
    neighbouring gauge does not contradict a rise at this one (a flash flood can stay local)."""
    return context_dir != "unknown" and basis == "leading" and evidence_dir != context_dir
