"""ml.import_runs: CSV parsing and idempotent insert logic against an in-memory fake cursor (no PostgreSQL here)."""
import re

import pytest

from ml import harness, import_runs as I


class FakeCursor:
    """Implements just the two statements import_runs issues, over a list of dict rows."""
    def __init__(self):
        self.table, self.rowcount = [], 0

    def _match(self, r, key):
        return all(r[k] == v for k, v in zip(I.KEY, key))

    def execute(self, sql, params):
        if sql.startswith("DELETE"):
            before = len(self.table)
            self.table = [r for r in self.table if not self._match(r, params)]
            self.rowcount = before - len(self.table)
        elif sql.startswith("INSERT"):
            n = len(harness.COLUMNS)
            row, key = dict(zip(harness.COLUMNS, params[:n])), params[n:]
            assert re.search(r"WHERE NOT EXISTS", sql)
            if any(self._match(r, key) for r in self.table):
                self.rowcount = 0
            else:
                self.table.append(row)
                self.rowcount = 1


def _csv(tmp_path, rows):
    p = tmp_path / "runs.csv"
    harness.write_csv(rows, str(p))
    return str(p)


ROW = {"run_label": "m1.4-baselines", "data_source": "replay", "site_no": "POOLED", "model": "rise_rate",
       "horizon_h": 3.0, "precision": 0.18, "recall": 0.61, "f1": 0.28, "false_alarms": 856,
       "median_lead_time_h": 0.83, "mae_ft": 0.066, "rmse_ft": None, "notes": "x"}


def test_parse_types_and_nulls(tmp_path):
    rows = I.read_rows(_csv(tmp_path, [ROW, {**ROW, "site_no": "08158700", "precision": None, "f1": None}]))
    assert rows[0]["horizon_h"] == 3.0 and rows[0]["false_alarms"] == 856 and rows[0]["rmse_ft"] is None
    assert rows[1]["precision"] is None and rows[1]["f1"] is None


def test_insert_is_idempotent_and_replace_updates(tmp_path):
    rows = I.read_rows(_csv(tmp_path, [ROW, {**ROW, "horizon_h": 6.0}]))
    cur = FakeCursor()
    assert I.upsert(cur, rows) == {"inserted": 2, "skipped": 0, "replaced": 0}
    assert I.upsert(cur, rows) == {"inserted": 0, "skipped": 2, "replaced": 0}
    assert len(cur.table) == 2
    new = [{**rows[0], "f1": 0.5}]
    assert I.upsert(cur, new, replace=True) == {"inserted": 1, "skipped": 0, "replaced": 1}
    assert len(cur.table) == 2 and sorted(r["f1"] for r in cur.table) == [0.28, 0.5]


def test_rejects_wrong_files(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="missing columns"):
        I.read_rows(str(p))
    with pytest.raises(ValueError, match="data_source"):
        I.read_rows(_csv(tmp_path, [{**ROW, "data_source": "mixed"}]))


def test_committed_baselines_parse():
    rows = I.read_rows("ml/figures/baselines.csv")
    assert len(rows) == 480 and {r["data_source"] for r in rows} == {"replay"}
