"""ml.rain_model: rain sums never treat a missing hour as zero, alert rule, crossing filter, weights, and an
end-to-end smoke run on synthetic data (synthetic results are never findings)."""
import math

import numpy as np
import pandas as pd

from ml import rain_model as rm
from ml.dataset import event_weights
from tests.ml import synth

H = 3600.0


def test_rain_sums_and_missing_hours():
    hours = np.array([10, 11, 12, 13, 15]) * H           # hour 14 missing
    mm = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    f = rm.rain_features(np.array([13 * H, 13.5 * H, 15 * H]), hours, mm, sums=(1, 3))
    assert f["rain_1h"][0] == 4.0 and f["rain_3h"][0] == 9.0      # hours ending 11, 12, 13
    assert f["rain_1h"][1] == 4.0                                  # t inside hour 14 -> last full hour is 13
    assert f["rain_1h"][2] == 5.0 and math.isnan(f["rain_3h"][2])  # needs hour 14 -> NaN, not zero
    lag = rm.rain_features(np.array([13 * H]), hours, mm, sums=(1,), lag_h=1)
    assert lag["rain_1h"][0] == 3.0


def test_alert_rule_cooldown_and_below_threshold():
    t = np.arange(10) * H
    cur = np.array([1, 1, 1, 9, 1, 1, 1, 1, 1, 1], dtype=float)
    fire = np.ones(10, dtype=bool)
    al = rm.alerts_from_flags(t, cur, 8.0, fire)
    assert [a[0] for a in al] == [0.0, 6 * H]                      # 6 h cooldown; never while already above


def test_crossing_needs_a_row_before_it():
    cr = [(10 * H, "minor", True), (50 * H, "minor", False)]
    assert rm.crossings_in_rows(cr, np.array([8 * H, 9 * H]), 3) == [cr[0]]


def test_vectorised_weights_match_dataset_definition():
    rows = [{"site_no": s, "event_id": e} for s, e in
            [("A", ""), ("A", "A|1"), ("A", "A|1"), ("A", "A|2"), ("B", ""), ("B", ""), ("B", "B|9")]]
    w = rm.equal_gauge_event_weights(np.array([r["site_no"] for r in rows]), np.array([r["event_id"] for r in rows]))
    assert np.allclose(w, event_weights(rows))


def test_end_to_end_synthetic_smoke():
    from threadpoolctl import threadpool_limits
    with threadpool_limits(1):            # OpenMP in HistGradientBoosting stalls badly when the CPUs are busy
        _smoke()


def _smoke():
    gauges, rain = [], []
    for k, site in enumerate(["S1", "S2", "S3"]):
        rs = synth.hydrograph(site, days=200, seed=k, n_storms=10, rise_h=6.0)
        gauges.append({"site_no": site, "lid": site, "cats": synth.CATS, "readings": rs})
        t0, t1 = rs[0]["reading_ts"], rs[-1]["reading_ts"]
        hrs = np.arange(t0, t1, H)
        stage = np.interp(hrs + 6 * H, [r["reading_ts"] for r in rs], [r["value"] for r in rs])
        rain.append(pd.DataFrame({"site_no": site, "hour_ts": hrs, "radius_km": 10.0,
                                  "rain_mm": np.clip(stage - 5, 0, None), "coverage": 1.0, "product": "synthetic"}))
    df, info = rm.build_rows(gauges, pd.concat(rain), 10.0, 0.0)
    assert set(rm.V1) <= set(df.columns) and df.rain_24h.notna().all()
    split = df.t.quantile(0.6)
    train, test = df[df.t < split], df[df.t >= split]
    models = {"v0": rm.fit_one(train, rm.V0, 3, info), "v1": rm.fit_one(train, rm.V1, 3, info)}
    rows, _ = rm.evaluate_models(test, models, 3, info, "synthetic-test")
    assert {r["model"] for r in rows} == {"v0", "v1", "rise_rate"}
    assert {"POOLED", "POOLED-SLOW"} <= {r["site_no"] for r in rows}
    assert all(r["data_source"] == "replay" for r in rows)
