"""NWPS gauge metadata: official flood categories (action/minor/moderate/major).

These are the labels the forecaster is scored against, so we never invent a
threshold. The parser is deliberately tolerant because the exact JSON layout
has only been seen through a summarising fetch tool, not raw.

VERIFY ON YOUR MACHINE: `python -m tools.fetch_sample --nwps <LID>` saves a real
response to tests/fixtures/live_nwps_gauge.json and `pytest` checks this parser
against it.
"""
from __future__ import annotations

CATEGORIES = ("action", "minor", "moderate", "major")
MISSING = -9999.0


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v == MISSING else v


def parse_flood_categories(doc: dict) -> dict:
    """Return {'action':ft|None, 'minor':..., 'moderate':..., 'major':..., 'units':str|None,
    'lid':..., 'name':..., 'usgs_id':...}."""
    flood = doc.get("flood") or {}
    cats = flood.get("categories") or doc.get("categories") or {}
    out = {}
    for c in CATEGORIES:
        entry = cats.get(c)
        if isinstance(entry, dict):
            out[c] = _num(entry.get("stage"))
        else:
            out[c] = _num(entry)
    out["units"] = flood.get("stageUnits") or doc.get("stageUnits")
    out["lid"] = doc.get("lid")
    out["name"] = doc.get("name")
    out["usgs_id"] = doc.get("usgsId")
    return out


def category_of(stage: float, cats: dict) -> str | None:
    """Highest category whose stage threshold is reached, or None."""
    reached = None
    for c in CATEGORIES:
        t = cats.get(c)
        if t is not None and stage >= t:
            reached = c
    return reached
