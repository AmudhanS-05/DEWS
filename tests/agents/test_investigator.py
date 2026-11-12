"""The investigator: the LLM chooses tools from a fixed list; code runs them for the alert's own gauge and time."""
import json

from agentic_ai import investigator as inv
from agentic_ai.eval import investigator_eval as ev
from agentic_ai.llm import LlmReply


def scripted(*replies):
    it = iter(replies)
    return lambda system, user: LlmReply(text=next(it), tokens=10, provider="test")


def test_it_chooses_tools_and_its_findings_must_quote_fetched_numbers():
    case = ev.CASES[0]
    llm = scripted('{"tool": "get_rain", "args": {"hours": 99}, "why": "rain vs flat gauge"}',
                   'Sure: {"tool": "get_neighbours", "args": {}, "why": "upstream?"}',
                   json.dumps({"done": True, "findings": ["3 h rain 41.5 mm", "upstream rose 2.4 ft in 3 h", "peak will be 30 ft"]}))
    calls = []
    res = inv.investigate(llm, case["alert"], case["ev"], case["cx"], case["conf"], case["dis"],
                          lambda name, *a, **k: calls.append((name, a, k)) or ev.CANNED[name])
    assert [c[0] for c in calls] == ["get_rain", "get_neighbours"]
    assert calls[0][1] == ("08156800", 24) and calls[0][2]["until"] == "2023-10-25T06:00:00Z"   # args clamped, site and time from the alert
    assert res["findings"] == ["3 h rain 41.5 mm", "upstream rose 2.4 ft in 3 h"] and res["dropped_findings"] == ["peak will be 30 ft"]


def test_unknown_tools_and_repeats_are_rejected_and_the_budget_holds():
    case = ev.CASES[7]
    bad = ['{"tool": "drop_table"}', '{"tool": "get_rain", "args": {}}', '{"tool": "get_rain", "args": {}}'] + \
          [f'{{"tool": "get_recent_windows", "args": {{"hours": {h}}}}}' for h in (3, 6, 9, 12, 15)]
    res = inv.investigate(scripted(*bad), case["alert"], case["ev"], case["cx"], "normal", None, lambda n, *a, **k: ev.CANNED[n])
    assert [s.get("rejected") for s in res["steps"][:3]] == ["not an allowed tool", None, "repeat"]
    assert len([s for s in res["steps"] if not s.get("rejected")]) <= inv.MAX_CALLS and res["stopped"] == "budget"


def test_eval_scores_choices():
    perfect = lambda case: scripted(*[json.dumps({"tool": t}) for t in sorted(case["need"])], '{"done": true, "findings": []}')
    rows = [ev.run_case(c, perfect(c)) for c in ev.CASES]
    s = ev.summary(rows)
    assert s["cases"] == 10 and s["mean_recall"] == 1.0 and s["mean_precision"] == 1.0 and s["valid"] == 10
    lazy = ev.run_case(ev.CASES[0], scripted('{"done": true, "findings": []}'))
    assert lazy["recall"] == 0.0 and lazy["chosen"] == "(none)"


def test_ask_uses_fixed_read_only_queries():
    from agentic_ai import ask

    class Cur:
        def __init__(self, log): self.log, self.description = log, [("site_no",), ("nwps_lid",), ("name",)]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): self.log.append((sql, params))
        def fetchall(self): return [("08156800", "AHOT2", "Shoal Creek at W 12th St")]

    class Conn:
        def __init__(self): self.log = []
        def cursor(self): return Cur(self.log)

    conn = Conn()
    llm = scripted('{"tool": "find_gauges", "args": {"query": "Shoal\'; DROP TABLE gauges;--"}}',
                   '{"done": true, "findings": ["Shoal Creek is gauge AHOT2 (08156800)."]}')
    res = ask.answer("which gauge is Shoal Creek?", llm, conn)
    sql, params = conn.log[0]
    assert sql == ask.QUERIES["find_gauges"] and "DROP" in params["q"]       # bound as a parameter, never in the SQL
    assert res["answer"] == ["Shoal Creek is gauge AHOT2 (08156800)."] and res["steps"][0]["rows"] == 1
