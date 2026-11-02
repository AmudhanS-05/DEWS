"""tools/seed_gauges.py: the gauges table holds what config/gauge_locations.csv says, and nothing else is disturbed."""
import os
import uuid
from pathlib import Path

import pytest

from tools import reset_replay as rr
from tools import seed_gauges as sg

ROOT = Path(__file__).resolve().parent.parent
HEADER = "site_no,lid,name,lat,lon,county,state,action,minor,moderate,major,units\n"


def test_the_committed_file_gives_38_distinct_gauges_with_all_four_levels():
    rows = sg.gauge_rows()
    assert len(rows) == 38 and len({r["site_no"] for r in rows}) == 38
    for r in rows:
        assert all(r[k] is not None for k in ("action", "minor", "moderate", "major")), r["lid"]


def test_a_row_holds_the_file_values_with_stray_spaces_removed(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text(HEADER + "08158840,SCAT2,Slaughter Creek ,30.1,-97.8,Travis,TX,8.0,8.5,9.0,15.0,ft\n", encoding="utf-8")
    assert sg.gauge_rows(p) == [{"site_no": "08158840", "lid": "SCAT2", "name": "Slaughter Creek", "units": "ft",
                                 "action": 8.0, "minor": 8.5, "moderate": 9.0, "major": 15.0}]


def test_a_missing_level_stays_empty_and_is_never_made_up(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text(HEADER + "1,A,N,0,0,C,TX,5.0,,7.0,,ft\n", encoding="utf-8")
    r = sg.gauge_rows(p)[0]
    assert r["minor"] is None and r["major"] is None and r["action"] == 5.0


def test_an_empty_file_fails_and_a_dry_run_opens_no_connection(tmp_path, capsys):
    p = tmp_path / "g.csv"
    p.write_text(HEADER, encoding="utf-8")
    assert sg.main(["--csv", str(p)]) == 1
    p.write_text(HEADER + "1,A,N,0,0,C,TX,5.0,6.0,7.0,8.0,ft\n", encoding="utf-8")
    assert sg.main(["--csv", str(p), "--dry-run"]) == 0
    assert "1 gauges" in capsys.readouterr().out


def test_a_replay_reset_never_deletes_gauges():
    assert "gauges" not in {t for t, _ in rr.DELETES}


def test_start_demo_seeds_after_postgres_is_healthy_and_before_the_services():
    s = (ROOT / "scripts" / "start_demo.ps1").read_text(encoding="utf-8")
    assert s.index("'brokers and PostgreSQL healthy'") < s.index("tools.seed_gauges") < s.index("foreach ($s in $services)")


@pytest.fixture()
def conn():
    dsn = os.environ.get("AGENTS_TEST_DSN")
    if not dsn:
        pytest.skip("AGENTS_TEST_DSN not set: PostgreSQL tests skipped")
    import psycopg2
    schema = "seed_t_" + uuid.uuid4().hex[:8]
    c = psycopg2.connect(dsn, connect_timeout=5)
    with c, c.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        cur.execute((ROOT / "storage" / "schema.sql").read_text(encoding="utf-8"))
    yield c
    c.rollback()
    with c, c.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


def _gauges(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT site_no, nwps_lid, name, stage_units, action_stage, minor_stage, moderate_stage, major_stage, median_rise_h "
                    "FROM gauges ORDER BY site_no")
        return cur.fetchall()


def test_seed_loads_every_gauge_with_the_file_values_and_a_second_run_changes_nothing(conn):
    rows = sg.gauge_rows()
    assert sg.seed(conn, rows) == 38
    want = sorted((r["site_no"], r["lid"], r["name"], r["units"], r["action"], r["minor"], r["moderate"], r["major"], None) for r in rows)
    assert _gauges(conn) == want
    assert sg.seed(conn, rows) == 0


def test_seed_corrects_a_changed_level_and_keeps_median_rise_h(conn):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO gauges (site_no, nwps_lid, name, stage_units, action_stage, minor_stage, moderate_stage, major_stage, median_rise_h) "
                    "VALUES ('08158700','DRWT2','Onion Creek near Driftwood','ft',5.5,5.5,7.0,99.0,10.9), ('X','X','kept','ft',1,2,3,4,NULL)")
    rows = [r for r in sg.gauge_rows() if r["lid"] == "DRWT2"]
    assert sg.seed(conn, rows) == 1
    got = {g[0]: g for g in _gauges(conn)}
    assert got["08158700"][7] == rows[0]["major"] and got["08158700"][8] == 10.9
    assert "X" in got                                                     # a row the file does not have is not deleted
