"""Producer-side dedupe and the message envelope (pure, unit-tested).

The live producer polls an overlapping window (last 6 h) every few minutes, so
most readings are seen many times. ReadingTracker decides, per reading:
  new       -> publish
  revision  -> same key, different value or qualifiers (provisional data revised)
               -> publish with revision = true
  duplicate -> skip
"""
from __future__ import annotations

import json

from common.timeutil import epoch_to_iso
from common.usgs import reading_key


class ReadingTracker:
    def __init__(self, retain_h: float = 72.0):
        self.retain_s = retain_h * 3600
        self.seen: dict[str, list] = {}      # key -> [value, qualifiers, reading_ts]

    def classify(self, r: dict) -> str:
        k = reading_key(r)
        sig = [r["value"], sorted(r.get("qualifiers") or []), r["reading_ts"]]
        prev = self.seen.get(k)
        self.seen[k] = sig
        if prev is None:
            return "new"
        if prev[0] != sig[0] or prev[1] != sig[1]:
            return "revision"
        return "duplicate"

    def prune(self, now_ts: float) -> int:
        old = [k for k, s in self.seen.items() if now_ts - s[2] > self.retain_s]
        for k in old:
            del self.seen[k]
        return len(old)

    def newest_ts(self) -> float | None:
        return max((s[2] for s in self.seen.values()), default=None)

    def dumps(self) -> str:
        return json.dumps(self.seen)

    @classmethod
    def loads(cls, s: str, retain_h: float = 72.0) -> "ReadingTracker":
        t = cls(retain_h)
        t.seen = json.loads(s)
        return t


def make_envelope(r: dict, fetched_at: float, mode: str, revision: bool = False, **extra) -> dict:
    env = {
        "type": "reading",
        "site_no": r["site_no"], "site_name": r.get("site_name"),
        "parameter": r["parameter"], "series": r["series"], "method": r.get("method", ""),
        "value": r["value"], "unit": r.get("unit"), "qualifiers": r.get("qualifiers") or [],
        "reading_time": r["reading_time"], "reading_ts": r["reading_ts"],
        "fetched_at": epoch_to_iso(fetched_at), "fetched_ts": fetched_at,
        "mode": mode, "revision": revision,
    }
    env.update(extra)
    return env


def make_heartbeat(site_no: str, now_ts: float, mode: str = "LIVE") -> dict:
    """Wall-clock tick per site. The Faust app uses it to zero-fill silent windows,
    and a gauge with heartbeats but no readings is visibly 'silent', not 'fine'."""
    return {"type": "heartbeat", "site_no": site_no, "now_ts": now_ts,
            "now": epoch_to_iso(now_ts), "mode": mode}
