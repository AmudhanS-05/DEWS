from tools import check_windows as cw


def m(start, n=5, rev=False, site="1"):
    return {"site_no": site, "series": "s", "mode": "SIMULATION", "window_start_ts": start,
            "n_readings": n, "revision": rev}


def test_duplicates_revisions_zero_fill_and_gaps():
    ok = cw.summarise([m(0), m(3600), m(3600, rev=True), m(7200, n=0)])
    assert not ok["duplicates"] and ok["revisions"] == 1 and ok["zero_filled"] == 1 and ok["gaps"] == 0
    bad = cw.summarise([m(0), m(0), m(10800)])
    assert list(bad["duplicates"].values()) == [2] and bad["gaps"] == 1
