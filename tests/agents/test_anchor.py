"""Regression (laptop runs, 31 Oct): evidence read 'stage 22.69 ft, rate 6.52364 ft/h, forecast 42.2609 ft' for the
seeded alert (2.6 ft now, 6.8 ft forecast). Cause: core's replay of the real 2019 record upserts the same
windowed_aggregates keys the illustrative seed used, and get_forecast took the newest row of any horizon (core's 3 h
rise-rate row) instead of the alert's own. A replayed alert's raised_at is wall-clock time too, so 'latest window
before raised_at' would be the end of the replay. Evidence is now anchored to the alert's own window and horizon."""
from datetime import datetime, timedelta, timezone

import pytest

from agentic_ai import graph, seed
from agentic_ai.tools import Tools, anchor_window
from tests.agents.fake_tools import FakeTools

LEVELS = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}


def _row(ws, fc, stage, model="rise_rate", p=None):
    return {"window_start": ws, "forecast_stage": fc, "stage_last": stage, "model": model, "horizon_h": 3.0,
            "p_major": p, "p_minor": p, "p_moderate": p, "p_action": p}


def test_anchor_exact_window_end_wins():
    rows = [_row("2019-05-04T01:00:00Z", 24.7, 8.2), _row("2019-05-04T02:00:00Z", 39.9, 16.0)]
    a = {"category": "major", "raised_at": "2019-05-04T03:00:00Z", "probability": None}
    assert anchor_window(a, LEVELS, rows)["window_start"] == "2019-05-04T02:00:00Z"


def test_anchor_wall_clock_raised_at_uses_first_crossing_not_latest_window():
    rows = [_row("2019-05-04T00:00:00Z", 3.4, 3.05), _row("2019-05-04T01:00:00Z", 24.7, 8.16),
            _row("2019-05-04T03:00:00Z", 42.26, 22.69)]
    a = {"category": "major", "raised_at": "2026-10-31T08:00:00Z", "probability": None}
    assert anchor_window(a, LEVELS, rows)["window_start"] == "2019-05-04T01:00:00Z"


def test_anchor_upgrade_is_the_later_crossing_and_model_matches_probability():
    rows = [_row("2019-05-04T01:00:00Z", 6.0, 3.0), _row("2019-05-04T02:00:00Z", 7.5, 4.0, "ml", 0.4),
            _row("2019-05-04T02:00:00Z", 7.6, 4.0, "rise_rate", 0.9)]
    a = {"category": "moderate", "raised_at": "2026-10-31T08:00:00Z", "probability": 0.4}
    r = anchor_window(a, LEVELS, rows)
    assert r["window_start"] == "2019-05-04T02:00:00Z" and r["model"] == "ml"
    assert anchor_window(a, LEVELS, []) is None


def test_fake_tools_ignore_later_windows_and_other_horizons():
    """Illustrative seed + later, larger 'real-looking' rows (3 h horizon, after the alert) + wall-clock raised_at."""
    t = FakeTools()
    aid = "08158700|minor|2019-05-04T03:00:00Z"
    t.alerts[aid] = dict(t.alerts[aid], raised_at=datetime(2026, 10, 31, 8, tzinfo=timezone.utc))
    for r in list(t.risk):
        if r["site_no"] == seed.SITE:
            t.risk.append(dict(r, horizon_h=3.0, forecast_stage=r["forecast_stage"] + 30.0))
    for w in t.windows:
        if w["site_no"] == seed.SITE and w["window_start"] >= datetime(2019, 5, 4, 4, tzinfo=timezone.utc):
            w["stage_last"], w["rise_rate_ft_per_h"] = 22.69, 6.52364
    a = t.get_alert(aid)
    assert (a["current_stage"], a["forecast_stage"], a["as_of"]) == (2.6, 6.8, "2019-05-04T04:00:00Z")
    res = graph.run_alert(t, aid)
    ev = next(e["detail"] for e in res["events"] if e["agent"] == "evidence" and e["kind"] == "end")
    assert (ev["stage_last"], ev["rise_rate_ft_per_h"], ev["forecast_stage"]) == (2.6, 0.7, 6.8)


# ---------------------------------------------------------------- PostgreSQL (AGENTS_TEST_DSN)

def _q(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None


def test_replay_seed_matches_the_measured_record(pg_empty):
    assert seed.load(pg_empty) == seed.REPLAY_ALERT
    assert seed.load(pg_empty) == seed.REPLAY_ALERT          # idempotent
    a = Tools(pg_empty).get_alert(seed.REPLAY_ALERT)
    assert a["category"] == "major" and a["probability"] is None and a["horizon_h"] == 3.0
    assert a["current_stage"] == 8.16 and round(a["forecast_stage"], 2) == 24.72 and a["as_of"] == "2019-05-04T02:00:00Z"
    # the window the laptop evidence showed is real, but it is two hours after this alert
    assert _q(pg_empty, "SELECT stage_last FROM windowed_aggregates WHERE site_no='08158700' "
                        "AND window_start='2019-05-04T03:00:00Z'")[0][0] == 22.69


def test_wall_clock_raised_at_and_later_windows_still_give_the_alert_window(pg_empty):
    seed.load(pg_empty)
    _q(pg_empty, "UPDATE alerts SET raised_at = '2026-10-31T08:00:00Z' WHERE alert_id = %s", (seed.REPLAY_ALERT,))
    res = graph.run_alert(Tools(pg_empty), seed.REPLAY_ALERT)
    ev = next(e["detail"] for e in res["events"] if e["agent"] == "evidence" and e["kind"] == "end")
    assert ev["stage_last"] == 8.16 and round(ev["forecast_stage"], 2) == 24.72


def test_illustrative_seed_then_replay_is_cleaned_and_never_mixed(pg_empty):
    """The laptop state: illustrative seed first, then core's replay overwrote its windows."""
    seed.load(pg_empty, illustrative=True)
    seed.load(pg_empty)                       # what a replay + re-seed writes; drops the illustrative alerts
    ids = {r[0] for r in _q(pg_empty, "SELECT alert_id FROM alerts")}
    assert not ids & set(seed.ILLUSTRATIVE_ALERTS) and seed.REPLAY_ALERT in ids
    assert _q(pg_empty, "SELECT count(*) FROM risk_scores WHERE horizon_h = 6")[0][0] == 0
    assert _q(pg_empty, "SELECT count(*) FROM windowed_aggregates WHERE rain_1h IS NOT NULL")[0][0] == 0
    with pytest.raises(RuntimeError, match="replay rows"):
        seed.load(pg_empty, illustrative=True)
