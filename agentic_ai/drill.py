"""Fault drill "LLM down" (C4.3) for core's tools/fault_drill.py:

    python -m agentic_ai.drill [--alert-id ID] [--dsn ...]      # last line: PASS: ... | FAIL: ... ; exit 0 / 1
    from agentic_ai.drill import llm_down; res = llm_down(tools_or_None, alert_id)   # {"ok", "recovery_time_s", "notes", ...}

The LLM is forced down (`LLM_FORCE_DOWN=1`, as a provider outage or missing key would be: every call fails at once)
and the agents run on one alert. Pass conditions:
  1. the run ends on its own, within LIMIT_S (nothing hangs or blocks);
  2. outcome v1_kept, reason "LLM unavailable": no agents draft is written;
  3. the alert's template v1 (if the sink wrote one) is unchanged and still approvable (status draft), and the alert
     row's status is unchanged;
  4. agent_events show the failure (writer revision llm_failed, coordinator end llm_down) for the console.
The same check runs with the real "no key" path when `--no-key` is given (LLM_PROVIDER=groq, GROQ_API_KEY unset).
Default alert: the newest SIMULATION alert in `alerts`. The drill writes only agent_events / agent_runs rows.
`recovery_time_s` = the run's elapsed time (how long until the console has its final state: v1 only).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

LIMIT_S = 30.0


def _adv(tools, alert_id: str) -> list[dict]:
    return tools._q("SELECT advisory_id, version, source, status, text FROM advisories WHERE alert_id = %s "
                    "ORDER BY version", (alert_id,))


def _status(tools, alert_id: str):
    r = tools._q("SELECT status FROM alerts WHERE alert_id = %s", (alert_id,))
    return r[0]["status"] if r else None


def pick_alert(tools) -> str | None:
    r = tools._q("SELECT alert_id FROM alerts WHERE mode = 'SIMULATION' AND status <> 'retracted' "
                 "ORDER BY raised_at DESC LIMIT 1")
    return r[0]["alert_id"] if r else None


def llm_down(tools=None, alert_id: str | None = None, no_key: bool = False) -> dict:
    """Run the drill; never raises. Returns core's drill dict: ok, recovery_time_s, notes, data_source, checks."""
    from agentic_ai import graph, llm
    from agentic_ai.tools import Tools
    try:
        tools = tools or Tools()
        alert_id = alert_id or pick_alert(tools)
        if not alert_id:
            return {"ok": False, "recovery_time_s": None, "notes": "no alert in the alerts table to run on",
                    "data_source": "replay", "checks": []}
        before, status0 = _adv(tools, alert_id), _status(tools, alert_id)
        saved_env = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "GROQ_API_KEY", "LLM_FALLBACK")}
        t0 = time.monotonic()
        try:
            if no_key:
                os.environ.update(LLM_PROVIDER="groq", LLM_FALLBACK="none")
                os.environ.pop("GROQ_API_KEY", None)
                res = graph.run_alert(tools, alert_id, force=True)
            else:
                with llm.forced_down_ctx():
                    res = graph.run_alert(tools, alert_id, force=True)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        elapsed = round(time.monotonic() - t0, 2)
        after = _adv(tools, alert_id)
        v1b = [a for a in before if a["version"] == 1]
        v1a = [a for a in after if a["version"] == 1]
        ev = res.get("events") or []
        checks = [
            (f"run ended within {LIMIT_S:g} s ({elapsed} s)", elapsed <= LIMIT_S),
            ("outcome v1_kept (LLM unavailable)", res.get("outcome") == "v1_kept" and bool(res.get("llm_down"))),
            ("no agents draft written", len(after) == len(before)),
            ("template v1 unchanged and approvable", v1a == v1b and all(a["status"] == "draft" for a in v1a)),
            ("alert status unchanged", _status(tools, alert_id) == status0),
            ("events show the failure", any(e["agent"] == "writer" and "llm_failed" in e["detail"] for e in ev)
             and any(e["agent"] == "coordinator" and e["detail"].get("llm_down") for e in ev)),
        ]
        failed = [n for n, ok in checks if not ok]
        notes = (f"agents LLM down ({'no key' if no_key else 'LLM_FORCE_DOWN'}) on {alert_id}: outcome "
                 f"{res.get('outcome')}, {len(ev)} events, v1 {'present' if v1a else 'not in advisories'}, "
                 f"{elapsed} s" + ("; FAILED: " + ", ".join(failed) if failed else ""))
        return {"ok": not failed, "recovery_time_s": elapsed, "notes": notes, "data_source": "replay",
                "checks": [{"check": n, "ok": ok} for n, ok in checks], "run": {k: res.get(k) for k in
                ("run_id", "outcome", "reasons", "llm_down", "elapsed_s")}}
    except Exception as e:  # noqa: BLE001 - a drill reports, it never crashes the caller
        return {"ok": False, "recovery_time_s": None, "notes": f"drill error: {type(e).__name__}: {e}",
                "data_source": "replay", "checks": []}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--alert-id")
    ap.add_argument("--dsn")
    ap.add_argument("--no-key", action="store_true", help="real path: LLM_PROVIDER=groq without GROQ_API_KEY")
    a = ap.parse_args(argv)
    from agentic_ai.db import connect, ensure_schema
    from agentic_ai.tools import Tools
    conn = connect(a.dsn)
    ensure_schema(conn)
    res = llm_down(Tools(conn), a.alert_id, a.no_key)
    for c in res["checks"]:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']}")
    rt = res["recovery_time_s"]
    print(("PASS" if res["ok"] else "FAIL") + f": recovery_time_s={-1.0 if rt is None else rt:.1f}; {res['notes']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
