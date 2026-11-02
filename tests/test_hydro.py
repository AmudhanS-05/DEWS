"""Synthetic series (made up for testing the arithmetic only)."""
from common import hydro

H = 3600


def series(points):
    return [(h * H, v) for h, v in points]


def test_crossings_and_rise_time():
    s = series([(0, 1.0), (1, 1.2), (2, 2.0), (3, 3.5), (4, 4.1), (5, 3.0), (6, 2.0),
                (40, 1.5), (41, 4.5), (42, 2.0)])
    idx = hydro.find_crossings(s, threshold=4.0)
    assert [s[i][0] / H for i in idx] == [4, 41]
    # first event rises from the 0 h minimum (1.0) -> 4 h; second from 40 h (1.5) -> 1 h
    assert hydro.rise_time_h(s, idx[0]) == 4.0
    assert hydro.rise_time_h(s, idx[1]) == 1.0


def test_close_reentries_are_one_event():
    s = series([(0, 1), (1, 5), (2, 3), (3, 5), (4, 1)])
    assert len(hydro.find_crossings(s, 4.0, min_gap_h=24)) == 1
    assert len(hydro.find_crossings(s, 4.0, min_gap_h=1)) == 2


def test_peak_and_intervals():
    s = series([(0, 1), (0.25, 2), (0.5, 6), (0.75, 7), (3, 2)])
    i = hydro.find_crossings(s, 5.0)[0]
    assert hydro.event_peak(s, i) == (3, 7)
    assert hydro.median_interval_min(s) == 15.0
    assert hydro.largest_gap_h(s) == 2.25
