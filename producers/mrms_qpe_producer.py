"""Rain producer (C2.1): NOAA MRMS hourly QPE -> mean depth over circles around each gauge -> Kafka.

Live (topic rain.mrms, mode LIVE): every --every-s seconds look for the next hourly file of the live product in the
public noaa-mrms-pds bucket, decode it in memory (eccodes, nothing written to disk), compute the mean depth over the
5, 10 and 25 km circles around every gauge in config/gauge_locations.csv and publish one record per gauge-hour, keyed
by site_no. Then one heartbeat per gauge, so a stalled rain feed is visible downstream. State (last hour published)
is saved after every hour, so a restart neither skips nor re-publishes; after downtime up to --backfill-h hours are
filled in, oldest first.

Live product (default radaronly): measured 31 Oct 2026, RadarOnly_QPE_01H at HH:00 appears ~3 min after its hour,
MultiSensor Pass 1 ~15.5 min, Pass 2 ~57 min. Radar-only is chosen for lead time (rain is the leading signal); it is
not gauge-corrected, while the ML model is trained on Pass 2 (state this with any live result). `--product pass1`
switches to the gauge-corrected hourly product.

Replay (topic replay.rain, mode SIMULATION): `--replay --start --end` publishes the historical Pass 2 hours, paced
like replay_producer (--speed simulated seconds per real second, anchored at --start so a gauge replay started at
the same moment with the same --start and --speed runs in step; --no-sleep = as fast as downloads allow).
The archive starts 2020-10-14: earlier hours (e.g. the May 2019 demo flood) have no radar rain and are skipped.

    python -m producers.mrms_qpe_producer --probe                         # newest hour + latency per product
    python -m producers.mrms_qpe_producer --once                          # one live poll, then exit
    python -m producers.mrms_qpe_producer                                 # live, every 120 s
    python -m producers.mrms_qpe_producer --replay --start 2024-05-31T18 --end 2024-06-01T06 --sites 08158700
    python -m producers.mrms_qpe_producer --replay --start 2024-05-28 --end 2024-06-02 --speed 3600

Record fields: docs/CONTRACTS.md (rain.mrms, replay.rain).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from common import config, mrms
from common.progress import Task
from common.timeutil import epoch_to_iso, now_epoch, to_epoch

STATE_FILE = os.path.join(config.STATE_DIR, "mrms_producer_state.json")
GAUGES_CSV = "config/gauge_locations.csv"
RADII = (5.0, 10.0, 25.0)
MAIN_RADIUS = 10.0              # decision D3: 10 km is the radius the pipeline and the windows use


# ---------- pure helpers (tested) ----------

def load_gauges(path: str = GAUGES_CSV, sites: str | None = None) -> list[dict]:
    want = {s.strip() for s in sites.split(",") if s.strip()} if sites else None
    with open(path, encoding="utf-8") as f:
        gs = [{"site_no": r["site_no"].strip(), "lid": r["lid"], "lat": float(r["lat"]), "lon": float(r["lon"])}
              for r in csv.DictReader(f)]
    if want:
        gs = [g for g in gs if g["site_no"] in want or g["lid"] in want]
        missing = want - {g["site_no"] for g in gs} - {g["lid"] for g in gs}
        if missing:
            raise SystemExit(f"not in {path}: {sorted(missing)}")
    return gs


def _num(x: float) -> float | None:
    """JSON has no NaN: a circle with no radar coverage is published as null."""
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 3)


def rain_id(site_no: str, product: str, hour_end_ts: float, mode: str) -> str:
    """Idempotent identity of one gauge-hour: the same file always maps to the same id (sink upsert key)."""
    return f"{site_no}|{product}|{int(hour_end_ts)}|{mode}"


def make_rain_record(site_no: str, hour_end_ts: float, product: str, means: dict[float, tuple[float, float, int]],
                     mode: str, fetched_ts: float, file_ts: float | None, produced_ts: float) -> dict:
    """One gauge-hour. means = {radius_km: (mean_mm, coverage, n_cells)} from common.mrms.gauge_means."""
    hour_start_ts = hour_end_ts - 3600
    m10 = means.get(MAIN_RADIUS, (float("nan"), 0.0, 0))
    rec = {
        "type": "rain", "rain_id": rain_id(site_no, product, hour_end_ts, mode),
        "site_no": site_no, "product": product, "mode": mode, "accum_hours": 1,
        "hour_start": epoch_to_iso(hour_start_ts), "hour_start_ts": hour_start_ts,
        "hour_end": epoch_to_iso(hour_end_ts), "hour_end_ts": hour_end_ts,
        # S2 contract names kept: window_end = end of the accumulation hour, rain_mm = 10 km mean
        "window_end": epoch_to_iso(hour_end_ts), "window_end_ts": hour_end_ts,
        "rain_mm": _num(m10[0]), "n_cells": m10[2],
        "file_time": epoch_to_iso(file_ts) if file_ts else None, "file_ts": file_ts,
        "fetched_at": epoch_to_iso(fetched_ts), "fetched_ts": fetched_ts,
        "produced_ts": produced_ts,
        # feed delay = when we had the rain minus the end of its hour; meaningful for LIVE only
        "feed_delay_s": round(fetched_ts - hour_end_ts, 1) if mode == "LIVE" else None,
    }
    for rad in RADII:
        mean, cov, n = means.get(rad, (float("nan"), 0.0, 0))
        tag = f"{int(rad)}km"
        rec[f"rain_mm_{tag}"] = _num(mean)
        rec[f"coverage_{tag}"] = round(cov, 3)
        rec[f"n_cells_{tag}"] = n
    return rec


def make_rain_heartbeat(site_no: str, now_ts: float, product: str, last_hour_end_ts: float | None,
                        mode: str = "LIVE") -> dict:
    return {"type": "heartbeat", "site_no": site_no, "now_ts": now_ts, "now": epoch_to_iso(now_ts), "mode": mode,
            "product": product, "last_hour_end_ts": last_hour_end_ts}


def hours_to_try(last_hour_end_ts: float | None, now_ts: float, backfill_h: int) -> list[float]:
    """Valid times (hour ends) still to publish, oldest first: after the last one published, up to the current
    hour, never more than backfill_h. First run: only the latest few hours (newest available wins)."""
    newest = mrms.floor_hour(now_ts)
    oldest = newest - (backfill_h - 1) * 3600
    if last_hour_end_ts is not None:
        oldest = max(oldest, last_hour_end_ts + 3600)
    out, h = [], oldest
    while h <= newest:
        out.append(h)
        h += 3600
    return out


def parse_when(s: str, end: bool = False) -> float:
    """'YYYY-MM-DD' (whole day: 00Z, or 23Z with end=True), 'YYYY-MM-DDTHH' or a full ISO time (UTC if no offset)."""
    s = s.strip()
    if len(s) == 10:
        return to_epoch(s + ("T23:00:00+00:00" if end else "T00:00:00+00:00"))
    if len(s) == 13:
        s += ":00:00"
    if not (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]):
        s += "+00:00"
    return to_epoch(s)


def replay_hours(start_ts: float, end_ts: float) -> tuple[list[float], int]:
    """Hour ends in [start, end] that the archive can hold, and how many were before the archive start."""
    h, out, before = mrms.floor_hour(start_ts), [], 0
    if h < start_ts:
        h += 3600
    while h <= end_ts:
        if h < mrms.ARCHIVE_START:
            before += 1
        else:
            out.append(h)
        h += 3600
    return out, before


def wall_target(hour_end_ts: float, anchor_ts: float, speed: float, wall0: float) -> float:
    """Wall-clock moment (perf_counter scale) at which a replayed hour is due: the rain of an hour is known at its
    end, so hour_end maps to wall0 + (hour_end - anchor) / speed. Same anchoring as replay_producer."""
    return wall0 + max(hour_end_ts - anchor_ts, 0.0) / speed


class HourTracker:
    """Which hours of a product were published, and what they contained. classify(): new / duplicate / revision
    (the same file re-read with different values), so a re-poll or a restart never publishes the same hour twice."""

    def __init__(self, retain_h: float = 72.0):
        self.retain_s = retain_h * 3600
        self.seen: dict[str, str] = {}

    @staticmethod
    def _key(product: str, hour_end_ts: float) -> str:
        return f"{product}|{int(hour_end_ts)}"

    def classify(self, product: str, hour_end_ts: float, records: list[dict]) -> str:
        sig = json.dumps(sorted((r["site_no"], r["rain_mm_5km"], r["rain_mm_10km"], r["rain_mm_25km"])
                                for r in records))
        k = self._key(product, hour_end_ts)
        prev = self.seen.get(k)
        self.seen[k] = sig
        if prev is None:
            return "new"
        return "duplicate" if prev == sig else "revision"

    def last_hour(self, product: str) -> float | None:
        ts = [float(k.split("|")[1]) for k in self.seen if k.split("|")[0] == product]
        return max(ts, default=None)

    def prune(self, now_ts: float):
        for k in [k for k in self.seen if now_ts - float(k.split("|")[1]) > self.retain_s]:
            del self.seen[k]

    def dumps(self) -> str:
        return json.dumps(self.seen)

    @classmethod
    def loads(cls, s: str) -> "HourTracker":
        t = cls()
        t.seen = json.loads(s)
        return t


# ---------- network ----------

def fetch_hour(product: str, hour_end_ts: float, tries: int = 4) -> tuple[bytes, float | None] | None:
    """(gzipped GRIB2 bytes, S3 Last-Modified epoch) or None if the file does not exist (yet)."""
    import requests
    url = f"{mrms.BUCKET}/{mrms.hour_key(hour_end_ts, product)}"
    for k in range(tries):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": config.USER_AGENT})
            if r.status_code in (403, 404):
                return None
            r.raise_for_status()
            lm = r.headers.get("Last-Modified")
            return r.content, (parsedate_to_datetime(lm).timestamp() if lm else None)
        except Exception:  # noqa: BLE001
            if k == tries - 1:
                raise
            time.sleep(2 * 2 ** k)
    return None


def list_day(product: str, day: datetime) -> list[tuple[str, str]]:
    import requests
    out, tok = [], None
    while True:
        params = {"list-type": "2", "prefix": f"CONUS/{product}/{day:%Y%m%d}/"}
        if tok:
            params["continuation-token"] = tok
        r = requests.get(mrms.BUCKET + "/", params=params, timeout=60, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        items, tok = mrms.parse_listing(r.text)
        out += items
        if not tok:
            return out


def probe(products: list[str], now_ts: float | None = None) -> list[dict]:
    """Newest HH:00 file per product and its publication latency (S3 LastModified - valid time), from the listing."""
    now_ts = now_ts or now_epoch()
    rows = []
    for p in products:
        found = []
        for back in (0, 1):
            day = datetime.fromtimestamp(now_ts - back * 86400, timezone.utc)
            found += [(mrms.key_time(k), lm) for k, lm in list_day(p, day)]
        hourly = sorted((t, lm) for t, lm in found if t is not None and t % 3600 == 0)
        lat = [(datetime.fromisoformat(lm.replace("Z", "+00:00")).timestamp() - t) / 60 for t, lm in hourly[-6:]]
        if hourly:
            t, lm = hourly[-1]
            rows.append({"product": p, "newest_hour_end": epoch_to_iso(t), "last_modified": lm,
                         "latency_min": round(lat[-1], 1), "latency_min_last6": [round(x, 1) for x in lat],
                         "age_now_min": round((now_ts - t) / 60, 1)})
        else:
            rows.append({"product": p, "newest_hour_end": None})
    return rows


# ---------- one hour -> records ----------

def hour_records(body: bytes, file_ts: float | None, hour_end_ts: float, product: str, gauges: list[dict],
                 mode: str, fetched_ts: float, cache: mrms.CircleCache) -> list[dict]:
    geom, vals, missing = mrms.decode(body)
    means = mrms.gauge_means(vals, geom, missing, gauges, list(RADII), cache)
    now = now_epoch()
    return [make_rain_record(g["site_no"], hour_end_ts, product, means[g["site_no"]], mode, fetched_ts, file_ts, now)
            for g in gauges]


def publish(producer, topic: str, records: list[dict]):
    for r in records:
        producer.send(topic, r["site_no"], r)


# ---------- live ----------

def load_state() -> HourTracker:
    if not os.path.exists(STATE_FILE):
        return HourTracker()
    with open(STATE_FILE, encoding="utf-8") as f:
        return HourTracker.loads(json.load(f)["tracker"])


def save_state(tracker: HourTracker):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tracker": tracker.dumps(), "saved_ts": now_epoch()}, f)
    os.replace(tmp, STATE_FILE)


def poll_once(gauges, product, tracker, producer, cache, backfill_h=6, fetcher=fetch_hour, persist=True) -> dict:
    now_ts = now_epoch()
    stats = {"hours": [], "new": 0, "revision": 0, "duplicate": 0, "records": 0, "error": ""}
    last = tracker.last_hour(product)
    todo = hours_to_try(last, now_ts, backfill_h)
    if last is None:
        todo = todo[::-1]          # first run: newest available hour only (backfill starts from there)
    try:
        for h in todo:
            got = fetcher(product, h)
            if got is None:
                if last is None:
                    continue       # not there yet: try the hour before
                break              # products appear in order: later hours are not there either
            body, file_ts = got
            recs = hour_records(body, file_ts, h, product, gauges, "LIVE", now_epoch(), cache)
            kind = tracker.classify(product, h, recs)
            stats[kind] += 1
            if kind != "duplicate":
                publish(producer, config.TOPIC_RAIN, recs)
                stats["records"] += len(recs)
                stats["hours"].append({"hour_end": epoch_to_iso(h), "kind": kind,
                                       "feed_delay_min": round((recs[0]["fetched_ts"] - h) / 60, 1),
                                       "publish_latency_min": round((file_ts - h) / 60, 1) if file_ts else None})
            if persist:
                save_state(tracker)
            if last is None:
                break
    except Exception as e:  # noqa: BLE001  network / decode: next poll retries; heartbeats still go out
        stats["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        producer.send(config.TOPIC_DLQ, "mrms_qpe", {"error": stats["error"], "product": product, "at": now_ts})
    lh = tracker.last_hour(product)
    for g in gauges:
        producer.send(config.TOPIC_RAIN, g["site_no"], make_rain_heartbeat(g["site_no"], now_epoch(), product, lh))
    producer.flush(10)
    tracker.prune(now_ts)
    return stats


def run_live(a, gauges, producer):
    product = mrms.product_name(a.product or "radaronly")
    tracker, cache = load_state(), mrms.CircleCache()
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    print(f"[start {datetime.now():%H:%M:%S}] MRMS live: {product}, {len(gauges)} gauges, radii {RADII} km, "
          f"every {a.every_s:.0f}s -> {config.TOPIC_RAIN} @ {config.KAFKA_BOOTSTRAP}", flush=True)
    while not stop["flag"]:
        t0 = time.monotonic()
        stats = poll_once(gauges, product, tracker, producer, cache, a.backfill_h)
        lh = tracker.last_hour(product)
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} hours={stats['hours']} "
              f"new={stats['new']} rev={stats['revision']} dup={stats['duplicate']} records={stats['records']} "
              f"newest={epoch_to_iso(lh) if lh else None} sent={producer.sent} failed={producer.failed} "
              f"poll {time.monotonic() - t0:.1f}s{'  ERROR ' + stats['error'] if stats['error'] else ''}", flush=True)
        if a.once:
            break
        for _ in range(int(a.every_s)):
            if stop["flag"]:
                break
            time.sleep(1)
    producer.flush(30)


# ---------- replay ----------

def run_replay(a, gauges, producer, fetcher=fetch_hour, sleep=time.sleep, clock=time.perf_counter) -> dict:
    product = mrms.product_name(a.product or "pass2")
    t0, t1 = parse_when(a.start), parse_when(a.end, end=True)
    hours, before = replay_hours(t0, t1)
    topic = config.TOPIC_REPLAY_RAIN
    stats = {"hours": len(hours), "ok": 0, "missing": 0, "records": 0, "behind": 0, "before_archive": before}
    if before:
        print(f"  {before} hour(s) before {epoch_to_iso(mrms.ARCHIVE_START)} skipped: the MRMS archive starts there, "
              f"so there is no radar rain for them")
    cache = mrms.CircleCache()
    pace = "as fast as possible" if a.no_sleep else f"speed {a.speed:g}x"
    with Task(f"MRMS replay {product} {epoch_to_iso(t0)}..{epoch_to_iso(t1)} ({len(hours)} h, {len(gauges)} "
              f"gauges, {pace}) -> {topic}", total=len(hours), unit=" h") as task, \
            ThreadPoolExecutor(max_workers=max(a.workers, 1)) as ex:
        q: deque = deque()
        it = iter(hours)
        for h in it:                                   # bounded look-ahead: at most 2 x workers files in memory
            q.append((h, ex.submit(fetcher, product, h)))
            if len(q) >= 2 * max(a.workers, 1):
                break
        if q:
            q[0][1].result()                           # the clock starts when the first hour is in hand
        wall0 = clock()
        n = 0
        while q:
            h, fut = q.popleft()
            nxt = next(it, None)
            if nxt is not None:
                q.append((nxt, ex.submit(fetcher, product, nxt)))
            got = fut.result()
            n += 1
            if got is None:
                stats["missing"] += 1
                task.update(n, f"{epoch_to_iso(h)} missing in the archive")
                continue
            body, file_ts = got
            recs = hour_records(body, file_ts, h, product, gauges, "SIMULATION", now_epoch(), cache)
            if not a.no_sleep:
                due = wall_target(h, t0, a.speed, wall0)
                lag = due - clock()
                if lag > 0:
                    sleep(lag)
                elif lag < -1.0:
                    stats["behind"] += 1
            publish(producer, topic, recs)
            stats["ok"] += 1
            stats["records"] += len(recs)
            r10 = [r["rain_mm_10km"] for r in recs if r["rain_mm_10km"] is not None]
            task.update(n, f"{epoch_to_iso(h)} max 10 km mean {max(r10) if r10 else float('nan'):.2f} mm"
                           + (f" ({recs[0]['site_no']})" if len(recs) == 1 else ""))
        producer.flush(60)
        stats["sent"], stats["failed"] = producer.sent, producer.failed
        task.summary(f"{stats['ok']}/{stats['hours']} hours, {stats['records']} records, missing {stats['missing']}, "
                     f"behind schedule {stats['behind']}, sent={producer.sent} failed={producer.failed}")
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--product", help="radaronly | pass1 | pass2 or a bucket folder (default: radaronly live, "
                                     "pass2 replay)")
    p.add_argument("--gauges", default=GAUGES_CSV)
    p.add_argument("--sites", help="comma-separated site_no or NWS lid (default: every gauge in --gauges)")
    p.add_argument("--every-s", type=float, default=120.0)
    p.add_argument("--backfill-h", type=int, default=6, help="live: at most this many missed hours are filled in")
    p.add_argument("--once", action="store_true", help="live: poll once and exit")
    p.add_argument("--probe", action="store_true", help="print the newest hour and latency of each product, exit")
    p.add_argument("--replay", action="store_true", help="historical hours -> replay.rain (SIMULATION)")
    p.add_argument("--start", help="replay: YYYY-MM-DD, YYYY-MM-DDTHH or ISO time (UTC)")
    p.add_argument("--end", help="replay: same formats; a bare date means through 23Z")
    p.add_argument("--speed", type=float, default=3600.0, help="replay: simulated seconds per real second")
    p.add_argument("--no-sleep", action="store_true", help="replay: publish as fast as downloads allow")
    p.add_argument("--workers", type=int, default=4, help="replay: parallel downloads")
    a = p.parse_args(argv)

    if a.probe:
        for r in probe([mrms.PRODUCTS[k] for k in ("radaronly", "pass1", "pass2")]):
            print(json.dumps(r))
        return
    from common.kafka_io import JsonProducer
    gauges = load_gauges(a.gauges, a.sites)
    if a.replay:
        if not (a.start and a.end):
            p.error("--replay needs --start and --end")
        run_replay(a, gauges, JsonProducer("mrms-replay-producer"))
    else:
        run_live(a, gauges, JsonProducer("mrms-qpe-producer"))


if __name__ == "__main__":
    main()
