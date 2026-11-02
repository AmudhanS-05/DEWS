"""A3.1: the deterministic reviewer catches every seeded error on corrupted drafts (agentic_ai/eval/corrupt.py),
and the new checks (category_stage, threshold, cause, update, suspect_data) do not fire on correct text."""
import pytest

from agentic_ai import advisory, checks
from agentic_ai.eval import catch_rate, corrupt

CASES = catch_rate.base_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_base_drafts_pass(case):
    assert checks.review(case["text"], case["alert"], case["facts"], case["confidence"]) == []


def test_every_seeded_error_is_caught():
    res = corrupt.catch_rate(CASES)
    missed = [(r["case"], r["kind"]) for r in res["rows"] if not r["caught"]]
    assert not missed and res["total"] >= 70
    assert set(corrupt.SLICE_KINDS) <= set(res["by_kind"])          # the six core error kinds


def _ids(text, case):
    return {i for i, _ in checks.review_detailed(text, case["alert"], case["facts"], case["confidence"])}


def test_category_with_another_official_stage_is_caught():
    c = CASES[0]                                   # DRWT2 major: MAJOR is 13.0 ft, MODERATE 7.0 ft
    bad = c["text"].replace("MAJOR flood stage (13.0 ft)", "MAJOR flood stage (7.0 ft)")
    assert "category_stage" in _ids(bad, c)


def test_official_stage_list_and_correct_pairs_are_not_flagged():
    c = CASES[1]
    for ok in ("Official stages: action 5.5, minor 5.5, moderate 7.0, major 13.0 ft.",
               "forecast to reach MINOR flood stage (5.5 ft) within 6 h", "Alert category: MINOR (5.5 ft)"):
        assert "category_stage" not in _ids(c["text"] + "\n" + ok, c), ok


def test_threshold_with_a_number_from_the_facts_is_caught():
    c = CASES[1]
    assert "threshold" in _ids(c["text"] + "\nThe warning level is 2.6 ft.", c)
    assert "threshold" not in _ids(c["text"] + "\nThe flood threshold is the official MINOR stage, 5.5 ft.", c)


def test_rain_archive_reason_is_not_a_cause_claim():
    c = CASES[0]
    ok = c["text"] + "\nRain data not available because of the radar archive start (Oct 2020)."
    assert "cause" not in _ids(ok, c)
    assert "cause" in _ids(c["text"] + "\nThe rise is due to heavy rain upstream.", c)


def test_downstream_cause_is_caught_whatever_the_context_kind():
    c = CASES[1]                                   # leading context (rain), downstream gauge present
    assert "context_kind" in _ids(c["text"] + "\nDownstream Onion Creek at US 183 is causing this rise.", c)


def test_update_wording():
    first, repeat = CASES[1], CASES[3]
    assert "Update 2:" in repeat["text"] and "update" not in _ids(repeat["text"], repeat)
    assert "update" in _ids(repeat["text"].replace("Update 2: ", ""), repeat)
    assert "update" in _ids(repeat["text"].replace("Update 2", "Update 3"), repeat)
    assert "update" in _ids("Update 2: " + first["text"], first)


def test_suspect_data_must_be_mentioned_when_flagged():
    c = CASES[1]
    facts = {**c["facts"], "data_quality": "readings at this gauge look like a sensor fault (stuck)"}
    assert "suspect_data" in {i for i, _ in checks.review_detailed(c["text"], c["alert"], facts)}
    t = c["text"] + "\nData quality: readings at this gauge look like a sensor fault (stuck); treat them as questionable."
    assert "suspect_data" not in {i for i, _ in checks.review_detailed(t, c["alert"], facts)}


def test_catch_rate_cli_writes_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(catch_rate, "HERE", tmp_path)
    res = catch_rate.main([])
    assert res["caught"] == res["total"] and (tmp_path / "reviewer_catch_rate.csv").exists()
    assert "Caught" in (tmp_path / "reviewer_catch_rate.md").read_text(encoding="utf-8")


def test_corruptions_change_the_text_and_keep_template_untouched():
    c = CASES[0]
    before = c["text"]
    errs = corrupt.seed_errors(c["text"], c["alert"], c["facts"])
    assert all(e["text"] != before for e in errs) and c["text"] == before
    assert advisory.template_text  # template writer still importable
