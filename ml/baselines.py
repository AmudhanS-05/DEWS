"""Forecast baselines the ML model must beat (Phase 4).

Every forecaster maps the latest window aggregate to a stage `horizon_h` hours
ahead. The alert decision is then: does the forecast reach an official NWPS
category? Thresholds come from NWPS, never from us.
"""
from __future__ import annotations

from common.nwps import CATEGORIES, category_of


def persistence(agg: dict, horizon_h: float) -> float | None:
    """Stage stays where it is. The 'do nothing clever' baseline."""
    return agg.get("stage_last")


def rise_rate(agg: dict, horizon_h: float, damping: float = 1.0) -> float | None:
    """Extrapolate the window's rise rate linearly. Falling stages are not
    extrapolated below the current value (we only care about rises)."""
    s, rr = agg.get("stage_last"), agg.get("rise_rate_ft_per_h")
    if s is None:
        return None
    if rr is None or rr <= 0:
        return s
    return s + damping * rr * horizon_h


BASELINES = {"persistence": persistence, "rise_rate": rise_rate}


def alert_category(forecast_stage: float | None, cats: dict, min_category: str = "action") -> str | None:
    """Category the forecast reaches, if at or above `min_category`."""
    if forecast_stage is None:
        return None
    c = category_of(forecast_stage, cats)
    if c is None or CATEGORIES.index(c) < CATEGORIES.index(min_category):
        return None
    return c
