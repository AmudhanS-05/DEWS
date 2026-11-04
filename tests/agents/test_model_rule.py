"""Core 1 Nov: alerts raised by the model's probability (alert_rule alert_by_model, p_minor >= tau at 3 h). When the
model's own stage forecast stays below the alert's category (LLAT2 2023-10-26: stage forecast 5.4 ft, MINOR 10.0 ft)
the advisory states the classifier head's probability and the regression head's stage forecast and never presents the stage forecast as the reason.
Synthetic cases both ways."""
from agentic_ai import advisory, checks, graph
from tests.agents.fake_tools import FakeTools

LLAT = {"category": "minor", "mode": "SIMULATION", "probability": 0.86, "tau": 0.827, "alert_rule": "alert_by_model",
        "horizon_h": 3.0, "provisional_data": False, "official_levels": {"action": 10.0, "minor": 10.0, "moderate": 12.0,
        "major": 23.0}, "raised_at": "2023-10-26T05:00:00Z", "site_no": "08151500", "site_name": "Llano River at Llano",
        "forecast_stage": 5.4, "current_stage": 8.4, "model": "s3_v0"}
EV = {"stage_last": 8.4, "rise_rate_ft_per_h": 1.1, "forecast_stage": 5.4, "p_minor": 0.86, "model": "s3_v0"}
CX = {"rain": {"available": True, "rain_3h": 30.0, "rain_6h": 55.0}, "neighbours": [], "direction": "rising",
      "kind": "leading", "impacts": []}
AGREE = {**LLAT, "forecast_stage": 11.2}


def _ids(text, alert, conf, cx=CX, ev=EV):
    return {i for i, _ in checks.review_detailed(text, alert, advisory.build_facts(alert, ev, cx, 1), conf)}


def test_disagreement_is_detected_only_for_the_model_rule_below_the_category():
    assert advisory.forecasts_disagree(LLAT)
    assert not advisory.forecasts_disagree(AGREE)
    assert not advisory.forecasts_disagree({**LLAT, "alert_rule": "forecast_stage"})
    assert not advisory.forecasts_disagree({**LLAT, "alert_rule": None})


def test_template_says_both_forecasts_and_uncertain_and_passes():
    t = advisory.template_text(LLAT, EV, CX, "uncertain", 1, None)
    s = advisory.disagreement_sentence(LLAT)
    assert s == ("The model's classifier head puts the probability of crossing MINOR within 3 h at 86%. Its regression "
                 "head forecasts the stage at 5.4 ft, below the MINOR stage (10.0 ft). Re-check at the next window.")
    assert "uncertain" not in s.lower() and "disagree" not in s
    assert s in t and "probability of crossing MINOR flood stage (10.0 ft) within 3 h: 86% (model alert threshold 83%)" in t
    assert "forecast to reach" not in t
    assert _ids(t, LLAT, "uncertain") == set()
    facts = advisory.build_facts(LLAT, EV, CX, 1)
    assert facts["alert"]["alert_threshold_pct"] == 83 and facts["forecasts_disagree"] == s


def test_stage_forecast_presented_as_the_reason_is_rejected():
    t = advisory.template_text(LLAT, EV, CX, "uncertain", 1, None)
    bad = t.replace(advisory.disagreement_sentence(LLAT), "Forecast to reach MINOR flood stage within 3 h.")
    assert "model_rule" in _ids(bad, LLAT, "uncertain")
    quiet = t.replace("classifier head", "model")
    assert "model_rule" in _ids(quiet, LLAT, "uncertain")


def test_writer_is_given_the_sentence_and_the_rule():
    facts = advisory.build_facts(LLAT, EV, CX, 1)
    req = dict(advisory.requirements(facts, "uncertain"))
    assert advisory.disagreement_sentence(LLAT) in req["model_rule"] and "uncertain" not in req


def test_model_rule_when_the_forecasts_agree_is_normal_and_leads_with_the_probability():
    t = advisory.template_text(AGREE, {**EV, "forecast_stage": 11.2}, CX, "normal", 1, None)
    assert "probability of crossing MINOR flood stage (10.0 ft) within 3 h: 86%" in t
    assert "Stage forecast: 11.2 ft in 3 h, at or above the MINOR stage." in t and "UNCERTAIN" not in t
    assert _ids(t, AGREE, "normal", ev={**EV, "forecast_stage": 11.2}) == set()


def test_forecast_stage_rule_keeps_the_old_wording():
    a = {**AGREE, "alert_rule": "forecast_stage", "tau": None}
    t = advisory.template_text(a, {**EV, "forecast_stage": 11.2}, CX, "normal", 1, None)
    assert "forecast to reach MINOR flood stage (10.0 ft) within 3 h" in t


def test_graph_marks_it_uncertain_and_saves_the_disagreement_wording():
    t = FakeTools(rain=False, flat=True)            # stage forecast 1.3 ft, MINOR 5.5 ft
    aid = "08158700|minor|2019-05-04T03:00:00Z"
    t.alerts[aid]["details"] = {"alert_rule": "alert_by_model", "tau": 0.6}
    res = graph.run_alert(t, aid)
    assert res["outcome"] == "saved" and res["confidence"] == "uncertain", res.get("reasons")
    txt = t.advisories[-1]["text"]
    assert "regression head" in txt and "below the MINOR stage (5.5 ft)" in txt and "forecast to reach" not in txt
    d = [e for e in res["events"] if e["kind"] == "disagreement"]
    assert d and d[-1]["detail"]["kind"] == "model"


def test_probability_threshold_is_not_a_stage_threshold():
    t = advisory.template_text(LLAT, EV, CX, "uncertain", 1, None)
    assert "threshold" not in _ids(t + "\nThe model alert threshold is 83%.", LLAT, "uncertain")
    assert "threshold" in _ids(t + "\nThe warning threshold is 8.4 ft.", LLAT, "uncertain")


def test_lead_time_is_not_given_when_the_forecasts_disagree():
    """Live LLAT2 run 1 Nov: 'Lead time to MINOR: about 0.6 h' (rise rate 2.8 ft/h) next to a stage forecast that never
    reaches MINOR was judged misleading."""
    assert advisory.lead_time(EV, LLAT) == "not available (the regression head stays below the category stage)"
    t = advisory.template_text(LLAT, EV, CX, "uncertain", 1, None)
    assert "Lead time to MINOR: not available (the regression head stays below the category stage)." in t
    assert "lead_time" in _ids(t.replace("not available (the regression head stays below the category stage)", "about 1.5 h"), LLAT, "uncertain")
    assert advisory.lead_time(EV, {**AGREE}) == 1.5            # forecasts agree: the rise-rate estimate stays
