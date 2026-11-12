"""Does the investigator choose the right tools? Scripted cases, each with the tools a hydrologist would want
(`need`), tools that are reasonable extras (`ok`), and the rest counted as wasted calls.

    python -m agentic_ai.eval.investigator_eval            # the configured LLM (LLM_PROVIDER), canned tool results
    python -m agentic_ai.eval.investigator_eval --csv gate_output/investigator_eval.csv

Per case: needed tools found (recall), calls that were needed or reasonable (precision), stayed within the budget,
every reply valid JSON, no rejected call, findings whose numbers all came from a tool (kept / all). The tools return
canned results, so only the choices are measured, not the database.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from agentic_ai import investigator

LV = {"action": 8.0, "minor": 10.0, "moderate": 14.0, "major": 18.0}
CANNED = {
    "get_recent_windows": [{"window_end": "2023-10-25T05:00:00Z", "stage_last": 7.1, "rise_rate_ft_per_h": 0.9},
                           {"window_end": "2023-10-25T06:00:00Z", "stage_last": 8.3, "rise_rate_ft_per_h": 1.2}],
    "get_rain": {"available": True, "rain_1h": 18.2, "rain_3h": 41.5, "rain_6h": 55.0},
    "get_neighbours": [{"site_no": "08156700", "relation": "upstream", "change_3h_ft": 2.4, "rising": True},
                       {"site_no": "08158000", "relation": "downstream", "change_3h_ft": 0.3, "rising": False}],
    "get_past_floods": {"n": 6, "median_rise_h": 2.5, "recent": [{"category": "minor", "peak_stage_ft": 12.1, "rise_time_h": 1.8}]},
    "get_data_quality": {"suspect": True, "reasons": ["stage jumped 4.0 ft in 15 min and fell back"]},
    "get_gauge_info": {"name": "Shoal Creek at W 12th St, Austin", "lat": 30.28, "lon": -97.75},
    "get_impacts": ["14 ft: water over Lamar Blvd low-water crossing"],
}


def _case(cid, title, need, ok, category="minor", ev=None, cx=None, dis=None, conf="normal", alert=None):
    a = {"site_no": "08156800", "category": category, "mode": "SIMULATION", "horizon_h": 3, "probability": 0.86,
         "forecast_stage": 11.2, "current_stage": 8.3, "alert_rule": "forecast_stage", "raised_at": "2023-10-25T06:00:00Z",
         **(alert or {})}
    e = {"direction": "rising", "stage_last": 8.3, "rise_rate_ft_per_h": 1.2, "stage_12h_ago": 3.0, "forecast_stage": 11.2,
         "p_minor": 0.86, "official_levels": LV, "past_floods_n": 6, "data_quality": {"suspect": False}, **(ev or {})}
    c = {"direction": "rising", "kind": "rain", "basis": "leading", "summary": "rain 3 h 41.5 mm", **(cx or {})}
    return {"id": cid, "title": title, "need": set(need), "ok": set(ok), "alert": a, "ev": e, "cx": c, "conf": conf, "dis": dis}


CASES = [
    _case("rain_vs_flat", "heavy rain, gauge flat (signals disagree)", ["get_rain", "get_neighbours"], ["get_recent_windows", "get_past_floods"],
          ev={"direction": "not rising", "rise_rate_ft_per_h": 0.0}, conf="uncertain",
          dis={"evidence": "not rising (rise 0.0 ft/h)", "context": "rising (rain 3h 41.5 mm)"}),
    _case("upstream_rising", "upstream gauge rising, this gauge flat", ["get_neighbours"], ["get_recent_windows", "get_rain", "get_past_floods"],
          ev={"direction": "not rising", "rise_rate_ft_per_h": 0.1}, cx={"kind": "upstream", "summary": "upstream rising 2.4 ft in 3 h"},
          conf="uncertain", dis={"evidence": "not rising", "context": "rising (upstream gauges, a leading signal)"}),
    _case("model_vs_stage", "probability fired, stage forecast below MINOR", ["get_recent_windows", "get_past_floods"], ["get_rain", "get_neighbours"],
          ev={"forecast_stage": 9.1}, alert={"alert_rule": "alert_by_model", "forecast_stage": 9.1}, conf="uncertain",
          dis={"kind": "model", "probability": 0.86, "forecast_stage": 9.1, "category_stage": 10.0}),
    _case("sensor_spike", "readings look faulty", ["get_data_quality"], ["get_recent_windows", "get_neighbours"],
          ev={"data_quality": {"suspect": True, "reasons": ["spike"]}}),
    _case("major", "MAJOR forecast", ["get_impacts"], ["get_past_floods", "get_neighbours", "get_rain", "get_recent_windows"],
          category="major", ev={"forecast_stage": 19.0}, alert={"forecast_stage": 19.0}),
    _case("moderate", "MODERATE forecast, signals agree", ["get_impacts"], ["get_past_floods", "get_neighbours", "get_rain"],
          category="moderate", ev={"forecast_stage": 14.6}, alert={"forecast_stage": 14.6}),
    _case("fast_rise", "rising 3 ft/h, how fast do floods here rise", ["get_past_floods"], ["get_recent_windows", "get_rain", "get_neighbours"],
          ev={"rise_rate_ft_per_h": 3.0}),
    _case("clear_minor", "clear MINOR, everything agrees", [], ["get_past_floods", "get_neighbours", "get_rain", "get_recent_windows"]),
    _case("no_rain_data", "no rain data, gauge rising", ["get_neighbours"], ["get_recent_windows", "get_past_floods"],
          cx={"kind": "none", "basis": "none", "summary": "no rain data"}),
    _case("falling_after_peak", "gauge falling but forecast above MINOR", ["get_recent_windows"], ["get_neighbours", "get_rain", "get_past_floods"],
          ev={"direction": "falling", "rise_rate_ft_per_h": -0.6}, conf="uncertain",
          dis={"evidence": "falling (rise -0.6 ft/h)", "context": "rising (rain 3h 41.5 mm)"}),
]


def run_case(case: dict, complete) -> dict:
    calls = []

    def call(name, *a, **k):
        calls.append(name)
        return CANNED[name]

    res = investigator.investigate(complete, case["alert"], case["ev"], case["cx"], case["conf"], case["dis"], call)
    chosen = [s["tool"] for s in res["steps"] if not s.get("rejected")]
    good = case["need"] | case["ok"]
    found = case["need"] & set(chosen)
    n_find = len(res["findings"]) + len(res.get("dropped_findings") or [])
    return {"case": case["id"], "title": case["title"], "chosen": " ".join(chosen) or "(none)",
            "need": " ".join(sorted(case["need"])) or "(none)",
            "recall": 1.0 if not case["need"] else round(len(found) / len(case["need"]), 2),
            "precision": 1.0 if not chosen else round(sum(t in good for t in chosen) / len(chosen), 2),
            "within_budget": len(chosen) <= investigator.MAX_CALLS, "valid_replies": res["stopped"] in ("done", "budget"),
            "rejected": sum(1 for s in res["steps"] if s.get("rejected")),
            "findings_kept": f"{len(res['findings'])}/{n_find}", "stopped": res["stopped"], "tokens": res["tokens"]}


def summary(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {"cases": len(rows), "mean_recall": round(sum(r["recall"] for r in rows) / n, 2),
            "mean_precision": round(sum(r["precision"] for r in rows) / n, 2),
            "within_budget": sum(r["within_budget"] for r in rows), "valid": sum(r["valid_replies"] for r in rows),
            "rejected_calls": sum(r["rejected"] for r in rows)}


def main(argv=None):
    from agentic_ai import llm
    from agentic_ai.env import load_env
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", help="also write the per-case rows here")
    a = p.parse_args(argv)
    if llm.provider() == "none":
        sys.exit("LLM_PROVIDER is none: set it (e.g. groq) to evaluate the investigator")
    rows = [run_case(c, llm.complete) for c in CASES]
    for r in rows:
        print(f"{r['case']:<20} recall {r['recall']:.2f}  precision {r['precision']:.2f}  chose: {r['chosen']:<55} need: {r['need']}")
    print(json.dumps(summary(rows)))
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
