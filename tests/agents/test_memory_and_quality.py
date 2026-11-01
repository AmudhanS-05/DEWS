"""A3.3 episodic memory (episode history in the facts, "Update n" wording, officer edits as wording examples, numbers
only from the current evidence) and task 3 (s3_v0 p_minor, sensor-fault flag). LLM_PROVIDER=none unless a fake
llm.complete is patched in; synthetic data."""
from datetime import datetime, timedelta, timezone

import pytest

from agentic_ai import advisory, checks, graph, llm
from tests.agents.fake_tools import FakeTools
from tests.agents.test_checks import ALERT, CX, EV

MINOR = "08158700|minor|2019-05-04T03:00:00Z"
UPGRADE = "08158700|moderate|2019-05-04T03:00:00Z"


def test_repeat_alert_says_update_2_upgraded_from_minor_and_passes():
    t = FakeTools()
    graph.run_alert(t, MINOR)
    res = graph.run_alert(t, UPGRADE)
    txt = t.advisories[-1]["text"]
    assert res["outcome"] == "saved" and "Update 2:" in txt and "(upgraded from MINOR)" in txt
    first = t.advisories[0]["text"]
    assert "Update" not in first
    co = [e for e in res["events"] if e["agent"] == "coordinator" and e["kind"] == "end"][0]["detail"]
    assert co["update_n"] == 2 and co["earlier"] == ["MINOR"]


def test_episode_is_in_the_facts_as_words_only():
    hist = {"alerts": [{"alert_id": MINOR, "category": "minor", "probability": 0.55, "raised_at": "2019-05-04T04:00:00Z"},
                       {"alert_id": UPGRADE, "category": "moderate", "probability": 0.62, "raised_at": "2019-05-04T07:00:00Z"}],
            "advisories": [{"alert_id": MINOR, "source": "agents", "status": "approved", "edited_text": "x"}]}
    ep = advisory.episode_summary(hist, UPGRADE, "2019-05-04T07:00:00Z", 2)
    assert ep["earlier_alert_categories"] == ["MINOR"] and ep["upgraded_from"] == "MINOR"
    assert "edited by the duty officer" in ep["earlier_advisories"][0]
    assert "0.55" not in str(ep) and "55" not in str(ep)
    facts = advisory.build_facts(ALERT, EV, CX, 2, ep)
    assert facts["episode"]["update_n"] == 2
    assert "episode" not in advisory.build_facts(ALERT, EV, CX, 1, ep)        # first alert: no episode section


def test_guard_numbers_from_earlier_alerts_are_not_allowed():
    facts = advisory.build_facts(ALERT, EV, CX, 2, {"update_n": 2, "earlier_probability_pct": 55, "earlier_stage_ft": 4.4})
    assert checks.check_numbers("earlier 55% and 4.4 ft", facts) == ["55%", "4.4"]
    assert checks.check_numbers("now 79% and 6.8 ft", facts) == []


def test_officer_edits_reach_the_writer_as_masked_examples(monkeypatch):
    t = FakeTools()
    t.edits = [{"advisory_id": "x|2", "text": "Gauge 6.8 ft, rising 0.7 ft/h, probability 79%.",
                "edited_text": "Onion Creek at Driftwood: 6.8 ft and rising 0.7 ft an hour (79% chance). Avoid low crossings."}]
    seen = []

    def fake(system, user, **kw):
        seen.append(user)
        return llm.LlmReply(text=None, provider="none")
    monkeypatch.setattr(llm, "complete", fake)
    res = graph.run_alert(t, MINOR)
    ex = seen[0].split("WORDING EXAMPLES")[1].split("REVISION FEEDBACK")[0]
    assert "Avoid low crossings" in ex and "#" in ex
    assert not any(ch.isdigit() for ch in ex.split(":", 1)[1])          # no number survives the mask
    assert res["outcome"] == "saved"


def test_memory_tool_failure_does_not_stop_the_run():
    class Broken(FakeTools):
        def get_edit_examples(self, *a, **k):
            raise RuntimeError("table missing")
    assert graph.run_alert(Broken(), MINOR)["outcome"] == "saved"


# ------------------------------------------------------------------ task 3: s3_v0 p_minor, suspect-data flag

MAJOR = {"category": "major", "mode": "SIMULATION", "probability": None, "horizon_h": 3.0, "provisional_data": False,
         "official_levels": ALERT["official_levels"], "raised_at": "2023-10-26T10:00:00Z", "site_no": "08158700",
         "site_name": "Onion Creek near Driftwood", "forecast_stage": 14.2, "current_stage": 9.1, "model": "s3_v0"}
MAJOR_EV = {"stage_last": 9.1, "rise_rate_ft_per_h": 2.0, "forecast_stage": 14.2, "p_minor": 0.973, "model": "s3_v0"}


def test_p_minor_from_s3_v0_is_stated_when_the_alert_category_has_none():
    t = advisory.template_text(MAJOR, MAJOR_EV, CX, "normal", 1, None)
    assert "probability of reaching MAJOR: not available" in t and "Probability of reaching MINOR: 97%" in t
    assert "s3_v0" not in t and "trained S3 model v0" in t
    facts = advisory.build_facts(MAJOR, MAJOR_EV, CX, 1)
    assert checks.review(t, MAJOR, facts) == []
    req = dict(advisory.requirements(facts, "normal"))["probability"]
    assert "probability of reaching MINOR: 97%" in req
    assert checks.check_numbers("probability of reaching MINOR: 90%", facts) == ["90%"]


def test_minor_alert_probability_is_p_minor():
    a = {**ALERT, "model": "s3_v0", "probability": 0.79}
    t = advisory.template_text(a, {**EV, "p_minor": 0.79}, CX, "normal", 1, None)
    assert "79%" in t and "Probability of reaching" not in t      # no duplicate line


SUSPECT = {"suspect": True, "reasons": ["stuck"], "source": "test"}


def test_suspect_flag_is_said_in_the_template_and_required():
    t = advisory.template_text(ALERT, EV, CX, "normal", 1, None, None, SUSPECT)
    assert "sensor fault" in t and "questionable" in t and "stuck at one value" in t
    facts = advisory.build_facts(ALERT, EV, CX, 1, None, SUSPECT)
    assert checks.review(t, ALERT, facts) == []
    plain = advisory.template_text(ALERT, EV, CX, "normal", 1, None)
    assert "suspect_data" in {i for i, _ in checks.review_detailed(plain, ALERT, facts)}
    assert "data_quality" not in advisory.build_facts(ALERT, EV, CX, 1, None, {"suspect": False, "reasons": []})
    assert "data_quality" not in advisory.build_facts(ALERT, EV, CX, 1, None, {"suspect": None, "reasons": []})


def test_graph_carries_the_flag_from_the_tool_into_the_saved_text():
    t = FakeTools()
    t.quality = dict(SUSPECT)
    res = graph.run_alert(t, MINOR)
    assert res["outcome"] == "saved" and "sensor fault" in t.advisories[-1]["text"]
    ev = [e for e in res["events"] if e["agent"] == "evidence" and e["kind"] == "end"][0]["detail"]
    assert ev["suspect"] is True and ev["suspect_reasons"] == ["stuck"]


# ------------------------------------------------------------------ PostgreSQL

@pytest.mark.usefixtures("seeded")
def test_pg_edit_examples_same_mode_this_gauge_first(tools, pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO advisories (advisory_id, alert_id, version, source, text, edited_text, status, approved_at) "
                    "VALUES (%s,%s,9,'agents','old text 6.8 ft','officer text 6.8 ft','approved', now()) "
                    "ON CONFLICT (advisory_id) DO NOTHING", (MINOR + "|9", MINOR))
    ex = tools.get_edit_examples("08158700", "SIMULATION")
    assert ex and ex[0]["edited_text"] == "officer text 6.8 ft"
    assert tools.get_edit_examples("08158700", "LIVE") == []
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM advisories WHERE advisory_id = %s", (MINOR + "|9",))


@pytest.mark.usefixtures("seeded")
def test_pg_data_quality_not_checked_without_raw_readings_and_flags_a_gap(tools, pg_conn):
    pytest.importorskip("numpy")
    until = "2019-05-04T04:00:00Z"
    assert tools.get_data_quality("08158700", until, "SIMULATION")["suspect"] is None
    t0 = datetime(2019, 5, 2, 6, tzinfo=timezone.utc)
    with pg_conn.cursor() as cur:
        for i in range(4 * 46):          # every 15 min up to 03:45Z, with a 5 h hole 20:00-01:00Z
            ts = t0 + timedelta(minutes=15 * i)
            if ts >= datetime(2019, 5, 3, 20, tzinfo=timezone.utc) and ts < datetime(2019, 5, 4, 1, tzinfo=timezone.utc):
                continue
            cur.execute("INSERT INTO gauge_readings (site_no, parameter, series, reading_time, value, mode) "
                        "VALUES ('08158700','00065','x',%s,1.2,'SIMULATION') ON CONFLICT DO NOTHING", (ts,))
    try:
        q = tools.get_data_quality("08158700", until, "SIMULATION")
        assert q["suspect"] is True and "no_readings" in q["reasons"], q
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM gauge_readings WHERE site_no='08158700' AND series='x'")


def test_worst_case_template_still_fits_and_passes():
    """Update 2 + upgraded + s3_v0 p_minor + sensor fault + UNCERTAIN + an impact statement: the template drops
    optional parts (stage list, footer, archive note) and stays under 900 characters with every check passing."""
    from agentic_ai.eval.catch_rate import NB
    cx = {"rain": {"available": False}, "neighbours": NB, "direction": "rising", "kind": "supporting",
          "impacts": ["Water over FM 150 low water crossing; homes near the creek at risk (NWS)."]}
    dq = {"suspect": True, "reasons": ["stuck", "step"]}
    dis = {"evidence": "rising (rise 2.0 ft/h, forecast 14.2 ft)", "context": "not rising (upstream gauges, a leading signal)"}
    t = advisory.template_text(MAJOR, MAJOR_EV, cx, "uncertain", 2, dis, {"upgraded_from": "MODERATE"}, dq)
    facts = advisory.build_facts(MAJOR, MAJOR_EV, cx, 2, {"update_n": 2}, dq)
    assert len(t) <= 900 and checks.review(t, MAJOR, facts, "uncertain") == []
    assert "(upgraded from MODERATE)" in t and "MINOR: 97%" in t and "sensor fault" in t


def test_lead_time_follows_the_model_when_the_rise_rate_is_slower():
    """Replay eval 1 Nov, DRWT2 2019 with s3_v0: 3.0 ft rising 0.1 ft/h gave 'about 20.4 h' while the model forecast
    MINOR (5.5 ft) within 3 h; the LLM judge rejected it three times. Now: 'within 3 h (model forecast)'."""
    a = {**ALERT, "horizon_h": 3.0, "current_stage": 3.0, "forecast_stage": 5.6, "probability": 0.55, "model": "s3_v0"}
    ev = {**EV, "stage_last": 3.0, "rise_rate_ft_per_h": 0.12, "forecast_stage": 5.6, "p_minor": 0.55}
    assert advisory.lead_time_h(ev, a) == 20.8 and advisory.lead_time(ev, a) == "within 3 h (model forecast)"
    facts = advisory.build_facts(a, ev, CX, 1)
    t = advisory.template_text(a, ev, CX, "normal", 1, None)
    assert "Lead time to MINOR: within 3 h (model forecast)." in t and checks.review(t, a, facts) == []
    bad = t.replace("within 3 h (model forecast)", "about 20.8 h")
    ids = {i for i, _ in checks.review_detailed(bad, a, facts)}
    assert "lead_time" in ids and "numbers" in ids
    fast = {**ev, "rise_rate_ft_per_h": 1.0}
    assert advisory.lead_time(fast, a) == 2.5                         # rise-rate estimate inside the horizon: kept
