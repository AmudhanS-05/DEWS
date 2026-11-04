import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alerting import approval, bulletin, notifier, template
from common import config
from storage import sink

ROOT = Path(__file__).resolve().parent.parent
LEVELS = {"action": 5.5, "minor": 5.5, "moderate": 7.0, "major": 13.0}
ALERT = {"alert_id": "08158700|minor|2019-05-04T03:00:00Z", "kind": "new", "site_no": "08158700",
         "site_name": "Onion Creek near Driftwood", "mode": "SIMULATION", "category": "minor", "probability": None,
         "horizon_h": 3, "forecast_stage": 6.8, "current_stage": 4.7, "official_levels": LEVELS,
         "provisional_data": True, "model": "rise_rate", "raised_at": "2026-10-31T10:00:00Z"}


# ---- template (pure) ---------------------------------------------------------------------------------------------

def test_v1_says_simulation_numbers_and_missing_probability():
    t = template.render_v1(ALERT)
    assert t.startswith("SIMULATION:")
    assert "MINOR flood stage (5.5 ft) within 3 h" in t and "Gauge now: 4.7 ft." in t
    assert "no probability from this model" in t and "%" not in t           # never invented
    assert "Official stages: action 5.5, minor 5.5, moderate 7.0, major 13.0 ft." in t
    assert "provisional" in t and "not sent until approved" in t


def test_v1_with_probability_upgrade_and_already_in_category():
    t = template.render_v1(dict(ALERT, probability=0.79, kind="upgrade", category="major", current_stage=8.2, mode="LIVE"))
    assert t.startswith("UPGRADE: ") and "79%" in t and "(already at MODERATE)" in t and "SIMULATION" not in t


# ---- sink routing of alerts ---------------------------------------------------------------------------------------

def test_sink_creates_v1_draft_for_new_alert_and_supersedes_on_retract():
    routed = dict()
    for table, row in sink.route(config.TOPIC_ALERTS, ALERT):
        routed[table] = row
    assert routed["advisories"][0] == ALERT["alert_id"] + "|1" and routed["advisories"][3] == "template"
    assert routed["advisories"][4] == template.render_v1(ALERT)
    r = dict(sink.route(config.TOPIC_ALERTS, dict(ALERT, kind="retract")))
    assert "advisories" not in r and r["advisory_retract"] == (ALERT["alert_id"],)


# ---- notifier (no database) ---------------------------------------------------------------------------------------

REC = {"alert_id": ALERT["alert_id"], "advisory_id": ALERT["alert_id"] + "|1", "version": 1, "text": "Flood text  ok",
       "approved_by": "A. Person", "approved_at": "2026-10-31T10:05:00+00:00", "mode": "SIMULATION"}


DETAILS = {"kind": "new", "site_name": "Onion Creek near Driftwood", "current_stage": 4.7, "forecast_stage": 6.8123456, "model": "rise_rate",
           "official_levels": LEVELS}
ALERT_ROW = {"site_no": "08158700", "mode": "SIMULATION", "category": "minor", "probability": 0.79, "horizon_h": 3,
             "provisional_data": True, "details": DETAILS}


def test_message_is_a_tagged_bulletin_with_plain_and_html_parts():
    m = notifier.build_message(dict(REC, version=8, text="Advice 42.260909090909095 ft."), ALERT_ROW, "Onion Creek", "me@example.org", ["to@example.org"])
    assert m["Subject"] == "[SIMULATION] Flood forecast: Onion Creek near Driftwood (MINOR category)"
    plain, html_part = m.get_body(("plain",)).get_content(), m.get_body(("html",)).get_content()
    assert "Flood forecast for Onion Creek" in plain and "Notes" in plain and "A. Person" in plain and "SIMULATION: replayed" in plain
    assert "replayed historical data" in html_part and "MINOR flood category" in html_part and "prefers-color-scheme: dark" in html_part


def test_bulletin_formats_numbers_and_rounds_long_decimals_in_the_advice():
    f = bulletin.facts(dict(REC, version=8, text="Forecast 42.260909090909095 ft, 79% in 6 h"), ALERT_ROW, None)
    t = bulletin.render_text(f)
    assert "42.3 ft" in t and "42.260909" not in t and "79 %" in t
    assert "Forecast in 3 h:  " in t and "Latest reading:   4.7 ft" in t
    assert "action 5.5 ft, minor 5.5 ft, moderate 7.0 ft, major 13.0 ft (NWS)" in t


MODEL_DETAILS = {"kind": "new", "site_name": "Llano River at Llano", "site_no": "08151500", "category": "minor", "current_stage": 8.38, "forecast_stage": 5.42, "model": "s3_v0", "horizon_h": 3.0,
                 "official_levels": {"action": 10.0, "minor": 10.0, "moderate": 12.0, "major": 23.0}, "alert_rule": "alert_by_model", "tau": 0.827, "probability": 0.895}
MODEL_ROW = {"site_no": "08151500", "mode": "SIMULATION", "category": "minor", "probability": 0.895, "horizon_h": 3, "provisional_data": False, "details": MODEL_DETAILS}
MODEL_REC = dict(REC, alert_id="08151500|minor|2023-10-26T07:00:00Z", advisory_id="08151500|minor|2023-10-26T07:00:00Z|1")


def test_model_alert_bulletin_leads_with_the_probability_and_never_says_forecast_to_reach():
    f = bulletin.facts(MODEL_REC, MODEL_ROW, None)
    text, html = bulletin.render_text(f), bulletin.render_html(f)
    for out in (text, html):
        assert "probability of crossing MINOR flood stage (10.0 ft) within 3 h: 90%" in out and "forecast to reach" not in out.lower()
        assert "classifier head" in out and "regression head" in out and "5.4 ft" in out and "UNCERTAIN" not in out


def test_model_alert_with_the_stage_forecast_at_the_category_has_no_uncertain_sentence():
    row = dict(MODEL_ROW, details=dict(MODEL_DETAILS, forecast_stage=10.5))
    text = bulletin.render_text(bulletin.facts(MODEL_REC, row, None))
    assert "probability of crossing MINOR" in text and "regression head" not in text


def test_rise_rate_alert_keeps_the_stage_forecast_headline():
    assert "forecast to reach the MINOR" in bulletin.render_text(bulletin.facts(REC, ALERT_ROW, None)).replace("The stage is forecast", "forecast")


def test_v1_draft_under_the_model_rule_uses_the_same_wording():
    t = template.render_v1({**MODEL_DETAILS, "mode": "SIMULATION", "alert_id": "x"})
    assert "probability of crossing MINOR" in t and "forecast to reach" not in t and "regression head" in t and "83%" in t


def test_advisories_carry_the_time_they_arrived_newest_first(conn):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO alerts (alert_id, site_no, mode, category, raised_at) VALUES ('new|minor|2019-05-05T00:00:00Z','new','SIMULATION','minor',now())")
        cur.execute("INSERT INTO advisories (advisory_id, alert_id, version, source, text, created_at) VALUES ('new|minor|2019-05-05T00:00:00Z|1','new|minor|2019-05-05T00:00:00Z',1,'template','x', now() + interval '1 hour')")
    out = approval.list_advisories(conn)
    assert out[0]["advisory_id"].startswith("new|") and out[0]["created_at"] and out[1]["created_at"]


def test_v1_template_text_is_not_repeated_in_the_bulletin():
    f = bulletin.facts(dict(REC, version=1, text="TEMPLATE TEXT"), ALERT_ROW, None)
    assert "TEMPLATE TEXT" not in bulletin.render_text(f) and "Approved advisory" not in bulletin.render_text(f)


def test_missing_facts_are_said_to_be_missing_not_invented():
    f = bulletin.facts(dict(REC, version=8), {"category": "minor", "probability": None, "horizon_h": None, "details": None}, "Creek")
    t = bulletin.render_text(f)
    assert "not available" in t and "Probability:      not given" in t and "%" not in t.replace("79", "")


def test_already_in_category_is_flagged():
    f = bulletin.facts(REC, dict(ALERT_ROW, details=dict(DETAILS, current_stage=8.0)), None)
    assert any("already in the MODERATE category" in n for n in bulletin.notes(f))


def test_retry_succeeds_on_third_attempt_and_gives_up_after_the_limit():
    calls = []

    def flaky(msg, cfg):
        calls.append(1)
        if len(calls) < 3:
            raise OSError("smtp down")

    waits = []
    assert notifier.send_with_retry(flaky, None, {}, attempts=3, backoff_s=2, sleep=waits.append) == (True, None)
    assert len(calls) == 3 and waits == [2, 4]
    ok, err = notifier.send_with_retry(lambda m, c: (_ for _ in ()).throw(OSError("nope")), None, {}, attempts=2,
                                       backoff_s=0, sleep=lambda s: None)
    assert ok is False and "OSError: nope" in err


def test_missing_smtp_settings_are_named_not_printed():
    with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
        notifier.smtp_settings({"NOTIFY_TO": "a@b", "SMTP_HOST": "h", "SMTP_USER": "u"})


# ---- approval and delivery against PostgreSQL ---------------------------------------------------------------------

class FakeProducer:
    def __init__(self, fail=False):
        self.sent, self.fail, self.failed = [], fail, 0

    def send(self, topic, key, value):
        self.sent.append((topic, key, value))

    def flush(self, timeout=0):
        return 1 if self.fail else 0


@pytest.fixture()
def conn():
    dsn = os.getenv("AGENTS_TEST_DSN")
    if not dsn:
        pytest.skip("AGENTS_TEST_DSN not set: PostgreSQL tests skipped")
    import psycopg2
    schema = "alert_t_" + uuid.uuid4().hex[:8]
    c = psycopg2.connect(dsn, connect_timeout=5)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        for f in ("schema.sql", "views.sql"):
            cur.execute((ROOT / "storage" / f).read_text(encoding="utf-8"))
    c.autocommit = False
    # put the alert and its v1 draft in through the sink's own write path
    rows = {}
    for table, row in sink.route(config.TOPIC_ALERTS, ALERT):
        rows.setdefault(table, []).append(row)
    sink.write_batch(c, rows)
    yield c
    c.rollback()
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()


ADV = ALERT["alert_id"] + "|1"
FC_LATER = {"site_no": "08158700", "mode": "SIMULATION", "window_start": "2019-05-04T03:00:00Z", "model": "rise_rate",
            "horizon_h": 3, "forecast_stage": 6.8, "made_at": "2026-01-01T10:00:03Z"}


def status(conn, table="advisories", key="advisory_id", val=ADV):
    with conn.cursor() as cur:
        cur.execute(f"SELECT status FROM {table} WHERE {key} = %s", (val,))
        return cur.fetchone()[0]


def test_v1_draft_is_listed_pending(conn):
    (a,) = approval.list_advisories(conn)
    assert a["status"] == "draft" and a["version"] == 1 and a["source"] == "template"


def test_approve_publishes_exactly_the_edited_text_and_marks_rows(conn):
    prod = FakeProducer()
    approval.edit(conn, ADV, "Edited by a person.")
    rec = approval.approve(conn, prod, ADV, "A. Person")
    assert len(prod.sent) == 1 and prod.sent[0][0] == config.TOPIC_APPROVED
    assert prod.sent[0][2]["text"] == "Edited by a person." and rec["mode"] == "SIMULATION"
    assert status(conn) == "approved" and status(conn, "alerts", "alert_id", ALERT["alert_id"]) == "approved"
    with pytest.raises(approval.ApprovalError):          # cannot approve twice
        approval.approve(conn, prod, ADV, "A. Person")
    assert len(prod.sent) == 1


def test_human_and_delivery_times_land_on_the_alerts_own_trace(conn):
    tr = {"reading_ts": 1556938800.0, "fetched_ts": 1767261600.0, "ingest_ts": 1767261601.0,
          "window_close_time": 1767261602.0, "forecast_time": "2026-01-01T10:00:03Z"}
    alert = dict(ALERT, alert_id="08158700|major|e", trace=tr)
    # a newer forecast of the same gauge must not take the alert's times
    later = dict(FC_LATER, window_start="2019-05-04T20:00:00Z", trace=dict(tr, reading_ts=1556999999.0))
    rows = {}
    for topic, m in ((config.TOPIC_FORECASTS, FC_LATER | {"trace": tr}), (config.TOPIC_FORECASTS, later), (config.TOPIC_ALERTS, alert)):
        for table, row in sink.route(topic, m):
            rows.setdefault(table, []).append(row)
    sink.write_batch(conn, rows)
    rec = approval.approve(conn, FakeProducer(), alert["alert_id"] + "|1", "A. Person")
    notifier.mark_delivered(conn, rec, datetime.now(timezone.utc))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT trace_id, human_s > 0, delivery_s >= 0, alert_s > 0 FROM latency_waterfall "
                    "WHERE human_s IS NOT NULL OR delivery_s IS NOT NULL")
        assert cur.fetchall() == [("08158700|SIMULATION|1556938800", True, True, True)]


def test_a_rerun_of_the_forecast_keeps_the_first_forecast_time(conn):
    tr = {"reading_ts": 1556938800.0, "fetched_ts": 1793400000.0, "ingest_ts": 1793400001.0, "window_close_time": 1793400002.0}
    for made in ("2026-10-31T10:00:03Z", "2026-10-31T15:00:00Z"):
        sink.write_batch(conn, {"latency_trace": [sink.trace_row(dict(FC_LATER, trace=tr, made_at=made))]})
    with conn.cursor() as cur:
        cur.execute("SELECT forecast_time = '2026-10-31T10:00:03Z' FROM latency_trace")
        assert cur.fetchone() == (True,)


def test_reject_sends_nothing(conn):
    prod = FakeProducer()
    approval.reject(conn, ADV, "A. Person")
    assert status(conn) == "rejected" and prod.sent == []
    with pytest.raises(approval.ApprovalError):
        approval.approve(conn, prod, ADV, "A. Person")
    assert prod.sent == []


def test_failed_publish_rolls_the_approval_back(conn):
    with pytest.raises(approval.ApprovalError):
        approval.approve(conn, FakeProducer(fail=True), ADV, "A. Person")
    assert status(conn) == "draft"


def test_approver_name_required(conn):
    with pytest.raises(approval.ApprovalError):
        approval.approve(conn, FakeProducer(), ADV, "  ")


def test_delivery_marks_sent_and_failure_leaves_approved_and_dead_letters(conn):
    prod = FakeProducer()
    rec = approval.approve(conn, prod, ADV, "A. Person")
    cfg = {"user": "me@example.org", "to": ["to@example.org"], "host": "h", "port": 587, "password": "x"}
    sent = []
    assert notifier.handle(rec, conn, cfg, dry_run=True, send=lambda m, c: sent.append(m)) == "dry-run" and sent == []
    assert status(conn) == "approved"
    dead = []
    bad = lambda m, c: (_ for _ in ()).throw(OSError("smtp down"))          # noqa: E731
    assert notifier.handle(rec, conn, cfg, False, send=bad, sleep=lambda s: None, dlq=dead.append) == "failed"
    assert status(conn) == "approved" and len(dead) == 1
    assert notifier.handle(rec, conn, cfg, False, send=lambda m, c: sent.append(m), sleep=lambda s: None) == "sent"
    assert len(sent) == 1 and status(conn) == "sent"
    with conn.cursor() as cur:
        cur.execute("SELECT delivered_at, channel FROM alerts WHERE alert_id = %s", (ALERT["alert_id"],))
        d, ch = cur.fetchone()
    assert d is not None and ch == "email"


def test_retract_supersedes_unapproved_draft(conn):
    rows = {}
    for table, row in sink.route(config.TOPIC_ALERTS, dict(ALERT, kind="retract")):
        rows.setdefault(table, []).append(row)
    sink.write_batch(conn, rows)
    assert status(conn) == "superseded" and status(conn, "alerts", "alert_id", ALERT["alert_id"]) == "retracted"
