"""Round 4 (laptop runs on 08158700|major|2019-05-04T01:00:00Z, Groq gpt-oss-120b):
1. the LLM reviewer rejected "replayed historical data" because FACTS only said mode SIMULATION;
2. raw fields leaked into the text ("provisional data false", "provisional data not available", "as_of");
3. context read "unknown ... 4 neighbour gauge(s)" although downstream and nearby gauges rose in the same window;
4. rain: the MRMS radar archive starts 2020-10-14, so a 2019 flood never has radar rain.
User decision: upstream gauges and radar rain are LEADING signals; downstream and nearby gauges are SUPPORTING
context, never a cause or predictor of the rise at this gauge. All cases below are synthetic."""
import json

from agentic_ai import advisory, checks, graph, llm, rules
from agentic_ai.tools import anchor_window
from tests.agents.fake_tools import FakeTools
from tests.agents.test_format_checks import NO_RAIN, REAL, REAL_EV

ALERT = "08158700|minor|2019-05-04T03:00:00Z"


def _nb(relation, rising, change=None, name=None, hours=4):
    ch = change if change is not None else (2.0 if rising else 0.0)
    return {"site_no": f"x-{relation}", "name": name or f"{relation.title()}  Creek", "relation": relation,
            "distance_km": 10.0, "stage_last": 5.0, "change_ft_over_window": None if rising is None else ch,
            "hours_in_window": hours, "rising": rising}


# ------------------------------------------------------------------ rules.context_signals (pure)

def test_downstream_or_nearby_rising_is_supporting_not_unknown():
    c = rules.context_signals(None, [], [True, False])
    assert (c["direction"], c["kind"], c["basis"]) == ("rising", "supporting", "supporting")
    assert not c["upstream_signal"] and not c["rain_signal"] and c["supporting_signal"]


def test_upstream_or_rain_is_leading_and_wins_over_supporting():
    assert rules.context_signals(None, [True], [True])["kind"] == "leading"
    assert rules.context_signals(27.0, [], [False])["kind"] == "leading"
    c = rules.context_signals(0.0, [False], [True])          # leading data known but quiet, a neighbour rising
    assert (c["direction"], c["kind"], c["basis"]) == ("rising", "supporting", "leading")


def test_no_reading_at_all_is_still_unknown():
    for args in ((None, [], []), (None, [None], [None, None])):
        c = rules.context_signals(*args)
        assert (c["direction"], c["kind"], c["basis"]) == ("unknown", "none", "none")


def test_quiet_supporting_gauges_never_make_the_advisory_uncertain():
    c = rules.context_signals(None, [], [False, False])
    assert c["direction"] == "not rising" and c["basis"] == "supporting"
    assert not rules.disagreement("rising", c["direction"], c["basis"])        # a flash flood can stay local
    s = rules.context_signals(None, [], [True])
    assert not rules.disagreement("not rising", s["direction"], s["basis"])    # supporting rise alone: no conflict
    lead = rules.context_signals(27.0, [], [])
    assert rules.disagreement("not rising", lead["direction"], lead["basis"])  # leading conflict still counts


def test_s1_context_direction_unchanged_for_leading_signals():
    assert rules.context_direction(27.0, []) == ("rising", True, False)
    assert rules.context_direction(None, []) == ("unknown", False, False)


def test_summary_names_the_kind_and_never_claims_cause():
    nb = [_nb("downstream", True, 6.1, "Onion Creek at US 183"), _nb("nearby", True, 7.4, "Slaughter  Creek"),
          _nb("nearby", False)]
    sig = rules.context_signals(None, [], [n["rising"] for n in nb])
    s = advisory.context_summary(sig, {"available": False}, nb)
    assert s.startswith("SUPPORTING signal only") and "downstream Onion Creek at US 183 +6.1 ft in 3 h" in s
    assert "Slaughter Creek" in s and "rain data not available" in s and "no upstream gauge" in s
    assert "does not cause or predict" in s and not checks._CAUSAL.search(s.replace("does not cause or predict", ""))
    up = [_nb("upstream", True, 1.2, "Upper Creek")]
    lead = advisory.context_summary(rules.context_signals(None, [True], []), {"available": False}, up)
    assert lead.startswith("LEADING signal") and "upstream rising: upstream Upper Creek +1.2 ft in 3 h" in lead


# ------------------------------------------------------------------ graph with neighbour readings

class NeighbourTools(FakeTools):
    def __init__(self, nb, **kw):
        super().__init__(**kw)
        self._nb = nb

    def get_neighbours(self, site, until=None, mode=None, n=3, max_km=60.0):
        return [dict(x) for x in self._nb]


def test_graph_states_supporting_context_and_stays_normal():
    t = NeighbourTools([_nb("downstream", True, 6.1, "Onion Creek at US 183"), _nb("nearby", True, 7.4)], rain=False)
    res = graph.run_alert(t, ALERT)
    cx = next(e["detail"] for e in res["events"] if e["agent"] == "context" and e["kind"] == "end")
    assert cx["direction"] == "rising" and cx["kind"] == "supporting" and "unknown" not in cx["summary"]
    assert res["outcome"] == "saved" and res["confidence"] == "normal"
    text = t.advisories[0]["text"]
    assert "supporting signal only" in text and "downstream Onion Creek at US 183" in text
    assert not checks._CAUSAL.search(text) and len(text) <= rules.MAX_ADVISORY_CHARS


def test_graph_quiet_neighbours_with_rising_gauge_is_not_uncertain():
    t = NeighbourTools([_nb("downstream", False), _nb("nearby", False)], rain=False)
    res = graph.run_alert(t, ALERT)
    assert res["confidence"] == "normal" and not [e for e in res["events"] if e["kind"] == "disagreement"]


# ------------------------------------------------------------------ facts in plain words

def test_facts_state_the_replay_and_why_rain_is_missing():
    f = advisory.build_facts(REAL, REAL_EV, NO_RAIN, 1)
    assert "replay of a historical USGS gauge record" in f["data_source"] and "SIMULATION" in f["data_source"]
    assert f["context"]["rain"] == "rain data not available"
    assert "Oct 2020" in f["context"]["rain_note"]
    later = advisory.build_facts({**REAL, "as_of": "2021-06-01T00:00:00Z"}, REAL_EV, NO_RAIN, 1)
    assert later["context"]["rain_note"] == "radar rain is not joined for this window"
    live = advisory.build_facts({**REAL, "mode": "LIVE"}, REAL_EV, NO_RAIN, 1)
    assert live["data_source"] == "live USGS gauge readings"


def test_facts_carry_no_booleans_or_raw_model_ids_for_the_writer():
    f = advisory.build_facts(REAL, REAL_EV, {**NO_RAIN, "neighbours": [_nb("nearby", True)]}, 1)
    a = f["alert"]
    assert "provisional_data" not in a and "as_of" not in a and a["readings_time_utc"] == "2019-05-04T02:00:00Z"
    assert a["data_status"] == "approved per USGS" and a["model"] == "rise-rate baseline"
    assert advisory.data_status(True).startswith("provisional per USGS") and "not stated" in advisory.data_status(None)
    assert f["context"]["neighbours"][0]["rising"] == "rising"
    assert "true" not in json.dumps(f["alert"]).lower() and "false" not in json.dumps(f["alert"]).lower()


def test_template_says_why_rain_is_missing_and_states_approved_data():
    t = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "rain data not available (radar archive starts Oct 2020)" in t and "Data: approved USGS record." in t
    assert "replay of a historical USGS gauge record" in t and "rise_rate" not in t
    assert checks.review(t, REAL, advisory.build_facts(REAL, REAL_EV, NO_RAIN, 1)) == []


# ------------------------------------------------------------------ deterministic raw-field check

def _ids(draft, cx=NO_RAIN):
    return {i for i, _ in checks.review_detailed(draft, REAL, advisory.build_facts(REAL, REAL_EV, cx, 1))}


def test_raw_field_leaks_from_the_laptop_runs_are_rejected():
    good = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "raw_field" not in _ids(good)
    for leak in ("Data: provisional data false.", "Data: provisional data not available.", "Readings as_of 2019.",
                 "provisional_data: False.", "Model: rise_rate.", "Provisional: true."):
        assert "raw_field" in _ids(good + "\n" + leak), leak


def test_supporting_context_may_not_be_called_a_cause_or_predictor():
    cx = {**NO_RAIN, "kind": "supporting", "direction": "rising"}
    good = advisory.template_text(REAL, REAL_EV, NO_RAIN, "normal", 1, None)
    assert "context_kind" not in _ids(good + "\nThe downstream gauge is also rising (supporting signal).", cx)
    for bad in ("The downstream gauge rising predicts more rise here.", "Rises at nearby gauges are causing this.",
                "The downstream gauge is a leading signal."):
        assert "context_kind" in _ids(good + "\n" + bad, cx), bad


def test_reviewer_llm_sees_the_replay_statement(monkeypatch):
    prompts = []
    t = FakeTools()   # scripted: the writer call fails (template text is used), the judge says PASS

    def fake(system, user, timeout_s=20, **k):
        prompts.append((system, user))
        if system.startswith("You review"):
            return llm.LlmReply(text="PASS", tokens=5, provider="groq")
        return llm.LlmReply(text=None, provider="groq", error="scripted: use template")
    monkeypatch.setattr(llm, "provider", lambda: "groq")
    monkeypatch.setattr(llm, "complete", fake)
    assert graph.run_alert(t, ALERT)["outcome"] == "saved"
    sys_r, user_r = next(p for p in prompts if p[0].startswith("You review"))
    assert "replay of a historical USGS record" in sys_r and "supporting context only" in sys_r
    facts = json.loads(user_r.split("FACTS (JSON):\n", 1)[1].split("\n\nDRAFT:", 1)[0])
    assert "replay of a historical USGS gauge record" in facts["data_source"]
    writer_user = next(p[1] for p in prompts if not p[0].startswith("You review"))
    assert "replay of a historical USGS gauge record" in writer_user
    assert "never write a field name" in advisory.SYSTEM_PROMPT


# ------------------------------------------------------------------ alert window_start (core, 31 Oct)

LEVELS = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}


def _row(ws, fc, stage):
    return {"window_start": ws, "forecast_stage": fc, "stage_last": stage, "model": "rise_rate", "horizon_h": 3.0,
            "p_major": None, "p_minor": None, "p_moderate": None, "p_action": None}


def test_anchor_uses_the_alert_window_start_when_present():
    rows = [_row("2019-05-04T01:00:00Z", 24.7, 8.16), _row("2019-05-04T03:00:00Z", 42.26, 22.69)]
    base = {"category": "major", "raised_at": "2026-10-31T08:00:00Z", "probability": None}
    assert anchor_window(base, LEVELS, rows)["window_start"] == "2019-05-04T01:00:00Z"            # old-row fallback
    own = {**base, "window_start": "2019-05-04T03:00:00Z"}
    assert anchor_window(own, LEVELS, rows)["window_start"] == "2019-05-04T03:00:00Z"
    ts = {**base, "window_start_ts": 1556938800.0}                                                  # 03:00Z as epoch
    assert anchor_window(ts, LEVELS, rows)["window_start"] == "2019-05-04T03:00:00Z"
    gone = {**base, "window_start": "2019-05-05T00:00:00Z"}                                         # no such row
    assert anchor_window(gone, LEVELS, rows)["window_start"] == "2019-05-04T01:00:00Z"


def test_pg_alert_details_window_start_is_used(pg_empty):
    from agentic_ai import seed
    from agentic_ai.tools import Tools
    seed.load(pg_empty)
    with pg_empty.cursor() as cur:
        cur.execute("UPDATE alerts SET raised_at = '2026-10-31T08:00:00Z', details = %s WHERE alert_id = %s",
                    (json.dumps({"window_start": "2019-05-04T03:00:00Z", "window_start_ts": 1556938800.0}),
                     seed.REPLAY_ALERT))
    a = Tools(pg_empty).get_alert(seed.REPLAY_ALERT)
    assert a["as_of"] == "2019-05-04T04:00:00Z" and a["current_stage"] == 22.69
    with pg_empty.cursor() as cur:   # an old row without details keeps the first-crossing fallback
        cur.execute("UPDATE alerts SET details = NULL WHERE alert_id = %s", (seed.REPLAY_ALERT,))
    assert Tools(pg_empty).get_alert(seed.REPLAY_ALERT)["as_of"] == "2019-05-04T02:00:00Z"


# ------------------------------------------------------------------ unknown abbreviations (live run 3 at low effort)

RUN3 = """SIMULATION - replay of a historical USGS gauge record, not a real-time event
Onion Creek near Driftwood (site 08158700) current stage 8.2 ft, forecast stage 24.7 ft
Forecast to reach MAJOR flood stage (13.0 ft) within 3 h; probability: not available
Lead time to MAJOR: about 0.9 h
Impacts: no NGS impact statement loaded
Uncertainty: supporting signal only - downstream Onion Creek at US 183 +6.1 ft in 3 h and nearby Slaughter Creek +7.4 ft in 3 h are rising in the same window; no leading signal; rain data not available (radar rain archive starts Oct 2020; this event is earlier, so there is no radar rain for it)
Data: approved per USGS; model rise-rate baseline; readings time 2019-05-04T02:00:00Z."""

RUN3_CX = {**NO_RAIN, "kind": "supporting", "direction": "rising",
           "neighbours": [_nb("downstream", True, 6.1, "Onion Creek at US 183"),
                          _nb("nearby", True, 7.4, "Slaughter Creek at FM 1826 near Austin")]}


def test_run3_unknown_abbreviation_ngs_is_sent_back():
    found = checks.review_detailed(RUN3, REAL, advisory.build_facts(REAL, REAL_EV, RUN3_CX, 1))
    assert [i for i, _ in found] == ["abbreviation"]
    assert "unknown abbreviation NGS" in found[0][1]
    fixed = RUN3.replace("NGS", "NWS")
    assert checks.review(fixed, REAL, advisory.build_facts(REAL, REAL_EV, RUN3_CX, 1)) == []


def test_abbreviations_from_facts_names_and_lids_are_allowed():
    facts = advisory.build_facts({**REAL, "site_name": "Onion Ck nr Driftwood DRWT TX"}, REAL_EV, RUN3_CX, 1)
    allowed = checks.allowed_abbreviations(facts)
    assert {"NWS", "USGS", "UTC", "US", "FM", "DRWT", "TX"} <= allowed
    good = advisory.template_text(REAL, REAL_EV, RUN3_CX, "normal", 1, None)
    assert "abbreviation" not in {i for i, _ in checks.review_detailed(good, REAL, facts)}
    assert "abbreviation" in {i for i, _ in checks.review_detailed(good + "\nNOT an official forecast.", REAL, facts)}
