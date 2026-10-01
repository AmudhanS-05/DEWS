"""Pure analysis helpers on a gauge time series: [(epoch_seconds, stage), ...]
sorted by time. Used by the Phase 0 gate check and by evaluation.

Definitions (stated so the report can quote them):
- crossing: first reading at or above a threshold after the series was below it.
  Readings above the threshold within `min_gap_h` hours of the previous above-
  threshold reading belong to the same event.
- rise start: the nearest trough before the crossing (see rise_start), searched
  at most `lookback_h` hours back.
- rise time: crossing time minus rise-start time. It approximates how much warning
  the river itself gives; forecast lead time cannot usefully exceed it.
"""
from __future__ import annotations

from statistics import median

Series = list[tuple[float, float]]


def find_crossings(series: Series, threshold: float, min_gap_h: float = 24.0) -> list[int]:
    """Indices of readings where a new event first reaches `threshold`."""
    idx: list[int] = []
    last_above_ts = None
    prev_below = True
    for i, (ts, v) in enumerate(series):
        if v >= threshold:
            new_event = last_above_ts is None or (ts - last_above_ts) > min_gap_h * 3600
            if prev_below and new_event:
                idx.append(i)
            last_above_ts = ts
            prev_below = False
        else:
            prev_below = True
    return idx


def rise_start(series: Series, crossing_idx: int, lookback_h: float = 48.0, tol: float = 0.1) -> int:
    """Index of the nearest trough before the crossing.

    Walks backwards from the crossing keeping the lowest reading seen, and stops
    once the stage climbs back more than `tol` (same units as the series, ft for
    USGS gage height) above that low, or once `lookback_h` is exceeded. This finds
    the start of THIS rise rather than an older, lower reading from a previous event.
    """
    t_cross = series[crossing_idx][0]
    best = crossing_idx
    for j in range(crossing_idx, -1, -1):
        ts, v = series[j]
        if t_cross - ts > lookback_h * 3600:
            break
        if v < series[best][1]:
            best = j
        elif v > series[best][1] + tol:
            break
    return best


def rise_time_h(series: Series, crossing_idx: int, lookback_h: float = 48.0, tol: float = 0.1) -> float:
    j = rise_start(series, crossing_idx, lookback_h, tol)
    return (series[crossing_idx][0] - series[j][0]) / 3600.0


def event_peak(series: Series, crossing_idx: int, window_h: float = 72.0) -> tuple[int, float]:
    """(index, stage) of the highest reading within window_h after the crossing."""
    t0 = series[crossing_idx][0]
    best = crossing_idx
    for j in range(crossing_idx, len(series)):
        if series[j][0] - t0 > window_h * 3600:
            break
        if series[j][1] > series[best][1]:
            best = j
    return best, series[best][1]


def median_interval_min(series: Series) -> float | None:
    if len(series) < 2:
        return None
    gaps = [(b[0] - a[0]) / 60.0 for a, b in zip(series, series[1:]) if b[0] > a[0]]
    return median(gaps) if gaps else None


def largest_gap_h(series: Series) -> float | None:
    if len(series) < 2:
        return None
    return max((b[0] - a[0]) for a, b in zip(series, series[1:])) / 3600.0
