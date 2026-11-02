"""Window logic on synthetic readings (values made up; tests the mechanics only)."""
from streams import windowing as wd

W = 3600
T0 = 1_790_000_000 - (1_790_000_000 % W)   # aligned window start


def rd(ts, v, series="1", q=("P",)):
    return {"site_no": "S1", "series": series, "reading_ts": ts, "value": v, "qualifiers": list(q)}


def feed(state, readings, mode="LIVE"):
    out, dropped = [], 0
    for r in readings:
        state, em, d = wd.on_reading(state, r, W, mode)
        out += em
        dropped += d
    return state, out, dropped


def test_window_closes_on_next_window_event():
    st, out, _ = feed({}, [rd(T0 + 0, 1.0), rd(T0 + 900, 1.2), rd(T0 + 1800, 1.6), rd(T0 + W + 60, 2.0)])
    assert len(out) == 1
    a = out[0]
    assert a["n_readings"] == 3 and a["stage_last"] == 1.6 and a["stage_max"] == 1.6
    assert abs(a["rise_rate_ft_per_h"] - (0.6 / 0.5)) < 1e-9   # 0.6 ft over 30 min
    assert a["provisional"] is True and a["revision"] is False


def test_gap_is_zero_filled_with_carried_stage():
    st, out, _ = feed({}, [rd(T0, 1.0), rd(T0 + 3 * W, 1.5)])
    assert [a["n_readings"] for a in out] == [1, 0, 0]
    assert out[1]["stage_last"] == 1.0 and out[1]["carried_forward"] is True


def test_duplicate_reading_ignored_and_revision_replaces():
    st, out, _ = feed({}, [rd(T0, 1.0), rd(T0, 1.0), rd(T0 + 900, 1.1), rd(T0 + 900, 1.3)])
    assert [x[:3] for x in st["1"]["cur"]] == [[T0, 1.0, True], [T0 + 900, 1.3, True]]


def test_window_carries_the_trace_of_its_last_reading():
    a, b = dict(rd(T0, 1.0), fetched_ts=T0 + 5, ingest_ts=T0 + 6), dict(rd(T0 + 900, 1.2), fetched_ts=T0 + 905, ingest_ts=T0 + 906)
    _, out, _ = feed({}, [a, b, dict(b, fetched_ts=T0 + 999), rd(T0 + W, 2.0)])   # the duplicate keeps its first times
    assert (out[0]["last_reading_ts"], out[0]["fetched_ts"], out[0]["ingest_ts"]) == (T0 + 900, T0 + 905, T0 + 906)


def test_state_saved_before_the_trace_fields_still_closes_and_zero_fill_has_no_trace():
    st = {"1": dict(wd.new_series_state("LIVE"), cur_start=T0, cur=[[T0, 1.0, True]])}
    _, out, _ = feed(st, [rd(T0 + 2 * W, 2.0)])
    assert out[0]["n_readings"] == 1 and out[0]["fetched_ts"] is None and out[0]["ingest_ts"] is None
    assert out[1]["n_readings"] == 0 and out[1]["ingest_ts"] is None


def test_rain_sent_well_before_the_gauge_readings_is_held_by_event_time():
    st = {}
    for k in range(1, 7):
        st, _ = wd.on_rain(st, {"hour_end_ts": T0 + k * W, "rain_mm_10km": 2.0, "product": "p"}, W, "SIMULATION")
    rec = {"type": "reading", "site_no": "S1", "series": "1", "value": 1.0, "qualifiers": [], "mode": "SIMULATION"}
    for k in range(0, 3):
        st, out, _ = wd.on_record(st, {**rec, "reading_ts": T0 + k * W + 60}, "SIMULATION", T0, W, 5400)
    assert out and out[-1]["rain_1h"] == 2.0 and st["_rain"]["late"] == 0


def test_late_reading_reemits_closed_window_as_revision():
    st, out, _ = feed({}, [rd(T0, 1.0), rd(T0 + W, 2.0), rd(T0 + 1200, 1.4)])
    assert out[-1]["revision"] is True and out[-1]["n_readings"] == 2 and out[-1]["stage_last"] == 1.4


def test_too_late_is_dropped():
    st, out, dropped = feed({}, [rd(T0 + 10 * W, 1.0), rd(T0, 5.0)])
    assert dropped == 1


def test_series_kept_separate():
    st, out, _ = feed({}, [rd(T0, 1.0, "11"), rd(T0, 1.76, "12"), rd(T0 + W, 1.1, "11")])
    assert set(st) == {"11", "12"} and len(out) == 1 and out[0]["series"] == "11"


def test_tick_closes_windows_in_live_mode_only():
    st, _, _ = feed({}, [rd(T0, 1.0)])
    st, em = wd.on_tick("S1", st, now_ts=T0 + 2 * W + 1800, w=W, grace_s=1800)
    assert [a["n_readings"] for a in em] == [1, 0]
    sim, _, _ = feed({}, [rd(T0, 1.0)], mode="SIMULATION")
    sim, em2 = wd.on_tick("S1", sim, now_ts=T0 + 10 * W, w=W, grace_s=0)
    assert em2 == []


def test_closed_windows_are_bounded():
    st, out, _ = feed({}, [rd(T0 + k * W, 1.0) for k in range(10)])
    assert len(st["1"]["closed"]) == 3
