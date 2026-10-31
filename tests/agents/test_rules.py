"""A2.2 disagreement rule on synthetic cases (pure functions, no data)."""
from agentic_ai import graph, rules
from tests.agents.fake_tools import FakeTools

LEVEL = 5.5   # MINOR at Driftwood


def test_evidence_direction():
    assert rules.evidence_direction(0.7, 6.8, LEVEL) == "rising"
    assert rules.evidence_direction(0.0, 6.0, LEVEL) == "rising"          # forecast reaches the stage
    assert rules.evidence_direction(0.1, 3.0, LEVEL) == "not rising"
    assert rules.evidence_direction(None, None, LEVEL) == "not rising"


def test_context_direction():
    assert rules.context_direction(27.0, []) == ("rising", True, False)
    assert rules.context_direction(0.0, [False]) == ("not rising", False, False)
    assert rules.context_direction(None, [True]) == ("rising", False, True)    # upstream rising, no radar
    assert rules.context_direction(None, []) == ("unknown", False, False)
    assert rules.context_direction(None, [None]) == ("unknown", False, False)  # upstream gauge without a reading


def test_rain_but_no_rise_disagrees():
    ev = rules.evidence_direction(0.0, 1.3, LEVEL)
    cx, _, _ = rules.context_direction(27.0, [False])
    assert (ev, cx) == ("not rising", "rising") and rules.disagreement(ev, cx)


def test_rise_but_no_rain_disagrees():
    ev = rules.evidence_direction(0.7, 6.8, LEVEL)
    cx, _, _ = rules.context_direction(0.0, [False])
    assert (ev, cx) == ("rising", "not rising") and rules.disagreement(ev, cx)


def test_agreement_and_unknown_do_not_disagree():
    assert not rules.disagreement("rising", "rising")
    assert not rules.disagreement("not rising", "not rising")
    assert not rules.disagreement("rising", "unknown")


def _no_rain_flat_upstream(t):
    for w in t.windows:
        w["rain_1h"] = w["rain_3h"] = w["rain_6h"] = 0.0
        if w["site_no"] != "08158700":
            w["rise_rate_ft_per_h"] = 0.0
    return t


def test_graph_rise_no_rain_is_uncertain_with_both_signals():
    res = graph.run_alert(_no_rain_flat_upstream(FakeTools()), "08158700|minor|2019-05-04T03:00:00Z")
    dis = [e for e in res["events"] if e["kind"] == "disagreement"]
    assert res["confidence"] == "uncertain" and dis
    assert dis[0]["detail"]["evidence"].startswith("rising") and dis[0]["detail"]["context"].startswith("not rising")
    assert "UNCERTAIN" in res["draft"] and res["outcome"] == "saved"


def test_graph_rain_no_rise_is_uncertain():
    res = graph.run_alert(FakeTools(flat=True), "08158700|minor|2019-05-04T03:00:00Z")
    assert res["confidence"] == "uncertain" and "UNCERTAIN" in res["draft"]
