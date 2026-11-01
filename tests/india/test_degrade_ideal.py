"""Step C degradations and step D requirement rule (synthetic inputs only)."""
from india import degrade as D
from india import ideal_india as I
from tests.ml import synth


def test_subsample_hourly_and_daily():
    rs = synth.hydrograph("S1", days=5, step_s=900)
    hourly = D.subsample(rs, 1.0)
    assert all(b["reading_ts"] - a["reading_ts"] == 3600 for a, b in zip(hourly, hourly[1:]))
    daily = D.subsample(rs, 24.0, offset_s=2.5 * 3600)
    assert len(daily) in (4, 5) and all(r["reading_ts"] % 86400 == 2.5 * 3600 for r in daily)


def test_gaps_remove_about_the_fraction():
    rs = synth.hydrograph("S1", days=60, step_s=900)
    kept = D.remove_gaps(rs, 0.3, seed=1)
    assert 0.6 <= len(kept) / len(rs) <= 0.75


def test_requirements_pick_coarsest_level_within_tolerance():
    rows = [{"scenario": "baseline", "level": "US native", "model": "rise_rate", "recall": "0.5",
             "false_alarms_per_gauge_year": "2.0"}]
    for scen, lv, rec, fa in (("interval", "1 h", 0.48, 2.0), ("interval", "3 h", 0.2, 2.0),
                              ("delay", "1 h", 0.1, 2.0), ("gaps", "10 %", 0.46, 1.9), ("gaps", "30 %", 0.44, 1.5),
                              ("thresholds", "p99.9", 0.5, 3.0)):
        rows.append({"scenario": scen, "level": lv, "model": "rise_rate", "recall": str(rec),
                     "false_alarms_per_gauge_year": str(fa)})
    req = {r["property"]: list(r.values())[1] for r in I.requirements(rows)}
    assert req == {"interval": "1 h", "delay": "0 h", "gaps": "10 %", "thresholds": "official"}
