"""C2.2 rain join (streams/windowing.py on_rain / attach_rain / on_record): event-time alignment, sums only when every
hour is known, late rain never re-emits a window, live and replay never mix, rain heartbeats never close windows."""
from tools.check_windows import summarise
from streams import windowing as wd

W = 3600
T0 = 1_790_000_000 - (1_790_000_000 % W)
GRACE = 5400.0


def rd(ts, v, mode="SIMULATION"):
    return {"type": "reading", "site_no": "S1", "series": "1", "reading_ts": ts, "value": v, "qualifiers": ["P"],
            "mode": mode}


def rain(hour_end, mm, mode="SIMULATION"):
    return {"type": "rain", "site_no": "S1", "hour_end_ts": hour_end, "rain_mm_10km": mm, "rain_mm": mm,
            "product": "MultiSensor_QPE_01H_Pass2_00.00", "mode": mode}


def feed(msgs, mode="SIMULATION", state=None):
    st, out, dlq = state if state is not None else {}, [], []
    for m in msgs:
        st, em, d = wd.on_record(st, m, mode, 0.0, W, GRACE)
        out += em
        dlq += d
    return st, out, dlq


def test_window_gets_rain_of_the_hours_ending_at_its_end():
    # rain for the hours ending T0+1h .. T0+6h, then readings for 6 windows; the 7th window's reading closes window 6
    msgs = [rain(T0 + k * W, float(k)) for k in range(-23, 7)]
    msgs += [rd(T0 + k * W + 60, 1.0 + k) for k in range(7)]
    _, out, dlq = feed(msgs)
    assert dlq == [] and len(out) == 6
    w0 = out[0]                                         # window [T0, T0+1h): rain of the hour ending T0+1h = 1 mm
    assert w0["window_start_ts"] == T0 and w0["rain_1h"] == 1.0
    assert w0["rain_3h"] == 1.0 + 0.0 - 1.0             # hours ending T0+1h, T0, T0-1h
    assert w0["rain_6h"] == sum(range(-4, 2))
    assert w0["rain_24h"] == sum(range(-22, 2))
    assert out[5]["rain_1h"] == 6.0 and out[5]["rain_3h"] == 15.0
    assert w0["rain_product"] == "MultiSensor_QPE_01H_Pass2_00.00"


def test_sum_is_none_unless_every_hour_is_known():
    msgs = [rain(T0 + W, 2.0), rain(T0, 1.0)]           # only 2 of 3 hours; T0-1h missing
    msgs += [rd(T0 + 60, 1.0), rd(T0 + W + 60, 1.0)]
    _, out, _ = feed(msgs)
    assert out[0]["rain_1h"] == 2.0 and out[0]["rain_3h"] is None and out[0]["rain_24h"] is None
    _, out, _ = feed([rain(T0 + W, None), rd(T0 + 60, 1.0), rd(T0 + W + 60, 1.0)])   # no radar coverage
    assert out[0]["rain_1h"] is None                    # unknown is never 0


def test_window_without_any_rain_has_null_rain_fields():
    _, out, _ = feed([rd(T0 + 60, 1.0), rd(T0 + W + 60, 1.0)])
    assert {k: out[0][k] for k in ("rain_1h", "rain_3h", "rain_6h", "rain_24h")} == dict.fromkeys(
        ("rain_1h", "rain_3h", "rain_6h", "rain_24h"))


def test_late_rain_does_not_re_emit_and_counts_for_later_windows():
    st, out, _ = feed([rd(T0 + 60, 1.0), rd(T0 + W + 60, 1.0)])          # window T0 emitted, no rain yet
    assert len(out) == 1 and out[0]["rain_1h"] is None
    st, out2, _ = feed([rain(T0 + W, 5.0)], state=st)                    # rain for that hour arrives late
    assert out2 == [] and st[wd.RAIN_KEY]["late"] == 1
    st, out3, _ = feed([rain(T0 + 2 * W, 1.0), rain(T0 + 3 * W, 1.0), rd(T0 + 2 * W + 60, 1.0)], state=st)
    assert [a["window_start_ts"] for a in out3] == [T0 + W]
    assert out3[0]["rain_1h"] == 1.0                    # hour ending T0+2h
    st, out4, _ = feed([rd(T0 + 3 * W + 60, 1.0)], state=st)
    assert out4[0]["rain_3h"] == 7.0                    # 5 (late) + 1 + 1: late rain still counts later
    r = summarise(out + out3 + out4)
    assert r["duplicates"] == {} and r["revisions"] == 0


def test_mode_mismatch_goes_to_deadletter_and_is_not_stored():
    st, out, dlq = feed([rain(T0 + W, 9.0, mode="LIVE")], mode="SIMULATION")
    assert out == [] and dlq[0]["reason"].startswith("mode_mismatch") and wd.RAIN_KEY not in st
    st, _, dlq = feed([rain(T0 + W, 9.0, mode="SIMULATION")], mode="LIVE")
    assert dlq and wd.RAIN_KEY not in st


def test_rain_heartbeat_never_closes_windows_gauge_heartbeat_does():
    st, out, _ = feed([rd(T0 + 60, 1.0, mode="LIVE")], mode="LIVE")
    hb_rain = {"type": "heartbeat", "site_no": "S1", "now_ts": T0 + 10 * W, "mode": "LIVE",
               "product": "RadarOnly_QPE_01H_00.00", "last_hour_end_ts": T0}
    st, out, _ = feed([hb_rain], mode="LIVE", state=st)
    assert out == []
    st, out, _ = feed([{"type": "heartbeat", "site_no": "S1", "now_ts": T0 + W + GRACE, "mode": "LIVE"}],
                      mode="LIVE", state=st)
    assert len(out) == 1 and "rain_1h" in out[0]


def test_rain_state_is_pruned_and_tick_skips_it():
    msgs = [rain(T0 + k * W, 0.1) for k in range(-100, 1)] + [rd(T0 + 60, 1.0, mode="LIVE")]
    msgs = [dict(m, mode="LIVE") for m in msgs]
    st, _, _ = feed(msgs + [rain(T0 + W, 0.2, mode="LIVE")], mode="LIVE")
    hrs = sorted(float(k) for k in st[wd.RAIN_KEY]["hours"])
    assert hrs[0] >= T0 - wd.RAIN_KEEP_H * 3600 and hrs[-1] == T0 + W
    st, out = wd.on_tick("S1", st, T0 + 2 * W + GRACE, W, GRACE)        # "_rain" is not a series
    assert all(a["series"] == "1" for a in out)


def test_on_reading_unchanged_for_ml_backtest():
    st, em, _ = wd.on_reading({}, rd(T0 + 60, 1.0), W, "SIMULATION")
    st, em, _ = wd.on_reading(st, rd(T0 + W + 60, 1.0), W, "SIMULATION")
    assert "rain_1h" not in em[0]                       # ml.backtest output keeps its old shape
