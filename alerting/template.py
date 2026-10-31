"""Advisory v1 (C1.5): deterministic template text from an `alerts.flood` record. No LLM, no network, no invention:
every number comes from the alert record, a missing probability is said to be missing. The agents' v2 replaces it.
"""
from __future__ import annotations

from common.nwps import CATEGORIES, category_of


def _ft(x) -> str:
    return f"{x:.1f}" if isinstance(x, (int, float)) else "n/a"


def render_v1(alert: dict) -> str:
    cat = alert["category"]
    levels = alert.get("official_levels") or {}
    horizon = alert.get("horizon_h")
    h = f"{horizon:g}" if isinstance(horizon, (int, float)) else "?"
    lines = []
    if alert.get("mode") == "SIMULATION":
        lines.append("SIMULATION: replayed historical data, not a real event.")
    head = "UPGRADE: " if alert.get("kind") == "upgrade" else ""
    lines.append(f"{head}{alert.get('site_name') or alert['site_no']}: forecast to reach {cat.upper()} flood stage "
                 f"({_ft(levels.get(cat))} ft) within {h} h.")
    cur = alert.get("current_stage")
    now_cat = category_of(cur, levels) if isinstance(cur, (int, float)) and levels else None
    lines.append(f"Gauge now: {_ft(cur)} ft" + (f" (already at {now_cat.upper()})." if now_cat else "."))
    p = alert.get("probability")
    prob = (f"probability of reaching {cat.upper()}: {p * 100:.0f}%" if isinstance(p, (int, float))
            else "no probability from this model")
    lines.append(f"Forecast: {_ft(alert.get('forecast_stage'))} ft in {h} h; {prob} ({alert.get('model')} model).")
    if levels:
        lines.append("Official stages: " + ", ".join(f"{c} {_ft(levels[c])}" for c in CATEGORIES if c in levels) + " ft.")
    if alert.get("provisional_data"):
        lines.append("Data are provisional USGS readings and may be revised.")
    lines.append("Draft for approval by a duty officer; not sent until approved.")
    return "\n".join(lines)
