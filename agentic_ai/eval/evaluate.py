"""A4.2 agent evaluation and A5.1 ablation on replayed alerts from the measured USGS record.

    python -m agentic_ai.eval.evaluate --dsn <scratch db> [--configs multi,single,template,llm_down] [--gap 65]
    python -m agentic_ai.eval.evaluate --report-only        # rebuild the tables from agentic_ai/eval/runs.jsonl

Loads agentic_ai/eval/replay_events.json (build_replays.py) into the database (mode SIMULATION), then runs every
non-retracted alert through each configuration:
  multi     the LangGraph multi-agent graph (coordinator, evidence || context, reconcile, writer, reviewer with up to
            2 revisions; tools over MCP), LLM_PROVIDER from the environment (groq)
  single    A5.1 baseline: ONE LLM call writes the advisory from the raw tool outputs and the same rules in prose
            (no workers, no precomputed facts, no reviewer loop); scored afterwards with the same deterministic checks
  template  LLM_PROVIDER=none: the deterministic template through the graph (no LLM)
  llm_down  LLM_FORCE_DOWN=1 through the graph (C4.3: v1 must stay, nothing blocked)
Live LLM runs are spaced --gap seconds (Groq free tier). Every run is appended to runs.jsonl (resumable).
Metrics: outcome, attempts, deterministic checks on the final text, LLM judge verdict, faithfulness (share of numbers
in the text found in the current evidence), tokens, v1 -> v2 time (run start to saved draft; the template v1 exists
from the moment the alert is raised), disagreement outcome; reviewer catch rate on seeded errors in the LLM texts.
Writes agent_eval.csv / .md, ablation.md, agent_eval_runs.csv (evaluation_runs rows: python -m ml.import_runs ...),
llm_texts.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from agentic_ai import advisory, checks, llm

HERE = Path(__file__).resolve().parent
REPLAYS = HERE / "replay_events.json"
RUNS = HERE / "runs.jsonl"
SINGLE_COMPLETE = None      # tests: a scripted completion for the single agent only

SINGLE_SYSTEM = (
    "You write ONE flood advisory for a duty officer to approve, from DATA (raw outputs of the database tools, JSON). "
    "It is a forecast of crossing an official NWS category, never a statement that a flood will happen. Plain ASCII "
    "text, no Markdown. An automatic checker applies every rule below.")
SINGLE_RULES = """RULES:
1. Numbers only from DATA (copy them; the only computation allowed is the lead time in rule 5). Stages and rates in ft with one decimal (24.7 ft), probabilities as whole percentages (79%).
2. Name the alert's official category in capitals (ACTION, MINOR, MODERATE, MAJOR) with its official stage from official_levels. If the forecast stage is at or above a higher official category's stage, say the forecast reaches that higher category (with its stage) and that the alert category is the alert's; never use any other threshold or category name.
3. Say 'forecast to reach'; no certainty language (never 'will flood', 'certainly', 'guaranteed').
4. Probability: the alert's probability as a whole %, or 'probability: not available' when it is null; if the forecast row gives a probability for another category, you may add it ('probability of reaching MINOR: 97%').
5. State the horizon in hours ('within 3 h'). Lead time to the alert category: compute (official stage - current stage) / rise rate; if that is within the horizon write it with one decimal ('Lead time to MAJOR: about 0.9 h'); if it is longer than the horizon (or the gauge is not rising) but the forecast stage reaches the category, write 'Lead time to MAJOR: within 3 h (model forecast)' and no rise-rate number; if the gauge is already above the category write 'Lead time to MAJOR: not available'.
6. Radar rain: if rain is not available, write 'rain data not available'.
7. Never write null, None, NaN or '?' (write 'not available'); never write a DATA field name (words joined with _) or true/false; plain words.
8. Upstream gauges and radar rain are LEADING signals; downstream and nearby gauges are SUPPORTING context only (never a cause, a leading signal or a predictor of the rise here). Do not say why the water rises.
9. If mode is SIMULATION, start with the word SIMULATION and say it is a replay of a historical USGS gauge record, not a real-time event. Live data never says SIMULATION.
10. If the gauge data are provisional, use the word 'provisional'. If data_quality says suspect, say the readings are questionable (possible sensor fault).
11. If a leading signal (rain >= 10 mm in 3 h or an upstream gauge rising) disagrees with the gauge (rising vs not rising), write UNCERTAIN and state both the evidence (gauge) and the context signal, and ask for a re-check at the next window.
12. If earlier alerts of the same episode exist (episode alerts raised before this one), start with 'Update n:' where n = 1 + number of earlier alerts; otherwise no 'Update' wording.
13. Capitalised abbreviations only: NWS, USGS, NOAA, MRMS, NWPS, UTC, the category names, SIMULATION, UNCERTAIN, and names exactly as in DATA.
14. At most 800 characters."""


# ---------------------------------------------------------------------------------------------- data loading

def load_replays(conn, path: Path | None = None) -> list[dict]:
    """Insert the replayed events (SIMULATION) and return the alerts to evaluate, in time order."""
    path = path or REPLAYS
    from psycopg2.extras import Json
    from streams import forecaster
    from agentic_ai.db import ensure_schema
    ensure_schema(conn, include_core=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    gauges = forecaster.load_gauges()
    alerts = []
    with conn.cursor() as cur:
        for ev in data["events"]:
            for s in ev["sites"]:
                g = gauges.get(s)
                if g:
                    lv = g["levels"]
                    cur.execute("INSERT INTO gauges (site_no, nwps_lid, name, stage_units, action_stage, minor_stage, "
                                "moderate_stage, major_stage) VALUES (%s,%s,%s,'ft',%s,%s,%s,%s) ON CONFLICT (site_no) "
                                "DO NOTHING", (s, g.get("lid"), g.get("name"), lv["action"], lv["minor"],
                                               lv["moderate"], lv["major"]))
            for w in ev["windows"]:
                cur.execute("INSERT INTO windowed_aggregates (site_no, window_start, window_end, mode, n_readings, "
                            "stage_last, stage_max, stage_min, rise_rate_ft_per_h, rain_1h, rain_3h, rain_6h, provisional) "
                            "VALUES (%(site_no)s,%(window_start)s,%(window_end)s,%(mode)s,%(n_readings)s,%(stage_last)s,"
                            "%(stage_max)s,%(stage_min)s,%(rise_rate_ft_per_h)s,%(rain_1h)s,%(rain_3h)s,%(rain_6h)s,"
                            "%(provisional)s) ON CONFLICT (site_no, window_start, mode) DO UPDATE SET "
                            "stage_last = EXCLUDED.stage_last, rise_rate_ft_per_h = EXCLUDED.rise_rate_ft_per_h, "
                            "rain_1h = EXCLUDED.rain_1h, rain_3h = EXCLUDED.rain_3h, rain_6h = EXCLUDED.rain_6h", w)
            for r in ev["risk"]:
                cur.execute("INSERT INTO risk_scores (site_no, window_start, mode, model, horizon_h, forecast_stage, "
                            "p_action, p_minor, p_moderate, p_major) VALUES (%(site_no)s,%(window_start)s,%(mode)s,"
                            "%(model)s,%(horizon_h)s,%(forecast_stage)s,%(p_action)s,%(p_minor)s,%(p_moderate)s,"
                            "%(p_major)s) ON CONFLICT (site_no, window_start, mode, model, horizon_h) DO UPDATE SET "
                            "forecast_stage = EXCLUDED.forecast_stage, p_minor = EXCLUDED.p_minor", r)
            for t, v, p in ev["readings"]:
                cur.execute("INSERT INTO gauge_readings (site_no, parameter, series, reading_time, value, qualifiers, mode) "
                            "VALUES (%s,'00065','eval',to_timestamp(%s),%s,%s,'SIMULATION') ON CONFLICT DO NOTHING",
                            (ev["site_no"], t, v, ["P"] if p else ["A"]))
            standing = {}
            for a in ev["alerts"]:
                if a["kind"] == "retract":
                    standing.pop(a["alert_id"], None)
                    cur.execute("UPDATE alerts SET status = 'retracted' WHERE alert_id = %s", (a["alert_id"],))
                    continue
                cur.execute("INSERT INTO alerts (alert_id, site_no, mode, category, probability, horizon_h, "
                            "provisional_data, status, raised_at, details) VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s) "
                            "ON CONFLICT (alert_id) DO NOTHING",
                            (a["alert_id"], a["site_no"], a["mode"], a["category"], a["probability"], a["horizon_h"],
                             a["provisional_data"], a["raised_at"], Json({"window_start": a["window_start"],
                                                                           "window_start_ts": a["window_start_ts"],
                                                                           "alert_rule": a.get("alert_rule"),
                                                                           "tau": a.get("tau")})))
                alerts.append({**a, "event": f"{ev['lid']} {ev['crossing_utc'][:10]}"})
    return alerts


# ---------------------------------------------------------------------------------------------- the single agent

def single_agent(tools, alert_id: str) -> dict:
    """A5.1 baseline: gather the same tool outputs, one LLM call writes the advisory. No revision, no judge."""
    t0 = time.monotonic()
    a = tools.get_alert(alert_id)
    site, until, mode = a["site_no"], a.get("as_of") or a["raised_at"], a["mode"]
    hist = tools.get_episode_history(alert_id)
    data = {"alert": a, "gauge": tools.get_gauge_info(site), "official_levels": tools.get_official_levels(site),
            "recent_windows": [{k: w.get(k) for k in ("window_start", "stage_last", "rise_rate_ft_per_h", "rain_3h",
                                                       "provisional")} for w in tools.get_recent_windows(site, 6, until, mode)],
            "forecast": tools.get_forecast(site, until, mode, a.get("horizon_h")),
            "rain": tools.get_rain(site, 6, until, mode), "neighbours": tools.get_neighbours(site, until, mode),
            "impacts": tools.get_impacts(site, a["category"]),
            "episode_alerts": [{k: h.get(k) for k in ("alert_id", "category", "raised_at")} for h in hist["alerts"]],
            "data_quality": tools.get_data_quality(site, until, mode)}
    user = f"DATA (JSON):\n{json.dumps(data, default=str)}\n\n{SINGLE_RULES}"
    r = (SINGLE_COMPLETE or llm.complete)(SINGLE_SYSTEM, user)
    text = checks.normalize(r.text) if r.text else None
    return {"outcome": "written" if text else "llm_failed", "draft": text, "tokens": r.tokens, "attempts": 1,
            "elapsed_s": round(time.monotonic() - t0, 2), "provider": r.provider, "model": r.model,
            "llm_error": r.error, "prompt_tokens": r.prompt_tokens, "output_tokens": r.output_tokens}


# ---------------------------------------------------------------------------------------------- scoring

def faithfulness(text: str | None, facts: dict | None) -> tuple[int, int]:
    """(numbers in the text found in the current evidence, numbers in the text)."""
    if not text or not facts:
        return 0, 0
    n = len(list(checks._NUM.finditer(checks.normalize(text))))
    return n - len(checks.check_numbers(text, facts)), n


def _judge(events: list[dict]) -> str | None:
    v = [e["detail"].get("llm_judgement") for e in events if e["agent"] == "reviewer" and e["kind"] == "verdict"]
    v = [x for x in v if x]
    return v[-1][:60] if v else None


def score(cfg: str, alert: dict, res: dict, facts: dict | None, confidence: str | None, ref_alert: dict | None) -> dict:
    text = res.get("draft") if res.get("outcome") in ("saved", "written") else None
    ok_n, n = faithfulness(text, facts)
    fails = checks.review_detailed(text, ref_alert, facts, confidence or "normal") if text and facts and ref_alert else []
    ev = res.get("events") or []
    writer_ends = [e["detail"] for e in ev if e["agent"] == "writer" and e["kind"] == "end"]
    return {"config": cfg, "event": alert["event"], "alert_id": alert["alert_id"], "category": alert["category"],
            "kind": alert["kind"], "probability": alert["probability"], "model": alert["model"],
            "outcome": res.get("outcome"), "attempts": res.get("attempts"), "tokens": res.get("tokens", 0),
            "elapsed_s": res.get("elapsed_s"), "llm_text": bool(writer_ends and writer_ends[-1].get("llm")) if cfg != "single"
            else bool(text), "provider": (writer_ends[-1].get("provider") if writer_ends else res.get("provider")),
            "confidence": res.get("confidence") or confidence, "disagreement": bool(res.get("disagreement")),
            "uncertain_in_text": bool(text and "UNCERTAIN" in text),
            "checks_failed": ";".join(i for i, _ in fails), "checks_pass": bool(text) and not fails,
            "numbers_ok": ok_n, "numbers": n, "faithful": bool(text) and ok_n == n,
            "judge": _judge(ev), "llm_down": bool(res.get("llm_down")), "chars": len(text) if text else 0,
            "update_n": res.get("update_n"), "via_mcp": (res.get("tools_via") or {}).get("mcp") if isinstance(res.get("tools_via"), dict) else 0,
            "text": text, "error": res.get("error") or res.get("llm_error")}


# ---------------------------------------------------------------------------------------------- runner

def _done() -> set:
    if not RUNS.exists():
        return set()
    return {(r["config"], r["alert_id"]) for r in map(json.loads, RUNS.read_text(encoding="utf-8").splitlines()) if r}


def run(dsn: str, configs: list[str], gap_s: float, limit: int | None = None) -> None:
    from agentic_ai import graph
    from agentic_ai.db import connect
    from agentic_ai.tools import Tools
    conn = connect(dsn)
    alerts = load_replays(conn)
    alerts = alerts[:limit] if limit else alerts
    tools = Tools(conn)
    done = _done()
    ref: dict[str, dict] = {}
    for r in map(json.loads, RUNS.read_text(encoding="utf-8").splitlines() if RUNS.exists() else []):
        if r.get("facts"):
            ref[r["alert_id"]] = r
    print(f"{len(alerts)} alerts, configs {configs}, LLM_PROVIDER={llm.provider()} ({llm.model_name(llm.provider())})",
          flush=True)
    last_llm = 0.0
    for a in alerts:
        for cfg in configs:
            if (cfg, a["alert_id"]) in done:
                continue
            live = cfg in ("multi", "single") and llm.provider() != "none"
            if live:
                wait = gap_s - (time.monotonic() - last_llm)
                if wait > 0 and last_llm:
                    time.sleep(wait)
            env_old = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "LLM_FORCE_DOWN")}
            try:
                if cfg == "template":
                    os.environ["LLM_PROVIDER"] = "none"
                if cfg == "llm_down":
                    os.environ["LLM_FORCE_DOWN"] = "1"
                if cfg == "single":
                    res = single_agent(tools, a["alert_id"])
                else:
                    res = graph.run_alert(tools, a["alert_id"], force=True)
            finally:
                for k, v in env_old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            if live:
                last_llm = time.monotonic()
            if res.get("facts") and a["alert_id"] not in ref:
                ref[a["alert_id"]] = {"facts": res["facts"], "confidence": res.get("confidence"),
                                      "ref_alert": tools.get_alert(a["alert_id"])}
            rf = ref.get(a["alert_id"]) or {}
            row = score(cfg, a, res, rf.get("facts"), rf.get("confidence"), rf.get("ref_alert"))
            row.update({"facts": res.get("facts"), "ref_alert": tools.get_alert(a["alert_id"]) if res.get("facts") else None,
                        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            with open(RUNS, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
            print(f"{cfg:8s} {a['alert_id']:45s} {row['outcome']:8s} att {row['attempts']} checks "
                  f"{'PASS' if row['checks_pass'] else 'fail:' + row['checks_failed']} faithful {row['numbers_ok']}/"
                  f"{row['numbers']} tok {row['tokens']} {row['elapsed_s']} s", flush=True)


# ---------------------------------------------------------------------------------------------- report

def _rows() -> list[dict]:
    rows = [json.loads(x) for x in RUNS.read_text(encoding="utf-8").splitlines() if x.strip()]
    last: dict[tuple, dict] = {}
    for r in rows:
        last[(r["config"], r["alert_id"])] = r
    return list(last.values())


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summarise(rows: list[dict], cfg: str) -> dict:
    rs = [r for r in rows if r["config"] == cfg]
    texts = [r for r in rs if r["text"]]
    nums = sum(r["numbers"] for r in texts)
    return {"config": cfg, "alerts": len(rs), "texts": len(texts),
            "llm_texts": sum(1 for r in rs if r["llm_text"]),
            "saved": sum(1 for r in rs if r["outcome"] in ("saved", "written")),
            "v1_kept": sum(1 for r in rs if r["outcome"] == "v1_kept"),
            "checks_pass": sum(1 for r in texts if r["checks_pass"]),
            "attempt1": sum(1 for r in rs if r["outcome"] in ("saved", "written") and r["attempts"] == 1),
            "faithful_texts": sum(1 for r in texts if r["faithful"]),
            "numbers_ok": sum(r["numbers_ok"] for r in texts), "numbers": nums,
            "tokens_median": _med([r["tokens"] for r in rs]), "tokens_total": sum(r["tokens"] or 0 for r in rs),
            "time_median_s": _med([r["elapsed_s"] for r in rs]),
            "time_max_s": max((r["elapsed_s"] or 0 for r in rs), default=None),
            "attempts_mean": round(statistics.mean([r["attempts"] or 0 for r in rs]), 2) if rs else None,
            "disagreements": sum(1 for r in rs if r["disagreement"]),
            "uncertain_texts": sum(1 for r in rs if r["uncertain_in_text"]),
            "judge_pass": sum(1 for r in rs if (r.get("judge") or "").upper().startswith("PASS")),
            "judge_fail": sum(1 for r in rs if (r.get("judge") or "").upper().startswith("FAIL")),
            "failed_checks": sorted({c for r in texts for c in (r["checks_failed"] or "").split(";") if c})}


def report() -> dict:
    from agentic_ai.eval import corrupt
    rows = _rows()
    cfgs = [c for c in ("multi", "single", "template", "llm_down") if any(r["config"] == c for r in rows)]
    sums = {c: summarise(rows, c) for c in cfgs}
    fields = ["config", "event", "alert_id", "category", "kind", "probability", "model", "outcome", "attempts", "tokens",
              "elapsed_s", "llm_text", "provider", "confidence", "disagreement", "uncertain_in_text", "checks_pass",
              "checks_failed", "numbers_ok", "numbers", "faithful", "judge", "llm_down", "chars", "update_n", "via_mcp",
              "error"]
    with open(HERE / "agent_eval.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["config"], r["alert_id"])))
    # LLM-written multi texts -> catch-rate bases
    bases = [{"name": f"llm:{r['alert_id']}", "text": r["text"], "alert": r["ref_alert"], "facts": r["facts"],
              "confidence": r["confidence"] or "normal", "source": "llm (multi-agent v2)"}
             for r in rows if r["config"] == "multi" and r["llm_text"] and r["text"] and r.get("facts") and r.get("ref_alert")]
    (HERE / "llm_texts.json").write_text(json.dumps(bases, default=str, indent=1), encoding="utf-8")
    cr = corrupt.catch_rate(bases) if bases else None
    # evaluation_runs rows (ml.import_runs format)
    from ml.harness import COLUMNS
    er = []
    for c, s in sums.items():
        model = {"multi": "multi-agent graph", "single": "single agent (1 LLM call)", "template": "template (no LLM)",
                 "llm_down": "multi-agent graph, LLM forced down"}[c]
        prov = next((r["provider"] for r in rows if r["config"] == c and r.get("provider")), None)
        note = (f"agents eval {c}: alerts={s['alerts']} saved={s['saved']} v1_kept={s['v1_kept']} "
                f"checks_pass={s['checks_pass']}/{s['texts']} attempt1={s['attempt1']} faithful_texts={s['faithful_texts']}/"
                f"{s['texts']} numbers_ok={s['numbers_ok']}/{s['numbers']} tokens_median={s['tokens_median']} "
                f"time_median_s={s['time_median_s']} disagreements={s['disagreements']} judge_pass={s['judge_pass']} "
                f"judge_fail={s['judge_fail']} provider={prov}")
        if c == "multi" and cr:
            note += f" seeded_errors_caught={cr['caught']}/{cr['total']}"
        er.append({k: None for k in COLUMNS} | {"run_label": f"agent_eval:{c}", "data_source": "replay",
                                                "model": model, "horizon_h": 3.0, "notes": note})
    with open(HERE / "agent_eval_runs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(er)
    _write_md(rows, sums, cr)
    return {"sums": sums, "catch": cr}


def _pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f} %)" if b else "-"


def _write_md(rows, sums, cr) -> None:
    events = sorted({r["event"] for r in rows})
    m = sums.get("multi")
    lines = ["# Agent evaluation on replayed alerts (A4.2)", "",
             f"Data: {len(events)} floods from the measured USGS record ({', '.join(events)}), replayed through core's "
             "windowing and forecaster (s3_v0 at 3 h, MRMS rain from ml's cache), mode SIMULATION; every non-retracted "
             "alert. All numbers below are measured in this run (agentic_ai/eval/runs.jsonl); replay only, no live "
             "alerts.", "",
             "| metric | " + " | ".join(sums) + " |", "|---|" + "---|" * len(sums)]

    def row(label, f):
        lines.append(f"| {label} | " + " | ".join(f(s) for s in sums.values()) + " |")
    row("alerts", lambda s: str(s["alerts"]))
    row("draft saved (v2) / written", lambda s: _pct(s["saved"], s["alerts"]))
    row("template v1 kept", lambda s: str(s["v1_kept"]))
    row("text written by the LLM", lambda s: str(s["llm_texts"]))
    row("final text passes all deterministic checks", lambda s: _pct(s["checks_pass"], s["texts"]))
    row("saved on attempt 1", lambda s: _pct(s["attempt1"], s["alerts"]))
    row("mean attempts", lambda s: str(s["attempts_mean"]))
    row("faithfulness: texts with every number in the evidence", lambda s: _pct(s["faithful_texts"], s["texts"]))
    row("faithfulness: numbers found in the evidence", lambda s: _pct(s["numbers_ok"], s["numbers"]))
    row("LLM judge PASS / FAIL (final verdicts)", lambda s: f"{s['judge_pass']} / {s['judge_fail']}")
    row("disagreements (evidence vs leading context)", lambda s: str(s["disagreements"]))
    row("UNCERTAIN in the text", lambda s: str(s["uncertain_texts"]))
    row("tokens per alert (median)", lambda s: str(s["tokens_median"]))
    row("tokens total", lambda s: str(s["tokens_total"]))
    row("v1 -> v2 time per alert, median s (max)", lambda s: f"{s['time_median_s']} ({s['time_max_s']})")
    row("checks failed on final texts", lambda s: ", ".join(s["failed_checks"]) or "none")
    if cr:
        lines += ["", f"Reviewer on seeded errors in the LLM-written multi-agent texts (deterministic checks, "
                      f"{len({r['case'] for r in cr['rows']})} texts as bases): caught **{cr['caught']} of {cr['total']}** "
                      f"({100 * cr['caught'] / max(cr['total'], 1):.1f} %); base texts flagged although saved: "
                      f"{len(cr['base_false_alarms'])}.", ""]
        lines += ["| error kind | caught / seeded |", "|---|---|"]
        lines += [f"| {k} | {c} / {t} |" for k, (c, t) in cr["by_kind"].items()]
        missed = [r for r in cr["rows"] if not r["caught"]]
        if missed:
            lines += ["", "Missed: " + "; ".join(f"{r['case']} {r['kind']}" for r in missed)]
    lines += ["", "Per alert (multi):", "", "| alert | outcome | attempts | checks | faithful | judge | tokens | s |",
              "|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["config"] == "multi"), key=lambda r: r["alert_id"]):
        lines.append(f"| {r['alert_id']} | {r['outcome']} | {r['attempts']} | {'pass' if r['checks_pass'] else r['checks_failed'] or '-'} "
                     f"| {r['numbers_ok']}/{r['numbers']} | {r['judge'] or '-'} | {r['tokens']} | {r['elapsed_s']} |")
    (HERE / "agent_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "multi" in sums and "single" in sums:
        a, b = sums["multi"], sums["single"]
        ab = ["# Ablation: single agent vs multi-agent graph (A5.1)", "",
              "Same replayed alerts, same LLM, same deterministic checks. Single agent = one LLM call with the raw tool "
              "outputs and the same rules in prose; it has no evidence/context workers, no precomputed facts, no "
              "reviewer and no revision. Multi-agent = the LangGraph graph (writer + deterministic reviewer + LLM "
              "judge, up to 2 revisions). Measured in this run; replay only.", "",
              "| metric | multi-agent | single agent |", "|---|---|---|",
              f"| alerts | {a['alerts']} | {b['alerts']} |",
              f"| text passes all deterministic checks | {_pct(a['checks_pass'], a['texts'])} | {_pct(b['checks_pass'], b['texts'])} |",
              f"| passes on the first draft | {_pct(a['attempt1'], a['alerts'])} | {_pct(b['checks_pass'], b['alerts'])} |",
              f"| texts with every number in the evidence | {_pct(a['faithful_texts'], a['texts'])} | {_pct(b['faithful_texts'], b['texts'])} |",
              f"| numbers found in the evidence | {_pct(a['numbers_ok'], a['numbers'])} | {_pct(b['numbers_ok'], b['numbers'])} |",
              f"| tokens per alert (median) | {a['tokens_median']} | {b['tokens_median']} |",
              f"| time per alert, median s (max) | {a['time_median_s']} ({a['time_max_s']}) | {b['time_median_s']} ({b['time_max_s']}) |",
              f"| UNCERTAIN where the signals disagree | {a['uncertain_texts']} of {a['disagreements']} | "
              f"{b['uncertain_texts']} of {a['disagreements']} |",
              f"| checks failed (any text) | {', '.join(a['failed_checks']) or 'none'} | {', '.join(b['failed_checks']) or 'none'} |",
              "", "Prompt correction (1 Nov): a first single-agent run told it to give the rise-rate lead time, "
              "while the checks require 'within N h (model forecast)' when the rise rate is slower than the "
              "forecast; it passed 1/14 (lead_time failed 12). That run is kept in runs_single_v1.jsonl and not "
              "used here; the table above is the re-run with the same lead-time rule as the multi-agent writer.",
              "", "A single-agent text that fails a check would go to the officer only as an unchecked draft; in the "
              "multi-agent graph a failing draft is revised or template v1 stays.", ""]
        single_fail = {}
        for r in rows:
            if r["config"] == "single":
                for c in (r["checks_failed"] or "").split(";"):
                    if c:
                        single_fail[c] = single_fail.get(c, 0) + 1
        rounding = invented = 0
        ref = {r["alert_id"]: r for r in rows if r["config"] == "multi" and r.get("facts")}
        for r in rows:
            if r["config"] == "single" and r["text"] and r["alert_id"] in ref:
                plain, _ = checks.allowed_numbers(ref[r["alert_id"]]["facts"])
                for b in checks.check_numbers(r["text"], ref[r["alert_id"]]["facts"]):
                    v = float(b.rstrip("%"))
                    if not b.endswith("%") and any(abs(v - p) <= 0.11 for p in plain):
                        rounding += 1
                    else:
                        invented += 1
        ab += [f"Single-agent numbers not in the evidence: {rounding + invented}; of these {rounding} are within "
               f"0.1 of an evidence value (raw data rounded differently from the report precision) and {invented} "
               "are further off (computed or invented values).", ""]
        if single_fail:
            ab += ["Single-agent check failures by check: " + ", ".join(f"{k} {v}" for k, v in
                                                                      sorted(single_fail.items(), key=lambda x: -x[1])), ""]
        (HERE / "ablation.md").write_text("\n".join(ab) + "\n", encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn")
    ap.add_argument("--configs", default="multi,single,template,llm_down")
    ap.add_argument("--gap", type=float, default=65.0, help="seconds between live LLM runs (Groq free tier)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.report_only:
        run(a.dsn, a.configs.split(","), a.gap, a.limit)
    out = report()
    for c, s in out["sums"].items():
        print(c, {k: v for k, v in s.items() if k != "config"})
    if out["catch"]:
        print(f"seeded errors in LLM texts: caught {out['catch']['caught']}/{out['catch']['total']}")


if __name__ == "__main__":
    main()
