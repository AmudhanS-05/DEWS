from datetime import date

from tools import fault_drill as fd


def test_next_range_starts_after_the_newest_replayed_window():
    assert fd.next_range(date(2019, 7, 5)) == ("2019-07-07", "2019-07-11")
    assert fd.next_range(None) == ("2019-06-01", "2019-06-05")
    assert fd.next_range(date(2019, 12, 30), days=3) == ("2020-01-01", "2020-01-03")


def test_lag_counts_from_the_committed_offset_or_the_start():
    assert fd.lag_of(90, 0, 100) == 10
    assert fd.lag_of(-1001, 40, 100) == 60          # no committed offset yet


def reading(ts, value=1.0, revision=False):
    return {"type": "reading", "site_no": "s", "parameter": "00065", "series": "a", "reading_ts": ts, "value": value, "revision": revision}


def test_republished_counts_only_real_repeats():
    before = [reading(1), reading(2)]
    assert fd.republished(before, before + [reading(3)]) == 0                       # a new reading
    assert fd.republished(before, before + [reading(2, 1.5, revision=True)]) == 0   # a flagged revision
    assert fd.republished(before, before + [{"type": "heartbeat", "site_no": "s"}]) == 0
    assert fd.republished(before, before + [reading(2), reading(2)]) == 2           # the same reading sent again


def ev(ts, node, a, b):
    return {"ts": ts, "node": node, "from": a, "to": b}


def test_console_changes_are_filtered_and_sorted():
    events = [ev(30, "Sink", "down", "ok"), ev(20, "Sink", "ok", "down"), ev(5, "Sink", "x", "y"), ev(25, "PG", "ok", "down")]
    assert [e["ts"] for e in fd.node_changes(events, "Sink", 10)] == [20, 30]


def test_failed_and_recovered():
    assert fd.failed_and_recovered([ev(1, "n", "ok", "down"), ev(2, "n", "down", "ok")])
    assert not fd.failed_and_recovered([ev(1, "n", "ok", "down")])                  # never came back
    assert not fd.failed_and_recovered([ev(1, "n", "ok", "idle"), ev(2, "n", "idle", "ok")])  # no failure at all
    assert not fd.failed_and_recovered([])
    assert fd.failed_and_recovered([ev(1, "b", "3 up", "2 up"), ev(2, "b", "2 up", "3 up")])
    assert not fd.failed_and_recovered([ev(1, "b", "3 up", "2 up")])


def test_every_runbook_scenario_exists():
    assert set(fd.SCENARIOS) == {"faust", "broker", "sink", "postgres", "notifier", "producer"}
