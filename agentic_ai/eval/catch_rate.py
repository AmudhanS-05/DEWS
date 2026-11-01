"""A3.1 reviewer catch rate on corrupted drafts:  python -m agentic_ai.eval.catch_rate [--live-texts FILE]

Base drafts: the deterministic template advisory for five synthetic-but-shaped alerts (the measured DRWT2 May 2019
major alert, an illustrative MINOR alert with probability and rain, its LIVE twin, a repeat "Update 2" alert, and an
UNCERTAIN case), plus, with --live-texts, LLM-written advisories saved by the A4.2 evaluation (JSON list of
{name, text, alert, facts, confidence}). Every base must pass the reviewer first (false alarms are reported); then each
applicable seeded error (agentic_ai/eval/corrupt.py) is planted once and the deterministic reviewer must catch it.
Writes agentic_ai/eval/reviewer_catch_rate.csv and .md. No LLM, no database, no network.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from agentic_ai import advisory
from agentic_ai.eval import corrupt

HERE = Path(__file__).resolve().parent
LV = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}
NB = [{"site_no": "08159000", "name": "Onion Creek at US 183", "relation": "downstream", "distance_km": 30.1,
       "stage_last": 9.4, "change_ft_over_window": 6.1, "hours_in_window": 4, "rising": True},
      {"site_no": "08158840", "name": "Slaughter Creek at FM 1826", "relation": "nearby", "distance_km": 12.3,
       "stage_last": 8.0, "change_ft_over_window": 7.4, "hours_in_window": 4, "rising": True}]


def base_cases() -> list[dict]:
    real = {"category": "major", "mode": "SIMULATION", "probability": None, "horizon_h": 3.0, "provisional_data": False,
            "official_levels": LV, "raised_at": "2026-10-31T08:00:00Z", "as_of": "2019-05-04T02:00:00Z",
            "site_no": "08158700", "site_name": "Onion Creek near Driftwood", "forecast_stage": 24.72,
            "current_stage": 8.16, "model": "rise_rate"}
    real_ev = {"stage_last": 8.16, "rise_rate_ft_per_h": 5.52, "forecast_stage": 24.72, "direction": "rising"}
    real_cx = {"rain": {"available": False}, "neighbours": NB, "impacts": [], "direction": "rising",
               "kind": "supporting", "basis": "supporting"}
    minor = {"category": "minor", "mode": "SIMULATION", "probability": 0.79, "horizon_h": 6.0, "provisional_data": True,
             "official_levels": LV, "raised_at": "2019-05-04T04:00:00Z", "site_no": "08158700",
             "site_name": "Onion Creek near Driftwood", "forecast_stage": 6.8, "current_stage": 2.6, "model": "s3_v0"}
    ev = {"stage_last": 2.6, "rise_rate_ft_per_h": 0.7, "forecast_stage": 6.8, "p_minor": 0.79, "direction": "rising"}
    cx = {"rain": {"available": True, "rain_3h": 27.0, "rain_6h": 41.0}, "neighbours": NB[:1], "direction": "rising",
          "kind": "leading", "basis": "leading", "impacts": ["Minor lowland flooding along Onion Creek (NWS)."]}
    flat_cx = {**cx, "rain": {"available": True, "rain_3h": 0.0, "rain_6h": 0.0}, "neighbours": [], "direction": "not rising",
               "kind": "none"}
    dis = {"evidence": "rising (rise 0.7 ft/h, forecast 6.8 ft)", "context": "not rising (rain 3h 0.0 mm)"}
    specs = [("drwt2_2019_major_measured", real, real_ev, real_cx, "normal", 1, None),
             ("minor_p79_rain_sim", minor, ev, cx, "normal", 1, None),
             ("minor_p79_rain_live", {**minor, "mode": "LIVE"}, ev, cx, "normal", 1, None),
             ("moderate_update2_sim", {**minor, "category": "moderate", "forecast_stage": 7.9, "probability": 0.62},
              {**ev, "forecast_stage": 7.9}, cx, "normal", 2, None),
             ("minor_uncertain_sim", minor, ev, flat_cx, "uncertain", 1, dis)]
    out = []
    for name, a, e, c, conf, n, d in specs:
        facts = advisory.build_facts(a, e, c, n)
        out.append({"name": name, "text": advisory.template_text(a, e, c, conf, n, d), "alert": a, "facts": facts,
                    "confidence": conf, "source": "template"})
    return out


def write(res: dict, stem: str = "reviewer_catch_rate") -> tuple[Path, Path]:
    p_csv, p_md = HERE / f"{stem}.csv", HERE / f"{stem}.md"
    with open(p_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case", "kind", "caught", "by", "other_checks", "reason"])
        w.writeheader()
        w.writerows(res["rows"])
    lines = [f"# Reviewer catch rate on seeded errors (A3.1)", "",
             f"Deterministic reviewer (`agentic_ai/checks.py`), {len(res['cases'])} correct base drafts "
             f"({', '.join(sorted({c['source'] for c in res['cases']}))}), one seeded error per corrupted draft. "
             f"Base drafts flagged although correct (false alarms): {len(res['base_false_alarms'])}"
             + (f" ({', '.join(res['base_false_alarms'])})" if res["base_false_alarms"] else "") + ".", "",
             f"**Caught {res['caught']} of {res['total']} seeded errors "
             f"({100 * res['caught'] / max(res['total'], 1):.1f} %).**", "",
             "| error kind | caught / seeded | in SLICES A3.1 list |", "|---|---|---|"]
    for k, (c, t) in res["by_kind"].items():
        lines.append(f"| {k} | {c} / {t} | {'yes' if k in corrupt.SLICE_KINDS else ''} |")
    missed = [r for r in res["rows"] if not r["caught"]]
    if missed:
        lines += ["", "Missed:", ""] + [f"- {r['case']} / {r['kind']} (other checks fired: {r['other_checks']})" for r in missed]
    p_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p_csv, p_md


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--live-texts", help="JSON list of LLM-written base advisories (from agentic_ai.eval.evaluate)")
    ap.add_argument("--stem", default="reviewer_catch_rate")
    args = ap.parse_args(argv)
    cases = base_cases()
    if args.live_texts:
        cases += [{**c, "source": c.get("source", "llm")} for c in json.loads(Path(args.live_texts).read_text("utf-8"))]
    res = corrupt.catch_rate(cases)
    res["cases"] = cases
    p_csv, p_md = write(res, args.stem)
    print(f"caught {res['caught']}/{res['total']} seeded errors on {len(cases)} base drafts; "
          f"base false alarms: {res['base_false_alarms'] or 'none'}; wrote {p_csv.name}, {p_md.name}")
    for k, (c, t) in res["by_kind"].items():
        print(f"  {k:26s} {c}/{t}")
    return res


if __name__ == "__main__":
    main()
