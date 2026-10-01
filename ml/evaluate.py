"""Event-based evaluation (Phase 4/8): precision, recall, F1, false alarms,
lead time, and stage error. Pure functions; times are epoch seconds.

Definitions (quote these in the report):
- An alert at time t for category C is a TRUE POSITIVE if the observed stage
  crosses C (or higher) within (t, t + horizon]. Otherwise it is a FALSE ALARM.
- An observed crossing of category C is DETECTED if at least one alert for C or a
  LOWER category was raised in [crossing - horizon, crossing). (A "minor" alert
  counts toward a later "major" crossing; a "major" alert does not count toward a
  "minor" crossing, consistent with the true-positive rule.)
  Its LEAD TIME is crossing time minus the EARLIEST such alert.
- Precision = TP alerts / all alerts; recall = detected crossings / all crossings.
- Lead time can be reported net of feed staleness by subtracting each alert's
  staleness (fetched_at - reading_time) separately; see report_lead_times.
Caveat to state with the numbers: an alert with no recorded crossing may still be
a real flood that the gauge or reports missed ("no report" is not "no flood").
"""
from __future__ import annotations

from math import sqrt
from statistics import median

from common.nwps import CATEGORIES


def _rank(c: str) -> int:
    return CATEGORIES.index(c)


def score_alerts(alerts: list[tuple[float, str]], crossings: list[tuple[float, str]],
                 horizon_h: float) -> dict:
    """alerts / crossings: lists of (time, category)."""
    H = horizon_h * 3600
    tp = 0
    for t, c in alerts:
        if any(t < ct <= t + H and _rank(cc) >= _rank(c) for ct, cc in crossings):
            tp += 1
    fa = len(alerts) - tp
    leads, detected = [], 0
    for ct, cc in crossings:
        prior = [t for t, c in alerts if ct - H <= t < ct and _rank(cc) >= _rank(c)]
        if prior:
            detected += 1
            leads.append((ct - min(prior)) / 3600.0)
    precision = tp / len(alerts) if alerts else None
    recall = detected / len(crossings) if crossings else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and (precision + recall) > 0 else None)
    return {"alerts": len(alerts), "true_positive_alerts": tp, "false_alarms": fa,
            "crossings": len(crossings), "detected": detected,
            "precision": precision, "recall": recall, "f1": f1,
            "median_lead_time_h": median(leads) if leads else None, "lead_times_h": leads}


def stage_errors(pairs: list[tuple[float, float]]) -> dict:
    """pairs of (forecast, observed) stage."""
    errs = [f - o for f, o in pairs if f is not None and o is not None]
    if not errs:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    return {"n": len(errs), "mae": sum(abs(e) for e in errs) / len(errs),
            "rmse": sqrt(sum(e * e for e in errs) / len(errs)), "bias": sum(errs) / len(errs)}


def report_lead_times(lead_times_h: list[float], staleness_h: list[float]) -> dict:
    """Gross vs net-of-feed-staleness lead time (same order, one staleness per lead)."""
    net = [l - s for l, s in zip(lead_times_h, staleness_h)]
    return {"gross_median_h": median(lead_times_h) if lead_times_h else None,
            "net_median_h": median(net) if net else None, "net_lead_times_h": net}
