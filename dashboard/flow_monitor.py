"""Live workflow view: the architecture diagram, drawn on a canvas, driven by real Kafka metrics.

Run (stack up, from the repo root), then open http://127.0.0.1:8765

    python -m dashboard.flow_monitor
    python -m dashboard.flow_monitor --demo        # scripted fake data, no Kafka needed

What it shows, refreshed every couple of seconds:
  * every topic: messages/s, total messages, age of the newest message
  * every consumer group: running / stopped / not running, instances (Faust: 3 expected), lag
  * every producer: inferred from its topic (new messages, or a recent heartbeat)
  * brokers up, under-replicated and offline partitions, dead-letter count, PostgreSQL row counts
  * a log of every status change with a timestamp (stop something and read off the recovery time)

Producers are judged by what they publish, not by their process: a producer is "alive" if its topic
received a message recently (the live USGS producer sends a heartbeat on every poll).
Nodes whose consumer group does not exist yet (forecaster, agent, notifier) show as "not running";
that is the build progress, not an error.

Tabs: Pipeline flow (above) | Map (every gauge's newest stage against its official flood levels,
live or replay) | Readings and alerts (latest windows and alerts from PostgreSQL, filled once the sink exists).
The map needs config/gauge_locations.csv:  python -m tools.gauge_locations

Read-only for Kafka monitoring (throwaway consumers and the admin API, no offsets committed). The one write path is
the approval panel in Readings & alerts: Approve / Edit / Reject call alerting/approval.py, which publishes to
alerts.approved only after a person clicks Approve.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from common import config, nwps

HERE = os.path.dirname(os.path.abspath(__file__))
LOC_CSV = os.path.join(os.path.dirname(HERE), "config", "gauge_locations.csv")

# ---------------------------------------------------------------- the graph (matches the Phase I diagram)
# kind: producer | topic | group | store | manual | info
NODE_SPECS = {
    "p_usgs":     {"kind": "producer", "title": "USGS live", "topics": [config.TOPIC_GAUGE], "live": True},
    "p_mrms":     {"kind": "producer", "title": "MRMS live", "topics": [config.TOPIC_RAIN], "live": True},
    "p_replay":   {"kind": "producer", "title": "Replay", "topics": [config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN], "live": False},
    "t_gauge":    {"kind": "topic", "title": config.TOPIC_GAUGE, "topics": [config.TOPIC_GAUGE]},
    "t_rain":     {"kind": "topic", "title": config.TOPIC_RAIN, "topics": [config.TOPIC_RAIN]},
    "t_replay":   {"kind": "topic", "title": "replay.* (2 topics)", "topics": [config.TOPIC_REPLAY_GAUGE, config.TOPIC_REPLAY_RAIN]},
    "faust":      {"kind": "group", "title": "Faust windowing", "group": config.GROUP_WINDOWING, "expected": 3},
    "t_agg":      {"kind": "topic", "title": config.TOPIC_AGG, "topics": [config.TOPIC_AGG]},
    "forecaster": {"kind": "group", "title": "Forecaster", "group": config.GROUP_FORECASTER, "expected": 1},
    "t_alerts":   {"kind": "topic", "title": config.TOPIC_ALERTS, "topics": [config.TOPIC_ALERTS]},
    "sink":       {"kind": "group", "title": "Postgres sink", "group": config.GROUP_SINK, "expected": 1},
    "pg":         {"kind": "store", "title": "PostgreSQL"},
    "bi":         {"kind": "info", "title": "Power BI"},
    "agent":      {"kind": "group", "title": "Agent triage", "group": config.GROUP_AGENT, "expected": 1},
    "human":      {"kind": "manual", "title": "Human approval"},
    "t_approved": {"kind": "topic", "title": config.TOPIC_APPROVED, "topics": [config.TOPIC_APPROVED]},
    "notifier":   {"kind": "group", "title": "Notifier", "group": config.GROUP_NOTIFIER, "expected": 1},
}
# (id, from, to, node whose topics give the rate, planned)
EDGES = [
    ("e_p_usgs", "p_usgs", "t_gauge", "t_gauge", False),
    ("e_p_mrms", "p_mrms", "t_rain", "t_rain", False),
    ("e_p_replay", "p_replay", "t_replay", "t_replay", False),
    ("e_gauge_faust", "t_gauge", "faust", "t_gauge", False),
    ("e_rain_faust", "t_rain", "faust", "t_rain", True),       # Faust does not read rain.mrms yet (Phase 2b)
    ("e_replay_faust", "t_replay", "faust", "t_replay", False),
    ("e_faust_agg", "faust", "t_agg", "t_agg", False),
    ("e_agg_sink", "t_agg", "sink", "t_agg", False),
    ("e_agg_fore", "t_agg", "forecaster", "t_agg", False),
    ("e_fore_alerts", "forecaster", "t_alerts", "t_alerts", False),
    ("e_alerts_sink", "t_alerts", "sink", "t_alerts", False),
    ("e_alerts_agent", "t_alerts", "agent", "t_alerts", False),
    ("e_sink_pg", "sink", "pg", None, False),
    ("e_pg_bi", "pg", "bi", None, False),
    ("e_agent_pg", "agent", "pg", None, False),
    ("e_agent_human", "agent", "human", None, False),
    ("e_human_appr", "human", "t_approved", "t_approved", False),
    ("e_appr_notif", "t_approved", "notifier", "t_approved", False),
]
PG_TABLES = ["windowed_aggregates", "risk_scores", "alerts"]


# ---------------------------------------------------------------- pure status logic (unit-tested)
def _fmt_age(s):
    if s is None:
        return "no messages yet"
    if s < 90:
        return f"last {s:.0f} s ago"
    if s < 5400:
        return f"last {s / 60:.0f} min ago"
    return f"last {s / 3600:.1f} h ago"


def build_view(raw: dict, live_stale_s: float = 900.0, lag_warn: int = 500) -> dict:
    """raw (from Kafka or the demo) -> nodes, edges and cluster summary with a status per node.

    status: ok (green) | idle (blue: alive, nothing flowing) | warn (amber) | down (red)
            | absent (grey, not running / no data yet) | manual | info
    """
    now = raw["ts"]
    topics, groups = raw.get("topics", {}), raw.get("groups", {})

    def agg_topics(names):
        tot = sum(topics.get(n, {}).get("total", 0) for n in names)
        rate = sum(topics.get(n, {}).get("rate", 0.0) for n in names)
        ts = [topics[n]["last_ts"] for n in names if topics.get(n, {}).get("last_ts")]
        age = (now - max(ts)) if ts else None
        return tot, rate, age

    nodes, rates = {}, {}
    for nid, sp in NODE_SPECS.items():
        k = sp["kind"]
        if k in ("producer", "topic"):
            tot, rate, age = agg_topics(sp["topics"])
            rates[nid] = rate
            if k == "topic":
                lines = [f"{rate:.1f} msg/s · total {tot:,}", _fmt_age(age)]
                if tot == 0:                 # the topic exists (compose created it) but nothing was published yet
                    status, lines = "idle", ["empty · waiting", "for a producer"]
                else:
                    status = "ok" if rate > 0 else "idle"
            elif sp["live"]:
                if age is None:
                    status, lines = "absent", ["no data yet", "(not built or never started)"]
                elif rate > 0 or age <= live_stale_s:
                    status, lines = "ok", ["alive · " + _fmt_age(age), f"{rate:.1f} msg/s"]
                else:
                    status, lines = "down", ["SILENT · " + _fmt_age(age), "no heartbeat"]
            else:
                if age is None:
                    status, lines = "absent", ["not run yet"]
                elif rate > 0:
                    status, lines = "ok", [f"replaying {rate:.0f} msg/s", f"sent {tot:,}"]
                else:
                    status, lines = "idle", ["idle", f"sent {tot:,}"]
            nodes[nid] = {"status": status, "lines": lines}
        elif k == "group":
            g = groups.get(sp["group"])
            exp = sp["expected"]
            if g is None:
                nodes[nid] = {"status": "absent", "lines": ["not running", f"group {sp['group']}"]}
            else:
                m, lag = g["members"], g.get("lag", 0)
                lines = [f"{m}/{exp} instance{'s' if exp != 1 else ''} · {g['state'].lower()}", f"lag {lag:,}"]
                if m == 0:
                    status, lines[0] = "down", f"STOPPED · 0/{exp} instances"
                elif m < exp:
                    status = "warn"
                elif lag > lag_warn:
                    status = "warn"
                else:
                    status = "ok"
                nodes[nid] = {"status": status, "lines": lines}
        elif k == "store":
            pg = raw.get("pg")
            if pg is None:
                nodes[nid] = {"status": "absent", "lines": ["not checked"]}
            elif not pg.get("ok"):
                nodes[nid] = {"status": "down", "lines": ["UNREACHABLE", str(pg.get("error", ""))[:40]]}
            else:
                c = pg["counts"]
                nodes[nid] = {"status": "ok", "lines": [f"windows {c.get('windowed_aggregates', 0):,}",
                                                        f"alerts {c.get('alerts', 0):,} · scores {c.get('risk_scores', 0):,}"]}
        elif k == "manual":
            nodes[nid] = {"status": "manual", "lines": ["nothing unapproved", "is sent"]}
        else:
            nodes[nid] = {"status": "info", "lines": ["DirectQuery", "reads PostgreSQL"]}
    for nid in NODE_SPECS:
        nodes[nid]["kind"] = NODE_SPECS[nid]["kind"]
        nodes[nid]["title"] = NODE_SPECS[nid]["title"]

    edges = []
    for eid, a, b, rate_node, planned in EDGES:
        rate = rates.get(rate_node, 0.0) if rate_node else 0.0
        sa, sb = nodes[a]["status"], nodes[b]["status"]
        if planned:
            st = "planned"
        elif "absent" in (sa, sb):
            st = "inactive"
        elif sb == "down" and rate > 0:
            st = "blocked"                  # data is arriving, the consumer is gone
        elif rate_node is None:
            st = "idle" if sa in ("ok", "info", "idle", "manual") else "inactive"
        elif rate > 0:
            st = "flow"
        else:
            st = "idle"
        edges.append({"id": eid, "from": a, "to": b, "state": st, "rate": round(rate, 2)})

    dlq_tot, _, _ = agg_topics([config.TOPIC_DLQ])
    cluster = {
        "kafka_ok": raw.get("kafka_ok", False), "error": raw.get("kafka_error"),
        "brokers_up": raw.get("brokers_up", 0), "brokers_expected": raw.get("brokers_expected", 3),
        "under_replicated": raw.get("under_replicated", 0), "offline": raw.get("offline_partitions", 0),
        "dlq": dlq_tot,
        "pg": None if raw.get("pg") is None else bool(raw["pg"].get("ok")),
    }
    return {"ts": now, "nodes": nodes, "edges": edges, "cluster": cluster}


class EventLog:
    """Remembers the last status of every node and records each change."""

    def __init__(self, keep=60):
        self.last, self.events, self.keep = {}, [], keep

    def update(self, view: dict):
        for nid, n in view["nodes"].items():
            old = self.last.get(nid)
            if old is not None and old != n["status"]:
                self.events.insert(0, {"ts": view["ts"], "node": n["title"], "from": old, "to": n["status"]})
            self.last[nid] = n["status"]
        c = view["cluster"]
        if self.last.get("brokers") is not None and self.last["brokers"] != c["brokers_up"]:
            self.events.insert(0, {"ts": view["ts"], "node": "Kafka brokers",
                                   "from": f"{self.last['brokers']} up", "to": f"{c['brokers_up']} up"})
        self.last["brokers"] = c["brokers_up"]
        del self.events[self.keep:]
        return self.events


# ---------------------------------------------------------------- live probe (Kafka + PostgreSQL)
class KafkaProbe:
    def __init__(self):
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient
        conf = {"bootstrap.servers": config.KAFKA_BOOTSTRAP, "socket.timeout.ms": 4000}
        self.admin = AdminClient(conf)
        self.wm = Consumer({**conf, "group.id": "flow-monitor-wm", "enable.auto.commit": False})
        self.peek = Consumer({**conf, "group.id": "flow-monitor-peek", "enable.auto.commit": False,
                              "auto.offset.reset": "latest"})
        self.prev: dict[str, tuple[float, int]] = {}      # topic -> (t, total)
        self.cache: dict[tuple, float] = {}               # (topic, partition, high) -> message ts

    def _last_ts(self, topic, p, lo, hi):
        from confluent_kafka import TopicPartition
        key = (topic, p, hi)
        if key in self.cache:
            return self.cache[key]
        self.peek.assign([TopicPartition(topic, p, hi - 1)])
        msg = self.peek.poll(1.0)
        ts = None
        if msg is not None and not msg.error():
            ms = msg.timestamp()[1]
            ts = ms / 1000.0 if ms and ms > 0 else None
        self.cache = {k: v for k, v in self.cache.items() if k[:2] != key[:2]}
        self.cache[key] = ts
        return ts

    def snapshot(self) -> dict:
        from confluent_kafka import TopicPartition
        now = time.time()
        raw = {"ts": now, "topics": {}, "groups": {}, "kafka_ok": False, "brokers_expected": 3}
        try:
            md = self.admin.list_topics(timeout=5)
        except Exception as e:
            raw["kafka_error"] = str(e)[:120]
            raw["pg"] = probe_pg()
            return raw
        raw["kafka_ok"] = True
        raw["brokers_up"] = len(md.brokers)
        under = offline = 0
        highs: dict[tuple, int] = {}
        for t in config.ALL_TOPICS:
            tm = md.topics.get(t)
            if tm is None or tm.error is not None:
                continue
            total, last = 0, None
            for p, pm in tm.partitions.items():
                if pm.leader == -1:
                    offline += 1
                if len(pm.isrs) < len(pm.replicas):
                    under += 1
                try:
                    lo, hi = self.wm.get_watermark_offsets(TopicPartition(t, p), timeout=3, cached=False)
                except Exception:
                    continue
                highs[(t, p)] = hi
                total += hi - lo
                if hi > lo:
                    ts = self._last_ts(t, p, lo, hi)
                    if ts and (last is None or ts > last):
                        last = ts
            prev = self.prev.get(t)
            rate = max(0.0, (total - prev[1]) / (now - prev[0])) if prev and now > prev[0] else 0.0
            self.prev[t] = (now, total)
            raw["topics"][t] = {"total": total, "rate": rate, "last_ts": last}
        raw["under_replicated"], raw["offline_partitions"] = under, offline
        raw["groups"] = self._groups(highs)
        raw["pg"] = probe_pg()
        return raw

    def _groups(self, highs):
        from confluent_kafka import ConsumerGroupTopicPartitions
        wanted = {config.GROUP_WINDOWING, config.GROUP_FORECASTER, config.GROUP_SINK,
                  config.GROUP_AGENT, config.GROUP_NOTIFIER}
        out = {}
        try:
            listing = self.admin.list_consumer_groups(request_timeout=5).result()
            ids = [g.group_id for g in listing.valid if g.group_id in wanted]
            if not ids:
                return out
            for gid, fut in self.admin.describe_consumer_groups(ids, request_timeout=5).items():
                d = fut.result()
                out[gid] = {"state": getattr(d.state, "name", str(d.state)), "members": len(d.members), "lag": 0}
            for gid in ids:
                offs = self.admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(gid)],
                                                              request_timeout=5)[gid].result()
                lag = 0
                for tp in offs.topic_partitions:
                    hi = highs.get((tp.topic, tp.partition))
                    if hi is not None and tp.offset is not None and tp.offset >= 0:
                        lag += max(0, hi - tp.offset)
                out[gid]["lag"] = lag
        except Exception:
            pass                       # keep whatever we collected; the next cycle retries
        return out


def probe_pg():
    try:
        import psycopg2
        with psycopg2.connect(config.PG_DSN, connect_timeout=2) as con, con.cursor() as cur:
            counts = {}
            for t in PG_TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                counts[t] = cur.fetchone()[0]
        return {"ok": True, "counts": counts}
    except Exception as e:
        return {"ok": False, "error": str(e).strip().splitlines()[0] if str(e).strip() else "error"}


# ---------------------------------------------------------------- map: gauge locations + newest stage
REGIONS_JSON = os.path.join(os.path.dirname(HERE), "config", "regions.json")


def load_regions(path: str = REGIONS_JSON) -> list[dict]:
    """Monitored areas shown on the globe. Add an entry (and its gauge CSV, same columns as gauge_locations.csv) to monitor
    another area; fields: id, name, provider, lat, lon, gauges_csv (relative to the repo root), enabled."""
    try:
        with open(path, encoding="utf-8") as f:
            regs = json.load(f)
    except (OSError, ValueError):
        regs = [{"id": "default", "name": "Gauge network", "provider": "", "lat": 30.2, "lon": -98.3,
                 "gauges_csv": "config/gauge_locations.csv", "enabled": True}]
    return [r for r in regs if r.get("enabled", True) and r.get("id") and r.get("lat") is not None and r.get("lon") is not None]


def region_csv(region: dict) -> str:
    return os.path.join(os.path.dirname(HERE), region["gauges_csv"])


def regions_summary(regions: list[dict], latest: dict, live: bool, now: float, stale_s: float = 5400.0) -> list[dict]:
    """Per region: gauge count, how many report, and the worst official category right now (for the globe markers)."""
    sev = {"major": 5, "moderate": 4, "minor": 3, "action": 2, "normal": 1, "stale": 0, "nodata": 0}
    out = []
    for r in regions:
        locs = load_locations(region_csv(r)) or []
        gs = build_gauges(locs, latest, live, now, stale_s) if locs else []
        worst = max((g["status"] for g in gs), key=lambda k: sev.get(k, 0), default="nodata")
        out.append({"id": r["id"], "name": r["name"], "provider": r.get("provider", ""), "lat": r["lat"], "lon": r["lon"],
                    "n_gauges": len(gs), "n_reporting": sum(1 for g in gs if g["stage"] is not None),
                    "n_flooding": sum(1 for g in gs if sev.get(g["status"], 0) >= 3), "worst": worst})
    return out


def load_locations(path: str = LOC_CSV) -> list[dict] | None:
    if not os.path.exists(path):
        return None
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            def num(k):
                try:
                    return float(r[k]) if r.get(k) not in (None, "") else None
                except ValueError:
                    return None
            out.append({"site_no": r["site_no"], "lid": r["lid"], "name": r["name"], "lat": num("lat"), "lon": num("lon"),
                        "county": r.get("county", ""), "cats": {c: num(c) for c in nwps.CATEGORIES}})
    return out


def gauge_status(stage, age_s, cats, live: bool, stale_s: float = 5400.0) -> str:
    """normal | action | minor | moderate | major | stale | nodata (stale only applies to live data)."""
    if stage is None:
        return "nodata"
    if live and age_s is not None and age_s > stale_s:
        return "stale"
    return nwps.category_of(stage, cats) or "normal"


def build_gauges(locations, latest: dict, live: bool, now: float, stale_s: float = 5400.0) -> list[dict]:
    out = []
    for g in locations:
        r = latest.get(g["site_no"])
        stage = r["value"] if r else None
        age = (now - r["reading_ts"]) if r and live else None
        out.append({**g, "stage": stage, "age_s": age, "provisional": bool(r and "P" in (r.get("qualifiers") or [])),
                    "reading_ts": r["reading_ts"] if r else None,
                    "status": gauge_status(stage, age, g["cats"], live, stale_s)})
    return out


class LatestReadings:
    """Newest stage per gauge, separately for live (gauge.usgs) and replay (replay.gauge)."""

    def __init__(self, backfill=5000):
        self.live, self.replay, self.backfill = {}, {}, backfill
        self.lock = threading.Lock()

    def ingest(self, topic: str, msg: dict):
        if msg.get("type") != "reading" or msg.get("parameter") != config.PARAM_STAGE:
            return
        replay = topic == config.TOPIC_REPLAY_GAUGE
        d = self.replay if replay else self.live
        cur = d.get(msg["site_no"])
        # live: newest reading time wins; replay: the most recently replayed reading wins (it plays history)
        key = "fetched_ts" if replay else "reading_ts"
        if cur is None or msg.get(key, 0) >= cur.get(key, 0):
            with self.lock:
                d[msg["site_no"]] = {"value": msg["value"], "reading_ts": msg["reading_ts"],
                                     "fetched_ts": msg.get("fetched_ts", 0), "qualifiers": msg.get("qualifiers") or []}

    def run(self):
        from confluent_kafka import Consumer, TopicPartition
        while True:
            try:
                conf = {"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": "flow-monitor-latest",
                        "enable.auto.commit": False, "enable.partition.eof": False}
                c = Consumer(conf)
                parts = []
                for t in (config.TOPIC_GAUGE, config.TOPIC_REPLAY_GAUGE):
                    md = c.list_topics(t, timeout=5).topics[t]
                    for pn in md.partitions:
                        lo, hi = c.get_watermark_offsets(TopicPartition(t, pn), timeout=5)
                        parts.append(TopicPartition(t, pn, max(lo, hi - self.backfill)))
                c.assign(parts)
                while True:
                    m = c.poll(1.0)
                    if m is None or m.error():
                        continue
                    try:
                        self.ingest(m.topic(), json.loads(m.value()))
                    except (ValueError, KeyError, TypeError):
                        continue
            except Exception:
                time.sleep(5)           # broker down or restarting: reconnect and re-read the tail


def probe_db() -> dict:
    try:
        import psycopg2
        with psycopg2.connect(config.PG_DSN, connect_timeout=2) as con, con.cursor() as cur:
            def rows(sql):
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, r)} for r in cur.fetchall()]
            return {"ok": True,
                    "windows": rows("SELECT site_no, window_start, mode, n_readings, stage_last, rise_rate_ft_per_h, rain_3h "
                                    "FROM windowed_aggregates ORDER BY window_start DESC LIMIT 30"),
                    "alerts": rows("SELECT alert_id, site_no, mode, category, probability, horizon_h, status, raised_at, delivered_at "
                                   "FROM alerts ORDER BY raised_at DESC LIMIT 30")}
    except Exception as e:
        return {"ok": False, "error": (str(e).strip().splitlines() or ["error"])[0][:120]}


def pg_conn():
    import psycopg2
    return psycopg2.connect(config.PG_DSN, connect_timeout=3)


def approval_api(action: str, body: dict) -> tuple[int, dict]:
    """Run an approval action; returns (http status, json body). Never raises."""
    from alerting import approval
    if CTX["demo"]:
        return 400, {"error": "demo mode has no database"}
    try:
        conn = pg_conn()
        try:
            adv = body.get("advisory_id", "")
            by = (body.get("by") or "").strip()
            if action == "approve":
                if CTX.get("producer") is None:
                    from common.kafka_io import JsonProducer
                    CTX["producer"] = JsonProducer("console-approval")
                rec = approval.approve(conn, CTX["producer"], adv, by, body.get("text"))
                return 200, {"ok": True, "approved": rec["advisory_id"]}
            if action == "reject":
                approval.reject(conn, adv, by or "console")
                return 200, {"ok": True}
            if action == "edit":
                approval.edit(conn, adv, body.get("text", ""))
                return 200, {"ok": True}
            return 404, {"error": "unknown action"}
        finally:
            conn.close()
    except approval.ApprovalError as e:
        return 409, {"error": str(e)}
    except Exception as e:
        return 500, {"error": (str(e).strip().splitlines() or ["error"])[0][:160]}


def advisories_api() -> dict:
    from alerting import approval
    if CTX["demo"]:
        return {"ok": True, "advisories": []}
    try:
        conn = pg_conn()
        try:
            return {"ok": True, "advisories": approval.list_advisories(conn)}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": (str(e).strip().splitlines() or ["error"])[0][:120]}


def demo_gauges(t: float):
    """Fake gauges around the Texas Hill Country for --demo when config/gauge_locations.csv is missing."""
    locs, latest = [], {}
    now = time.time()
    for i in range(14):
        minor = 5.0 + (i % 4) * 2.5
        lat, lon = 29.9 + (i * 37 % 100) / 100 * 1.1, -99.9 + (i * 53 % 100) / 100 * 2.3
        sid = f"0810{i:04d}"
        locs.append({"site_no": sid, "lid": f"DM{i:02d}T2", "name": f"Demo creek {i + 1}", "lat": lat, "lon": lon, "county": "Demo",
                     "cats": {"action": minor, "minor": minor, "moderate": minor * 1.5, "major": minor * 2.0}})
        lvl = minor * (0.35 + 0.9 * abs(math.sin(t / 25 + i)))
        latest[sid] = {"value": round(lvl, 2), "reading_ts": now - (60 if i != 3 else 7200), "fetched_ts": now,
                       "qualifiers": ["P"]}
    return locs, latest


def demo_db():
    now = time.time()
    iso = lambda dt: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - dt))
    return {"ok": True,
            "windows": [{"site_no": "08158700", "window_start": iso(3600 * k), "mode": "SIMULATION", "n_readings": 12 - (k == 3) * 12,
                         "stage_last": 2.8 + k * 0.4, "rise_rate_ft_per_h": 0.4, "rain_3h": None} for k in range(1, 6)],
            "alerts": []}


# ---------------------------------------------------------------- scripted demo (no Kafka)
def demo_raw(t: float, t0: float) -> dict:
    """60 s cycle: 0-15 s normal, 15-30 s one Faust instance lost (lag grows), 30-45 s broker 2 down,
    45-60 s replay finished."""
    e = (t - t0) % 60
    now = time.time()
    replay_on = e < 45
    topics = {
        config.TOPIC_GAUGE: {"total": 4210 + int(t - t0), "rate": 0.4, "last_ts": now - 20},
        config.TOPIC_REPLAY_GAUGE: {"total": 18400, "rate": 42.0 if replay_on else 0.0, "last_ts": now - (0.5 if replay_on else 12)},
        config.TOPIC_AGG: {"total": 960, "rate": 1.3 if e < 30 else 0.4, "last_ts": now - 3},
        config.TOPIC_ALERTS: {"total": 0, "rate": 0.0, "last_ts": None},
        config.TOPIC_DLQ: {"total": 2, "rate": 0.0, "last_ts": now - 600},
    }
    members = 3 if e < 15 or e >= 30 else 2
    groups = {config.GROUP_WINDOWING: {"state": "Stable", "members": members,
                                       "lag": 0 if members == 3 else int((e - 15) * 90)},
              config.GROUP_SINK: {"state": "Stable", "members": 1, "lag": 3}}
    return {"ts": now, "topics": topics, "groups": groups, "kafka_ok": True,
            "brokers_up": 2 if 30 <= e < 45 else 3, "brokers_expected": 3,
            "under_replicated": 8 if 30 <= e < 45 else 0, "offline_partitions": 0,
            "pg": {"ok": True, "counts": {"windowed_aggregates": 960, "risk_scores": 0, "alerts": 0}}}


# ---------------------------------------------------------------- server
class State:
    def __init__(self):
        self.cond = threading.Condition()
        self.seq, self.payload = 0, b"{}"

    def publish(self, obj):
        with self.cond:
            self.seq += 1
            self.payload = json.dumps(obj).encode()
            self.cond.notify_all()


STATE = State()
CTX = {"latest": None, "demo": False, "t0": time.time(), "stale_s": 5400.0}


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/advisory/"):
                return self._send(404, "text/plain", b"not found")
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(400, "application/json", b'{"error":"bad json"}')
            code, out = approval_api(path.rsplit("/", 1)[-1], body)
            self._send(code, "application/json", json.dumps(out).encode())

        def do_GET(self):
            if self.path.startswith("/static/"):
                rel = os.path.normpath(urlparse(self.path).path[len("/static/"):]).replace("\\", "/")
                full = os.path.join(HERE, "static", rel)
                if rel.startswith("..") or not os.path.isfile(full):
                    return self._send(404, "text/plain", b"not found")
                ctype = {".js": "application/javascript", ".css": "text/css", ".png": "image/png"}.get(
                    os.path.splitext(full)[1], "application/octet-stream")
                with open(full, "rb") as f:
                    return self._send(200, ctype, f.read())
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "flow.html"), "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif self.path == "/api/state":
                self._send(200, "application/json", STATE.payload)
            elif self.path.startswith("/api/gauges"):
                q = parse_qs(urlparse(self.path).query)
                live = q.get("mode", ["live"])[0] != "replay"
                reg = next((r for r in load_regions() if r["id"] == q.get("region", [""])[0]), None)
                locs = load_locations(region_csv(reg)) if reg else load_locations()
                now = time.time()
                if CTX["demo"]:
                    dl, dlatest = demo_gauges(now - CTX["t0"])
                    locs, latest = (locs or dl), (dlatest if not locs else {})
                else:
                    latest = (CTX["latest"].live if live else CTX["latest"].replay) if CTX["latest"] else {}
                body = {"locations": locs is not None, "mode": "live" if live else "replay",
                        "gauges": build_gauges(locs, latest, live, now, CTX["stale_s"]) if locs else []}
                self._send(200, "application/json", json.dumps(body).encode())
            elif self.path.startswith("/api/regions"):
                q = parse_qs(urlparse(self.path).query)
                live = q.get("mode", ["live"])[0] != "replay"
                latest = {} if CTX["demo"] or not CTX["latest"] else (CTX["latest"].live if live else CTX["latest"].replay)
                self._send(200, "application/json", json.dumps({"mode": "live" if live else "replay",
                           "regions": regions_summary(load_regions(), latest, live, time.time(), CTX["stale_s"])}).encode())
            elif self.path == "/api/advisories":
                self._send(200, "application/json", json.dumps(advisories_api()).encode())
            elif self.path == "/api/db":
                self._send(200, "application/json", json.dumps(demo_db() if CTX["demo"] else probe_db()).encode())
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seen = 0
                try:
                    while True:
                        with STATE.cond:
                            STATE.cond.wait_for(lambda: STATE.seq != seen, timeout=15)
                            seen, data = STATE.seq, STATE.payload
                        self.wfile.write(b"data: " + data + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            else:
                self._send(404, "text/plain", b"not found")
    return H


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--interval", type=float, default=2.0, help="seconds between Kafka probes")
    p.add_argument("--demo", action="store_true", help="scripted fake data instead of Kafka")
    p.add_argument("--live-stale-s", type=float, default=900.0,
                   help="live producer counts as silent after this many seconds without a message")
    p.add_argument("--lag-warn", type=int, default=500, help="consumer lag that turns a node amber")
    a = p.parse_args(argv)

    probe = None if a.demo else KafkaProbe()
    log, t0 = EventLog(), time.time()
    CTX.update(demo=a.demo, t0=t0)
    if not a.demo:
        CTX["latest"] = LatestReadings()
        threading.Thread(target=CTX["latest"].run, daemon=True).start()

    def loop():
        while True:
            try:
                raw = demo_raw(time.time(), t0) if a.demo else probe.snapshot()
                view = build_view(raw, a.live_stale_s, a.lag_warn)
                view["events"] = log.update(view)
                view["demo"] = a.demo
                STATE.publish(view)
            except Exception as e:      # the monitor must outlive any single bad probe
                STATE.publish({"error": str(e)[:200], "ts": time.time()})
            time.sleep(a.interval)

    threading.Thread(target=loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler())
    print(f"flow monitor on http://127.0.0.1:{a.port}  ({'demo data' if a.demo else config.KAFKA_BOOTSTRAP})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
