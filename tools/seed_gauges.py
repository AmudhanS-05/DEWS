"""Fill the gauges table from config/gauge_locations.csv: name, NWPS id and official levels of every gauge.

    python -m tools.seed_gauges              # scripts/start_demo.ps1 runs this once PostgreSQL is healthy
    python -m tools.seed_gauges --dry-run    # counts only

Safe to run twice: a row changes only if the file differs. median_rise_h is left alone (the file does not have it), and no
row is ever deleted. tools.gauge_locations writes the file from NWPS, so these are official levels, never invented ones.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common import config

ROOT = Path(__file__).resolve().parent.parent
GAUGES_CSV = ROOT / "config" / "gauge_locations.csv"
UPSERT = ("INSERT INTO gauges (site_no, nwps_lid, name, stage_units, action_stage, minor_stage, moderate_stage, major_stage) "
          "VALUES (%(site_no)s, %(lid)s, %(name)s, %(units)s, %(action)s, %(minor)s, %(moderate)s, %(major)s) "
          "ON CONFLICT (site_no) DO UPDATE SET nwps_lid = EXCLUDED.nwps_lid, name = EXCLUDED.name, stage_units = EXCLUDED.stage_units, "
          "action_stage = EXCLUDED.action_stage, minor_stage = EXCLUDED.minor_stage, moderate_stage = EXCLUDED.moderate_stage, "
          "major_stage = EXCLUDED.major_stage, updated_at = now() "
          "WHERE (gauges.nwps_lid, gauges.name, gauges.stage_units, gauges.action_stage, gauges.minor_stage, gauges.moderate_stage, gauges.major_stage) "
          "IS DISTINCT FROM (EXCLUDED.nwps_lid, EXCLUDED.name, EXCLUDED.stage_units, EXCLUDED.action_stage, EXCLUDED.minor_stage, EXCLUDED.moderate_stage, EXCLUDED.major_stage)")


def _level(text: str | None) -> float | None:
    """A level the file does not have stays NULL: never a made-up value."""
    return float(text) if text and text.strip() else None


def gauge_rows(path: Path = GAUGES_CSV) -> list[dict]:
    """One dict per gauge in the file, ready for UPSERT."""
    with open(path, encoding="utf-8", newline="") as f:
        return [{"site_no": r["site_no"].strip(), "lid": r["lid"].strip(), "name": r["name"].strip(), "units": r["units"].strip(),
                 "action": _level(r["action"]), "minor": _level(r["minor"]), "moderate": _level(r["moderate"]), "major": _level(r["major"])}
                for r in csv.DictReader(f)]


def seed(conn, rows: list[dict]) -> int:
    """Upsert in one transaction. Returns how many rows were added or changed."""
    changed = 0
    with conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(UPSERT, row)
            changed += cur.rowcount
    return changed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, default=GAUGES_CSV)
    p.add_argument("--dry-run", action="store_true", help="print the counts, change nothing")
    a = p.parse_args(argv)
    rows = gauge_rows(a.csv)
    if not rows:
        print(f"seed_gauges: FAIL: no gauges in {a.csv}")
        return 1
    if a.dry_run:
        print(f"seed_gauges: {len(rows)} gauges in {a.csv.name}; dry run, nothing changed")
        return 0
    import psycopg2
    conn = psycopg2.connect(config.PG_DSN, connect_timeout=5)
    try:
        changed = seed(conn, rows)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM gauges")
            total = cur.fetchone()[0]
    finally:
        conn.close()
    print(f"seed_gauges: PASS: {len(rows)} gauges in {a.csv.name}, {changed} added or changed, {total} in the table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
