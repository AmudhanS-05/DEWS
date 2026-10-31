"""Human approval of advisories (C1.5): the only way anything reaches `alerts.approved`.

The console calls these functions when a person clicks Approve / Edit / Reject. Approving updates PostgreSQL and
publishes one record to `alerts.approved` in the same step: if the publish does not succeed the database change is
rolled back, so a row never says "approved" without the message having been queued for the notifier.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from common import config

PENDING = ("draft",)
# latency_trace row of the reading that raised an alert (same id as storage/sink.py trace_row); %s = alert_id
ALERT_TRACE_ID = ("(SELECT a.site_no || '|' || a.mode || '|' || floor((a.details->'trace'->>'reading_ts')::numeric)::bigint "
                  "FROM alerts a WHERE a.alert_id = %s)")


class ApprovalError(Exception):
    pass


def list_advisories(conn, limit: int = 30) -> list[dict]:
    """Newest advisory per alert (alert_overview + current_advisory), pending ones first."""
    with conn.cursor() as cur:
        cur.execute("""SELECT o.alert_id, o.site_no, o.mode, o.category, o.alert_status, o.raised_at,
                              c.advisory_id, c.version, c.source, c.confidence, c.status, c.text, c.edited_text,
                              c.reviewer_verdict, c.approved_by, c.approved_at, c.delivered_at
                       FROM alert_overview o JOIN current_advisory c USING (alert_id)
                       ORDER BY (c.status = 'draft') DESC, o.raised_at DESC LIMIT %s""", (limit,))
        cols = [d[0] for d in cur.description]
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, r)} for r in cur.fetchall()]


def _get(cur, advisory_id: str) -> dict:
    cur.execute("SELECT alert_id, version, text, edited_text, status FROM advisories WHERE advisory_id = %s FOR UPDATE",
                (advisory_id,))
    r = cur.fetchone()
    if not r:
        raise ApprovalError(f"unknown advisory {advisory_id}")
    return dict(zip(("alert_id", "version", "text", "edited_text", "status"), r))


def edit(conn, advisory_id: str, new_text: str) -> None:
    if not new_text.strip():
        raise ApprovalError("edited text is empty")
    with conn, conn.cursor() as cur:
        a = _get(cur, advisory_id)
        if a["status"] not in PENDING:
            raise ApprovalError(f"advisory is {a['status']}, only a draft can be edited")
        cur.execute("UPDATE advisories SET edited_text = %s WHERE advisory_id = %s", (new_text, advisory_id))


def reject(conn, advisory_id: str, by: str) -> None:
    with conn, conn.cursor() as cur:
        a = _get(cur, advisory_id)
        if a["status"] not in PENDING:
            raise ApprovalError(f"advisory is {a['status']}, only a draft can be rejected")
        cur.execute("UPDATE advisories SET status = 'rejected', approved_by = %s, approved_at = now() WHERE advisory_id = %s",
                    (by, advisory_id))
        cur.execute("UPDATE alerts SET status = 'rejected' WHERE alert_id = %s AND status = 'draft'", (a["alert_id"],))


def approve(conn, producer, advisory_id: str, by: str, text: str | None = None) -> dict:
    """Approve (optionally with replacement text), publish to alerts.approved, return the published record."""
    if not by.strip():
        raise ApprovalError("approver name is required")
    with conn, conn.cursor() as cur:
        a = _get(cur, advisory_id)
        if a["status"] not in PENDING:
            raise ApprovalError(f"advisory is {a['status']}, only a draft can be approved")
        final = (text if text is not None else a["edited_text"]) or a["text"]
        now = datetime.now(timezone.utc)
        cur.execute("""UPDATE advisories SET status = 'approved', approved_by = %s, approved_at = %s,
                       edited_text = CASE WHEN %s <> text THEN %s ELSE edited_text END WHERE advisory_id = %s""",
                    (by, now, final, final, advisory_id))
        # other drafts of the same alert are now moot
        cur.execute("UPDATE advisories SET status = 'superseded' WHERE alert_id = %s AND advisory_id <> %s AND status = 'draft'",
                    (a["alert_id"], advisory_id))
        cur.execute("UPDATE alerts SET status = 'approved', approved_at = %s, approved_by = %s WHERE alert_id = %s "
                    "RETURNING mode", (now, by, a["alert_id"]))
        row = cur.fetchone()
        if not row:
            raise ApprovalError(f"no alert row for {a['alert_id']}")
        cur.execute(f"UPDATE latency_trace SET approved_time = %s WHERE trace_id = {ALERT_TRACE_ID}", (now, a["alert_id"]))
        rec = {"alert_id": a["alert_id"], "advisory_id": advisory_id, "version": a["version"], "text": final,
               "approved_by": by, "approved_at": now.isoformat(), "mode": row[0]}
        producer.send(config.TOPIC_APPROVED, a["alert_id"].split("|")[0], rec)
        if producer.flush() > 0 or getattr(producer, "failed", 0):
            raise ApprovalError("could not queue the approved advisory on Kafka; nothing was approved")
    return rec


def as_json(obj) -> str:
    return json.dumps(obj, default=str)
