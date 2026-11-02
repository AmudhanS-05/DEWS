"""Round 3 laptop findings: a 42.26 ft forecast called MINOR (major is 13 ft), 'Lead time: null',
'42.260909090909095 ft', Markdown bold, and 'rain 3 h ?'. Each has a deterministic reviewer check and the template
satisfies all of them."""
from agentic_ai import advisory, checks, run
from tests.agents.test_checks import ALERT, CX, EV

LV = ALERT["official_levels"]
HIGHER = {**ALERT, "forecast_stage": 42.260909090909095}                 # MINOR alert, forecast above MAJOR
REAL = {"category": "major", "mode": "SIMULATION", "probability": None, "horizon_h": 3.0, "provisional_data": False,
        "official_levels": LV, "raised_at": "2026-10-31T08:00:00Z", "as_of": "2019-05-04T02:00:00Z",
        "site_no": "08158700", "site_name": "Onion Creek near Driftwood", "forecast_stage": 24.720000000000002,
        "current_stage": 8.16, "model": "rise_rate"}
REAL_EV = {"stage_last": 8.16, "rise_rate_ft_per_h": 5.5200000000000005, "forecast_stage": 24.720000000000002}
NO_RAIN = {"rain": {"available": False, "rain_3h": None}, "impacts": [], "direction": "unknown"}


def _review(draft, alert=ALERT, ev=EV, cx=CX):
    return checks.review_detailed(draft, alert, advisory.build_facts(alert, ev, cx, 1))


def _ids(draft, alert=ALERT, ev=EV, cx=CX):
    return {i for i, _ in _review(draft, alert, ev, cx)}


def test_category_of_uses_official_levels_only():
    from agentic_ai import rules
    assert rules.category_of(42.26, LV) == "major" and rules.category_of(6.8, LV) == "minor"
    assert rules.category_of(2.6, LV) is None and rules.category_of(None, LV) is None
    assert advisory.higher_forecast_category(HIGHER) == "major" and advisory.higher_forecast_category(ALERT) is None


def test_template_states_alert_category_and_higher_forecast_category():
    t = advisory.template_text({**HIGHER, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    assert "forecast to reach MAJOR flood stage (13.0 ft)" in t and "Alert category: MINOR (5.5 ft)" in t
    assert "42.3 ft" in t and "42.26" not in t
    assert _review(t, HIGHER) == []


def test_forecast_called_minor_when_it_crosses_major_is_rejected():
    d = advisory.template_text({**HIGHER, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    d = d.replace("forecast to reach MAJOR flood stage (13.0 ft)", "forecast to reach MINOR flood stage (5.5 ft)")
    d = d.replace("the forecast stage is above the official MAJOR stage", "the forecast stage is high")
    reasons = [r for i, r in _review(d, HIGHER) if i == "forecast_category"]
    assert len(reasons) == 2 and "crosses the official MAJOR stage (13.0 ft)" in reasons[0]
    assert "forecast reaches MAJOR; MINOR is the alert's category" in reasons[1]


def test_no_category_check_when_forecast_stays_in_the_alert_category():
    good = advisory.template_text({**ALERT, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    assert "forecast to reach MINOR" in good and "Alert category" not in good and _review(good) == []


def test_precision_stages_to_a_tenth():
    good = advisory.template_text({**ALERT, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    assert "precision" in _ids(good.replace("6.8 ft", "6.80 ft"))
    assert "precision" in _ids(good + " Peak 42.260909090909095 ft.", HIGHER)
    assert "precision" not in _ids(good)


def test_probability_whole_percent_or_not_available():
    t = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "probability of reaching MAJOR: not available (rise-rate baseline gives none)" in t and "%" not in t
    assert _review(t, REAL, REAL_EV, NO_RAIN) == []
    assert {"probability", "missing_value"} <= _ids(t.replace("not available (rise-rate baseline gives none)", "null"),
                                                   REAL, REAL_EV, NO_RAIN)
    good = advisory.template_text({**ALERT, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    assert "probability" in _ids(good.replace("79%", "79.3%"))


def test_lead_time_number_or_not_available_never_null():
    t = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "Lead time to MAJOR: about 0.9 h" in t
    for bad in ("Lead time: null", "Lead time: None", "Lead time: NaN"):
        d = t.replace("Lead time to MAJOR: about 0.9 h", bad)
        assert {"lead_time", "missing_value"} <= _ids(d, REAL, REAL_EV, NO_RAIN), bad
    flat = {"stage_last": 8.16, "rise_rate_ft_per_h": 0.0}
    t2 = advisory.template_text(REAL, flat, NO_RAIN, "normal", 1, None)
    # not rising, but the forecast crosses MAJOR within the horizon: the lead time follows the forecast (1 Nov)
    assert "Lead time to MAJOR: within 3 h (model forecast)." in t2 and _review(t2, REAL, flat, NO_RAIN) == []
    above = {**REAL, "current_stage": 14.0}                 # already above MAJOR: not available
    t3 = advisory.template_text(above, {**flat, "stage_last": 14.0}, NO_RAIN, "normal", 1, None)
    assert "Lead time to MAJOR: not available." in t3


def test_markdown_bold_is_rejected():
    good = advisory.template_text({**ALERT, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)
    assert "markdown" in _ids(good.replace("MINOR", "**MINOR**", 1))
    assert "markdown" in _ids("## Advisory\n" + good)


def test_missing_rain_says_rain_data_not_available():
    t = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "Radar rain: rain data not available (radar archive starts Oct 2020)." in t and "?" not in t
    assert {"rain", "missing_value"} <= _ids(t.replace("rain data not available", "?"), REAL, REAL_EV, NO_RAIN)
    facts = advisory.build_facts(REAL, REAL_EV, NO_RAIN, 1)
    assert facts["context"]["rain"] == "rain data not available" and facts["lead_time_h"] == 0.9
    assert facts["alert"]["forecast_stage_ft"] == 24.7 and facts["alert"]["probability_pct"] == "not available"


def test_cli_progress_lines_round_and_spell_out_missing_values():
    ev = run.progress_line({"agent": "evidence", "kind": "end", "t_s": 0.1, "detail": {
        "direction": "rising", "stage_last": 22.69, "rise_rate_ft_per_h": 6.52364, "forecast_stage": 42.260909090909095}})
    assert "stage 22.7 ft, rate 6.5 ft/h, forecast 42.3 ft" in ev
    cx = run.progress_line({"agent": "context", "kind": "end", "t_s": 0.1,
                            "detail": {"direction": "unknown", "rain_3h": None, "n_neighbours": 4}})
    assert "rain data not available" in cx and "?" not in cx
