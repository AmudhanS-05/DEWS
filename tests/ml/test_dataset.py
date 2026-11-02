"""Dataset builder mechanics on a synthetic hydrograph (tests the code, not a result)."""
from ml import dataset
from tests.ml.synth import CATS, hydrograph

RS = hydrograph("SYN1", days=90, seed=1, n_storms=4, peak=12.0)


def rows():
    return dataset.build_site(RS, CATS)


def test_one_row_per_window_with_relative_stage():
    r = rows()
    assert len(r) > 90 * 24 - 5
    assert all(abs(x["rel_minor"] - (x["stage"] - CATS["minor"])) < 1e-9 for x in r)
    assert [x["t"] for x in r] == sorted(x["t"] for x in r)


def test_cross_label_matches_future_stage_and_is_not_set_when_already_above():
    series = {x["reading_ts"]: x["value"] for x in RS}
    for x in rows():
        for h in (1, 3, 6):
            fut = [v for ts, v in series.items() if x["t"] < ts <= x["t"] + h * 3600]
            expect = int(x["stage"] < CATS["minor"] and bool(fut) and max(fut) >= CATS["minor"])
            assert x[f"y_cross_{h}"] == expect


def test_labels_grow_with_horizon_and_events_found():
    r = rows()
    assert sum(x["y_cross_6"] for x in r) >= sum(x["y_cross_1"] for x in r) > 0
    ev = {x["event_id"] for x in r if x["event_id"]}
    assert len(ev) == 4 and all(x["slow"] is True for x in r if x["event_id"])   # 8 h rise = slow


def test_fast_creek_flagged_not_slow():
    fast = dataset.build_site(hydrograph("F1", days=60, seed=2, n_storms=3, rise_h=1.0), CATS)
    assert {x["slow"] for x in fast if x["event_id"]} == {False}


def test_pooled_sorted_and_time_split_has_no_overlap():
    pooled = dataset.build_pooled({"A": RS, "B": hydrograph("B", days=90, seed=3)}, {"A": CATS, "B": CATS})
    assert {x["site_no"] for x in pooled} == {"SYN1", "B"}
    cut = pooled[len(pooled) // 2]["t"]
    tr, te = dataset.time_split(pooled, cut)
    assert max(x["t"] for x in tr) < cut <= min(x["t"] for x in te)


def test_event_weights_equalise_gauges():
    pooled = dataset.build_pooled({"A": RS, "B": hydrograph("B", days=90, seed=3, n_storms=2)}, {"A": CATS, "B": CATS})
    w = dataset.event_weights(pooled)
    tot = {s: sum(wi for wi, x in zip(w, pooled) if x["site_no"] == s) for s in ("SYN1", "B")}
    assert abs(tot["SYN1"] - tot["B"]) < 0.5 * max(tot.values()) and all(v > 0 for v in tot.values())


def test_cli_writes_parquet(tmp_path, monkeypatch):
    import pytest
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from ml import gauges
    monkeypatch.setattr(gauges, "iter_gauges", lambda *a, **k: iter([{"site_no": "SYN1", "lid": "X", "cats": CATS, "readings": RS}]))
    monkeypatch.setattr(gauges, "count_gauges", lambda *a, **k: 1)
    out = tmp_path / "d.parquet"
    dataset.main(["--out", str(out)])
    df = pd.read_parquet(out)
    assert len(df) == len(rows()) and df["t"].is_monotonic_increasing and "y_cross_3" in df
