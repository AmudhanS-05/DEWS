"""Import evaluation_runs-shaped CSVs (ml/harness.py COLUMNS) into PostgreSQL, idempotently.

Rows produced in another place (e.g. the cloud session) reach the laptop DB this way. evaluation_runs has no unique
key, so a row's identity is (run_label, data_source, site_no, model, horizon_h): a row whose identity is already in
the table is skipped (or, with --replace, the old rows with that identity are deleted and the new one inserted).
Empty / "NA" cells become NULL. Needs PG_DSN (default from common.config) and psycopg2.

    python -m ml.import_runs ml/figures/baselines.csv [ml/figures/model_v1_vs_v0.csv ...] [--replace] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv

from ml.harness import COLUMNS

KEY = ("run_label", "data_source", "site_no", "model", "horizon_h")
FLOATS = {"horizon_h", "precision", "recall", "f1", "median_lead_time_h", "mae_ft", "rmse_ft"}
INTS = {"false_alarms"}


def read_rows(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: not an evaluation_runs CSV, missing columns {missing}")
        for r in rd:
            row = {}
            for c in COLUMNS:
                v = r[c]
                if v is None or v.strip() in ("", "NA", "None", "nan"):
                    row[c] = None
                elif c in FLOATS:
                    row[c] = float(v)
                elif c in INTS:
                    row[c] = int(float(v))
                else:
                    row[c] = v
            if row["data_source"] not in ("replay", "live"):
                raise ValueError(f"{path}: data_source must be replay or live, got {row['data_source']!r}")
            out.append(row)
    return out


_WHERE = ("run_label = %s AND data_source = %s AND site_no IS NOT DISTINCT FROM %s AND model = %s "
          "AND horizon_h IS NOT DISTINCT FROM %s")


def upsert(cur, rows: list[dict], replace: bool = False) -> dict:
    """Returns {inserted, skipped, replaced}. `cur` is a DB-API cursor (psycopg2)."""
    n = {"inserted": 0, "skipped": 0, "replaced": 0}
    cols = ",".join(COLUMNS)
    ph = ",".join(["%s"] * len(COLUMNS))
    for r in rows:
        key = [r[k] for k in KEY]
        if replace:
            cur.execute(f"DELETE FROM evaluation_runs WHERE {_WHERE}", key)
            n["replaced"] += 1 if (cur.rowcount or 0) > 0 else 0
        cur.execute(f"INSERT INTO evaluation_runs ({cols}) SELECT {ph} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM evaluation_runs WHERE {_WHERE})",
                    [r[c] for c in COLUMNS] + key)
        if (cur.rowcount or 0) > 0:
            n["inserted"] += 1
        else:
            n["skipped"] += 1
    return n


def main(argv=None):
    from common import config
    from ml.progress import Timer
    p = argparse.ArgumentParser(description="Import evaluation_runs CSVs into PostgreSQL (idempotent).")
    p.add_argument("csv", nargs="+")
    p.add_argument("--replace", action="store_true", help="replace rows with the same identity instead of skipping")
    p.add_argument("--dry-run", action="store_true", help="parse and count only, no DB connection")
    p.add_argument("--dsn", default=config.PG_DSN)
    a = p.parse_args(argv)
    timer = Timer()
    files = {path: read_rows(path) for path in a.csv}
    print(f"ml.import_runs: {sum(len(r) for r in files.values())} rows from {len(files)} file(s) -> evaluation_runs"
          f"{' (dry run)' if a.dry_run else ''}")
    if a.dry_run:
        for path, rows in files.items():
            print(f"  {path}: {len(rows)} rows, labels {sorted({r['run_label'] for r in rows})}")
        return
    import psycopg2
    tot = {"inserted": 0, "skipped": 0, "replaced": 0}
    with psycopg2.connect(a.dsn) as conn, conn.cursor() as cur:
        for path, rows in files.items():
            n = upsert(cur, rows, a.replace)
            print(f"  {path}: inserted {n['inserted']}, skipped (already present) {n['skipped']}, "
                  f"replaced {n['replaced']}")
            for k in tot:
                tot[k] += n[k]
    print(f"done: inserted {tot['inserted']}, skipped {tot['skipped']}, replaced {tot['replaced']} "
          f"in {timer.elapsed()}")


if __name__ == "__main__":
    main()
