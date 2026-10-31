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


# Regression: a Groq (gpt-oss-120b) draft on the user's laptop was rejected on every attempt (outcome v1_kept).
# It wrote the probability as a decimal ("~0.79 probability") and used typographic Unicode.
LIVE_LIKE = ("SIMULATION – replayed historical data. Onion Creek at Driftwood: gauge now 2.6 ft, rising "
             "0.7 ft/h. Forecast 6.8 ft within 6.0 h; ~0.79 probability of reaching the MINOR flood "
             "stage (5.5 ft). Lead‑time ≈ 4.1 h. Data are provisional.")


def test_decimal_probability_and_unicode_draft_passes():
    assert checks.review(LIVE_LIKE, ALERT, FACTS) == []


def test_percentage_with_narrow_nbsp_passes():
    d = LIVE_LIKE.replace("~0.79 probability", "79 % probability")
    assert checks.review(d, ALERT, FACTS) == []


def test_normalize_maps_typographic_characters_to_ascii():
    s = "a–b‑c—d−e f g h​i­j “q” ’ ７９％"
    assert checks.normalize(s) == "a-b-c-d-e f g hij \"q\" ' 79%"
    assert checks.normalize(s).isascii()


def test_wrong_number_still_caught_through_unicode():
    d = LIVE_LIKE.replace("6.8 ft", "9.3 ft")
    assert any("9.3" in r for r in checks.review(d, ALERT, FACTS))


def test_wrong_decimal_probability_is_not_accepted():
    d = LIVE_LIKE.replace("~0.79 probability", "~0.97 probability")
    assert any(r.startswith("probability not stated") for r in checks.review(d, ALERT, FACTS))


def test_utc_time_digits_from_raised_at_are_allowed():
    assert checks.check_numbers("as of 2019‑05‑04 04:00 UTC", FACTS) == []


def test_lowercase_simulation_is_still_rejected_with_actionable_reason():
    r = checks.review(LIVE_LIKE.replace("SIMULATION", "Simulation"), ALERT, FACTS)
    assert r == ["SIMULATION label missing for replayed data (write the word SIMULATION in capitals)"]
