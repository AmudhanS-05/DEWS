"""A4.2/A5.1 evaluation harness, offline: a reduced copy of the measured replay set on a throwaway PostgreSQL
schema, LLM_PROVIDER=none (template) / forced down / a scripted single agent. Writes only into tmp_path."""
import json

import pytest

from agentic_ai import llm
from agentic_ai.eval import build_replays, evaluate


def test_faithfulness_counts_numbers_against_the_evidence():
    from agentic_ai import advisory
    from tests.agents.test_checks import ALERT, CX, EV
    facts = advisory.build_facts(ALERT, EV, CX, 1)
    assert evaluate.faithfulness("Gauge 2.6 ft, forecast 6.8 ft, 79%.", facts) == (3, 3)
    assert evaluate.faithfulness("Gauge 2.6 ft, forecast 9.9 ft.", facts) == (1, 2)
    assert evaluate.faithfulness(None, facts) == (0, 0)


def test_replay_builder_runs_core_functions_on_synthetic_readings():
    pytest.importorskip("confluent_kafka")
    from streams import forecaster
    g = forecaster.load_gauges()["08158700"]
    t0 = 1556928000.0                                    # 2019-05-04T00:00Z
    rd = [(t0 + 900 * i, 1.0 + 0.4 * max(0, i - 8), False) for i in range(48)]
    out = build_replays.replay("08158700", rd, g, t0, t0 + 12 * 3600, {})
    assert len(out["windows"]) == len(out["risk"]) == 12 and {w["mode"] for w in out["windows"]} == {"SIMULATION"}
    assert out["windows"][-1]["stage_last"] == rd[-1][1] and out["risk"][-1]["horizon_h"] == 3
    assert all(w["rain_1h"] is None for w in out["windows"])


@pytest.fixture()
def small_replays(tmp_path, monkeypatch):
    data = json.loads(evaluate.REPLAYS.read_text(encoding="utf-8"))
    ev = next(e for e in data["events"] if e["lid"] == "LLAT2" and e["alerts"])   # alert_by_model, forecasts disagree
    p = tmp_path / "replay_events.json"
    p.write_text(json.dumps({**data, "events": [ev]}), encoding="utf-8")
    monkeypatch.setattr(evaluate, "REPLAYS", p)
    monkeypatch.setattr(evaluate, "RUNS", tmp_path / "runs.jsonl")
    monkeypatch.setattr(evaluate, "HERE", tmp_path)
    return tmp_path


def _dsn_of(conn):
    from agentic_ai.mcp_client import _conn_env
    return _conn_env(conn)


def test_eval_template_and_llm_down_on_measured_replay(pg_empty, small_replays, monkeypatch):
    pytest.importorskip("confluent_kafka")
    from agentic_ai.tools import Tools
    monkeypatch.setattr("agentic_ai.db.connect", lambda dsn=None: pg_empty)
    alerts = evaluate.load_replays(pg_empty)
    assert alerts and all(a["mode"] == "SIMULATION" for a in alerts)
    assert alerts[0]["alert_rule"] == "alert_by_model" and alerts[0]["tau"]
    assert Tools(pg_empty).get_alert(alerts[0]["alert_id"])["alert_rule"] == "alert_by_model"
    assert Tools(pg_empty).get_rain(alerts[0]["site_no"], 6, alerts[0]["raised_at"], "SIMULATION")["available"] is True
    n = len(alerts)

    replies = iter(["SIMULATION: replay of a historical USGS gauge record. Llano River at Llano: forecast to reach "
                    "MINOR (10.0 ft) within 3 h. It will flood."] + [None] * 5)

    def fake(system, user, **k):                                     # the single agent's one call
        t = next(replies, None)
        return llm.LlmReply(text=t, tokens=100 if t else 0, provider="groq", error=None if t else "down")
    monkeypatch.setattr(evaluate, "SINGLE_COMPLETE", fake)
    evaluate.run(None, ["template", "llm_down", "single"], gap_s=0)
    out = evaluate.report()
    t, d, s = out["sums"]["template"], out["sums"]["llm_down"], out["sums"]["single"]
    assert t["saved"] == n and t["checks_pass"] == n and t["numbers_ok"] == t["numbers"] > 0
    assert t["model_rule"] == n and t["forecasts_disagree"] == 1
    assert d["v1_kept"] == n and d["saved"] == 0
    assert s["texts"] == 1 and s["checks_pass"] == 0 and "certainty" in s["failed_checks"]
    rows = (small_replays / "agent_eval_runs.csv").read_text(encoding="utf-8")
    assert "agent_eval:template" in rows and "replay" in rows
    assert (small_replays / "agent_eval.md").exists() and (small_replays / "ablation.md").exists() is False
