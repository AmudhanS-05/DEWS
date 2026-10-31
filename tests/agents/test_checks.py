from agentic_ai import advisory, checks

ALERT = {"category": "minor", "mode": "SIMULATION", "probability": 0.79, "horizon_h": 6.0, "provisional_data": True,
         "official_levels": {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}, "raised_at": "2019-05-04T04:00:00Z",
         "site_no": "08158700", "forecast_stage": 6.8, "current_stage": 2.6}
EV = {"stage_last": 2.6, "rise_rate_ft_per_h": 0.7, "forecast_stage": 6.8}
CX = {"rain": {"available": True, "rain_3h": 27.0, "rain_6h": 71.0}, "impacts": []}
FACTS = advisory.build_facts(ALERT, EV, CX, 1)
GOOD = advisory.template_text({**ALERT, "site_name": "Onion Creek"}, EV, CX, "normal", 1, None)


def test_template_passes_all_checks():
    assert checks.review(GOOD, ALERT, FACTS) == []


def test_invented_number_is_caught():
    assert "numbers not in the evidence: 9.3" in checks.review(GOOD.replace("6.8 ft", "9.3 ft"), ALERT, FACTS)[0]


def test_wrong_percentage_is_caught():
    assert any("97%" in r for r in checks.review(GOOD.replace("79%", "97%"), ALERT, FACTS))


def test_certainty_language_is_caught():
    r = checks.review(GOOD + " The creek will flood.", ALERT, FACTS)
    assert any("will flood" in x for x in r)


def test_missing_category_simulation_provisional_probability():
    r = checks.review("Water is rising in 6 h.", ALERT, FACTS)
    joined = " | ".join(r)
    assert "category" in joined and "SIMULATION" in joined and "provisional" in joined and "probability" in joined


def test_live_alert_must_not_carry_simulation_label():
    live = {**ALERT, "mode": "LIVE"}
    assert any("SIMULATION label on live" in x for x in checks.review(GOOD, live, FACTS))


def test_too_long():
    assert any("too long" in x for x in checks.review(GOOD + " x" * 600, ALERT, FACTS))


def test_uncertain_needs_both_signals():
    assert any("UNCERTAIN" in x for x in checks.review(GOOD, ALERT, FACTS, "uncertain"))
