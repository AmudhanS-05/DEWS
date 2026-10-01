"""Time parsing helpers. USGS returns local times with an offset, e.g.
'2026-09-30T00:00:00.000-07:00'. Everything internal is converted to UTC epoch
seconds so windows and latency arithmetic never depend on a time zone."""
from datetime import datetime, timezone


def parse_ts(s: str) -> datetime:
    """Parse an ISO-8601 timestamp with offset into an aware datetime (UTC)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp has no UTC offset: {s!r}")
    return dt.astimezone(timezone.utc)


def to_epoch(s: str) -> float:
    return parse_ts(s).timestamp()


def epoch_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def now_epoch() -> float:
    return datetime.now(tz=timezone.utc).timestamp()
