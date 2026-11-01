"""tools/reset_replay.py: what it deletes, and how the check tools allow for a reset."""
import os
import uuid
from pathlib import Path

import pytest

from storage import sink
from tools import check_windows, reset_replay as rr

ROOT = Path(__file__).resolve().parent.parent
SPAN = rr.date_range("2019-05-02", "2019-05-06")


def test_date_range_includes_the_end_and_refuses_a_backward_range():
    assert SPAN == {"start": "2019-05-02T00:00:00Z", "end": "2019-05-07T00:00:00Z"}
    with pytest.raises(ValueError):
        rr.date_range("2019-05-06", "2019-05-02")


def test_sites_by_number_or_nwps_id():
    assert rr.resolve_sites("AHOT2, 08151500") == ["08156800", "08151500"]
    with pytest.raises(ValueError):
        rr.resolve_sites("NOPE1")


def test_every_delete_is_limited_to_simulation():
    for table, where in rr.DELETES:
        assert "mode = 'SIMULATION'" in where, table
    assert "evaluation_runs" not in {t for t, _ in rr.DELETES}


def test_forecaster_state_loses_only_the_replay_entry():
    s = {"a|SIMULATION": 1, "a|LIVE": 2, "b|SIMULATION": 3}
    assert rr.drop_forecaster_state(s, ["a"]) == {"a|LIVE": 2, "b|SIMULATION": 3}


def test_only_changelogs_of_replay_state_are_written():
    assert all("replay" in t for t in rr.CHANGELOGS)


def test_older_replay_records_are_superseded_never_live_ones():
    resets = {"s": 10.0}
    assert rr.superseded("s", "SIMULATION", 5.0, resets) and not rr.superseded("s", "SIMULATION", 15.0, resets)
    assert not rr.superseded("s", "LIVE", 5.0, resets) and not rr.superseded("x", "SIMULATION", 5.0, resets)


def test_check_windows_counts_a_window_replayed_after_a_reset_once():
    w = {"site_no": "s", "series": "1", "mode": "SIMULATION", "window_start_ts": 100.0, "n_readings": 1}
    assert check_windows.summarise([w, w])["duplicates"]
    assert not check_windows.summarise([{**w, "_ts": 5.0}, {**w, "_ts": 15.0}], {"s": 10.0})["duplicates"]
    assert check_windows.summarise([{**w, "_ts": 11.0}, {**w, "_ts": 15.0}], {"s": 10.0})["duplicates"]


@pytest.fixture()
def conn():
    dsn = os.getenv("AGENTS_TEST_DSN")
    if not dsn:
        pytest.skip("AGENTS_TEST_DSN not set: PostgreSQL tests skipped")
    import psycopg2
    schema = "reset_t_" + uuid.uuid4().hex[:8]
    c = psycopg2.connect(dsn, connect_timeout=5)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        for f in ("schema.sql", "views.sql"):
            cur.execute((ROOT / "storage" / f).read_text(encoding="utf-8"))
    c.autocommit = False
    yield c
    c.rollback()
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


def _window(site, when, mode):
    return (site, when, when, mode, 1, 1.0, 1.0, 1.0, 0.0, None, None, None, None, False)


def test_delete_touches_only_that_site_mode_and_range_and_saves_alerts(conn, tmp_path):
    sink.write_batch(conn, {"windowed_aggregates": [_window("a", "2019-05-04T00:00:00Z", "SIMULATION"), _window("a", "2019-05-08T00:00:00Z", "SIMULATION"),
                                                    _window("a", "2019-05-04T00:00:00Z", "LIVE"), _window("b", "2019-05-04T00:00:00Z", "SIMULATION")]})
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO alerts (alert_id, site_no, mode, category, raised_at) VALUES ('a|minor|2019-05-04T00:00:00Z','a','SIMULATION','minor',now()),"
                    "('a|minor|2019-05-08T00:00:00Z','a','SIMULATION','minor',now()),('a|minor|2019-05-05T00:00:00Z','a','LIVE','minor',now())")
        cur.execute("INSERT INTO advisories (advisory_id, alert_id, version, source, text) VALUES ('a|minor|2019-05-04T00:00:00Z|1','a|minor|2019-05-04T00:00:00Z',1,'template','x')")
        cur.execute("INSERT INTO evaluation_runs (run_label, data_source, site_no) VALUES ('keep','replay','a')")
    assert rr.count_rows(conn, "a", SPAN)["alerts"] == 1
    conn.commit()
    with open(tmp_path / "b.jsonl", "w", encoding="utf-8") as f:
        done = rr.delete_rows(conn, "a", SPAN, f)
    assert done["windowed_aggregates"] == 1 and done["alerts"] == 1 and done["advisories"] == 1
    assert len((tmp_path / "b.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM windowed_aggregates")
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT alert_id FROM alerts ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == ["a|minor|2019-05-05T00:00:00Z", "a|minor|2019-05-08T00:00:00Z"]
        cur.execute("SELECT count(*) FROM evaluation_runs")
        assert cur.fetchone()[0] == 1
