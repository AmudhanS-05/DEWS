"""ml.mrms_history pure helpers: circle on the grid, mean depth with no-coverage cells, event/dry hour selection."""
import math
from datetime import datetime, timezone

import numpy as np

from ml import mrms_history as m

# a small grid laid out like MRMS (0.01 deg, rows north -> south, 0..360 longitudes)
GEOM = {"ni": 400, "nj": 400, "lat_first": 31.995, "lon_first": 360 - 99.995, "dlat": 0.01, "dlon": 0.01}


def test_key_name_matches_archive_layout():
    t = datetime(2024, 6, 1, 6, tzinfo=timezone.utc).timestamp()
    assert m.hour_key(t) == ("CONUS/MultiSensor_QPE_01H_Pass2_00.00/20240601/"
                             "MRMS_MultiSensor_QPE_01H_Pass2_00.00_20240601-060000.grib2.gz")


def test_circle_cell_count_matches_area():
    idx = m.circle_indices(30.2, -97.8, 10.0, GEOM)
    cell_km2 = (0.01 * 111.19) * (0.01 * 111.19 * math.cos(math.radians(30.2)))
    expected = math.pi * 100 / cell_km2
    assert abs(len(idx) - expected) / expected < 0.03
    # every selected cell centre is within 10 km
    j, i = idx // GEOM["ni"], idx % GEOM["ni"]
    lat, lon = GEOM["lat_first"] - j * 0.01, -99.995 + i * 0.01
    dy = (lat - 30.2) * 111.19
    dx = (lon + 97.8) * 111.19 * math.cos(math.radians(30.2))
    assert np.sqrt(dx ** 2 + dy ** 2).max() <= 10.05


def test_circle_mean_excludes_no_coverage():
    v = np.array([2.0, 4.0, -3.0, 9999.0, 0.0])
    mean, cov = m.circle_mean(v, np.arange(5))
    assert mean == 2.0 and cov == 0.6
    mean, cov = m.circle_mean(np.array([-3.0, -3.0]), np.arange(2))
    assert math.isnan(mean) and cov == 0.0


def test_hours_union_and_event_window():
    ev = [{"site_no": "A", "crossing_ts": 10 * 3600 + 1200}]
    w = m.event_windows(ev, 2, 1)
    assert w[0][0] == 8 * 3600 and w[0][1] == 12 * 3600
    hrs = m.hours_from_windows(w + [(9 * 3600, 14 * 3600, "dry A|x")])
    assert sorted(hrs) == [h * 3600 for h in range(8, 14)]
    assert hrs[9 * 3600].startswith("event") and hrs[13 * 3600].startswith("dry")


def test_dry_window_is_quiet_and_below_action():
    day = 86400.0
    t = np.arange(0, 400 * day, 900.0)
    v = np.full(len(t), 1.0)
    ct = 200 * day
    v[(t > ct - day) & (t < ct + day)] = 9.0          # a flood around the event
    ev = [{"site_no": "A", "crossing_ts": ct}]
    dry = m.matched_dry_windows(ev, {"A": (t, v)}, {"A": 5.0}, [ct], 48, 12, 0.0, float(t[-1]))
    assert len(dry) == 1
    s, e, why = dry[0]
    assert why.startswith("dry A|") and e - s == 61 * 3600
    assert abs(s - ct) > 30 * day
    lo, hi = np.searchsorted(t, s), np.searchsorted(t, e)
    assert v[lo:hi].max() < 5.0
