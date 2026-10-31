"""Thresholds and wording rules shared by the workers, writer and reviewer.

Direction thresholds (rising / not rising) are first values; the EDA tuning is a later step (S2/S3).
"""
from __future__ import annotations

CATEGORIES = ("action", "minor", "moderate", "major")

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
