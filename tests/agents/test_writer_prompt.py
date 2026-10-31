"""The writer is told every deterministic reviewer requirement up front, so a first draft can normally pass."""
from agentic_ai import advisory, checks
from tests.agents.test_checks import ALERT, CX, EV

LIVE = {**ALERT, "mode": "LIVE", "provisional_data": False}
HIGHER = {**ALERT, "forecast_stage": 42.2609}           # the laptop case: MINOR alert, forecast above MAJOR
NO_RAIN = {**CX, "rain": {"available": False}}


def _prompt(alert, confidence="normal"):
    facts = advisory.build_facts(alert, EV, CX, 1)
    return facts, advisory.llm_user_prompt(facts, confidence, None, [])


def test_every_check_has_a_requirement():
    ids = {i for i, _ in advisory.requirements(advisory.build_facts(ALERT, EV, CX, 1), "uncertain")}
    ids |= {i for i, _ in advisory.requirements(advisory.build_facts(LIVE, EV, CX, 1), "normal")}
    ids |= {i for i, _ in advisory.requirements(advisory.build_facts(HIGHER, EV, NO_RAIN, 1), "normal")}
    ids |= {i for i, _ in advisory.requirements(advisory.build_facts(HIGHER, EV, {**NO_RAIN, "kind": "supporting"}, 1),
                                                "normal")}
    assert ids == set(checks.CHECKS)


def test_each_failed_check_was_stated_in_the_prompt():
    """A draft breaking every rule: each check id the reviewer reports has its instruction in the writer prompt."""
    bad = "**Onion Creek** will flood at 9.9 feet (24.72 ft), forecast to reach MINOR; lead time null, rain ?. " + "x" * 950
    for alert, conf, cx in ((ALERT, "uncertain", CX), ({**LIVE, "provisional_data": False}, "normal", CX),
                            (HIGHER, "normal", NO_RAIN)):
        facts = advisory.build_facts(alert, EV, cx, 1)
        prompt = advisory.llm_user_prompt(facts, conf, None, [])
        draft = bad + (" SIMULATION" if alert["mode"] == "LIVE" else "")
        failed = {i for i, _ in checks.review_detailed(draft, alert, facts, conf)}
        assert failed, "the bad draft should fail"
        reqs = dict(advisory.requirements(facts, conf))
        for i in failed:
            assert reqs[i] in prompt, i


def test_requirements_carry_the_exact_values():
    _, p = _prompt(ALERT)
    for s in ("MINOR", "5.5 ft", "79%", "within 6 h", "SIMULATION", "provisional", "about 4.1 h"):
        assert s in p, s
    _, live = _prompt(LIVE)
    assert "do not use the word SIMULATION" in live and "provisional" not in live.split("REQUIREMENTS")[1]


def test_a_draft_that_follows_the_requirements_passes():
    facts, _ = _prompt(ALERT)
    d = ("SIMULATION: replayed historical data, not a real event. Onion Creek at Driftwood is forecast to reach MINOR "
         "flood stage (5.5 ft) within 6 h, probability 79%. Gauge now 2.6 ft, rising 0.7 ft/h; forecast 6.8 ft. "
         "Estimated lead time about 4.1 h. Data are provisional.")
    assert checks.review(d, ALERT, facts) == []
