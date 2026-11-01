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

Opens on a landing page that moves into the globe. Tabs: Pipeline flow (above) | Map (globe and flat world map: every
gauge's newest stage against its official flood levels, live or replay) | Readings and alerts (latest windows and alerts
from PostgreSQL) | Metrics (throughput, lag, latency waterfall) | Agents (graph and the last run from agent_events).
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
import re
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from common import config, nwps
from common.envfile import load_env

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
    ("e_rain_faust", "t_rain", "faust", "t_rain", False),
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
    metrics = {"topics": [{"name": t, "rate": round(v.get("rate", 0.0), 3), "total": v.get("total", 0),
                           "age_s": (now - v["last_ts"]) if v.get("last_ts") else None} for t, v in sorted(topics.items())],
               "groups": [{"id": g, "members": v.get("members", 0), "state": v.get("state", ""), "lag": v.get("lag", 0)}
                          for g, v in sorted(groups.items())]}
    return {"ts": now, "nodes": nodes, "edges": edges, "cluster": cluster, "metrics": metrics}


class EventLog:
    """Remembers the last status of every node and records each change."""

    def __init__(self, keep=60):
        self.last, self.events, self.keep = {}, [], keep

    def note(self, node: str, what: str, result: str):
        """An action from the control panel, in the same log as the status changes."""
        self.events.insert(0, {"ts": time.time(), "node": node, "from": what, "to": result})
        del self.events[self.keep:]

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
        """Group status and lag. A failed admin call (a broker just went away) keeps the last known values and gets a new
        client next cycle, so a running group never reads as "not running"; a group really gone is absent from the listing."""
        from confluent_kafka import ConsumerGroupTopicPartitions
        wanted = {config.GROUP_WINDOWING, config.GROUP_FORECASTER, config.GROUP_SINK,
                  config.GROUP_AGENT, config.GROUP_NOTIFIER}
        last = getattr(self, "last_groups", {})
        try:
            listing = self.admin.list_consumer_groups(request_timeout=5).result()
        except Exception:
            self._reset_admin()
            return dict(last)
        ids = [g.group_id for g in listing.valid if g.group_id in wanted]
        out, failed = {}, False
        if ids:
            try:
                futures = self.admin.describe_consumer_groups(ids, request_timeout=5)
            except Exception:
                futures, failed = {}, True
            for gid in ids:
                try:
                    d = futures[gid].result()
                    out[gid] = {"state": getattr(d.state, "name", str(d.state)), "members": len(d.members),
                                "lag": last.get(gid, {}).get("lag", 0)}
                except Exception:
                    failed = True
                    if gid in last:
                        out[gid] = last[gid]
                    continue
                try:
                    offs = self.admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(gid)],
                                                                  request_timeout=5)[gid].result()
                    lag = 0
                    for tp in offs.topic_partitions:
                        hi = highs.get((tp.topic, tp.partition))
                        if hi is not None and tp.offset is not None and tp.offset >= 0:
                            lag += max(0, hi - tp.offset)
                    out[gid]["lag"] = lag
                except Exception:
                    failed = True
        if failed:
            self._reset_admin()
        self.last_groups = out
        return out

    def _reset_admin(self):
        from confluent_kafka.admin import AdminClient
        self.admin = AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "socket.timeout.ms": 4000})


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
    another area; fields: id, name, provider, lat, lon, gauges_csv (relative to the repo root), enabled, planned (shown
    as a hollow marker: nothing is monitored there yet)."""
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
                    "n_flooding": sum(1 for g in gs if sev.get(g["status"], 0) >= 3), "worst": worst,
                    "planned": bool(r.get("planned"))})
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


WATERFALL_STAGES = [("feed_delay_s", "Feed delay"), ("ingest_s", "Ingest"), ("windowing_s", "Windowing"),
                    ("forecast_s", "Forecast"), ("alert_s", "Alert"), ("human_s", "Human approval"), ("delivery_s", "Delivery"),
                    ("net_total_s", "Total, net of feed delay"), ("gross_total_s", "Total, gross")]


def metrics_api() -> dict:
    """Latency per pipeline stage from the latency_waterfall view, live and replay kept apart: median, p95 and how many
    traces have that stage measured (a stage nobody measured stays n = 0, never 0 seconds). Only instrumented traces
    count; how many older ones were left out is reported. Feed delay does not apply to a replay. Also agent metrics and
    every fault drill, failed ones included."""
    if CTX["demo"]:
        return {"ok": True, "waterfall": []}
    try:
        conn = pg_conn()
        try:
            out = []
            with conn.cursor() as cur:
                cur.execute("SELECT mode, count(*) FILTER (WHERE instrumented), count(*) FILTER (WHERE NOT instrumented) "
                            "FROM latency_waterfall GROUP BY mode ORDER BY mode")
                for mode, traces, older in cur.fetchall():
                    stages = []
                    for col, label in WATERFALL_STAGES:      # column names come from the constant above, never from a request
                        cur.execute(f"SELECT count({col}), percentile_cont(0.5) WITHIN GROUP (ORDER BY {col}), "
                                    f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {col}) FROM latency_waterfall "
                                    "WHERE mode = %s AND instrumented", (mode,))
                        n, med, p95 = cur.fetchone()
                        stages.append({"id": col, "label": label, "n": n, "median_s": med, "p95_s": p95,
                                       "applies": mode == "LIVE" or col != "feed_delay_s"})
                    out.append({"mode": mode, "traces": traces, "older_traces": older, "stages": stages})
                return {"ok": True, "waterfall": out, "agents": agent_metrics(cur), "drills": drill_rows(cur), "detection": detection_rows(cur), "langsmith": langsmith_status()}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": (str(e).strip().splitlines() or ["error"])[0][:120]}


def lead_times(notes: str | None) -> tuple:
    """(gross, net) hours from an evaluation row's notes; None when the row has none."""
    def one(k):
        m = re.search(rf"{k}=(-?[0-9.]+)", notes or "")
        return float(m.group(1)) if m else None
    return one("lead_gross_h"), one("lead_net_h")


def detection_rows(cur) -> dict:
    """Pooled detection per run, model and horizon from evaluation_runs, plus the model the forecaster has active and last used."""
    cur.execute("""SELECT data_source, run_label, model, horizon_h, precision, recall, f1, false_alarms, notes FROM evaluation_runs
                   WHERE site_no = 'POOLED' AND run_label NOT LIKE 'fault_drill:%%' ORDER BY run_label, model, horizon_h""")
    rows = [{"data_source": s, "run": r, "model": m, "horizon_h": h, "precision": p, "recall": c, "f1": f, "false_alarms": fa,
             "lead_gross_h": lead_times(n)[0], "lead_net_h": lead_times(n)[1]} for s, r, m, h, p, c, f, fa, n in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ON (mode) mode, model, horizon_h, window_start FROM risk_scores ORDER BY mode, computed_at DESC NULLS LAST""")
    used = [{"mode": m, "model": mo, "horizon_h": h, "window_start": w.isoformat()} for m, mo, h, w in cur.fetchall()]
    try:
        with open(Path(__file__).resolve().parent.parent / "ml" / "models" / "active.json", encoding="utf-8") as f:
            active = json.load(f)
    except (OSError, ValueError):
        active = None
    return {"rows": rows, "used": used, "active": active}


def langsmith_status() -> dict:
    """Whether agent runs are traced to LangSmith (never the key). Tracing is optional and lives in the agents' own process."""
    load_env()
    on = os.getenv("LANGSMITH_TRACING", "").strip().lower() in ("1", "true", "yes", "on") and bool(os.getenv("LANGSMITH_API_KEY"))
    return {"enabled": on, "project": (os.getenv("LANGSMITH_PROJECT") or "dews-agents") if on else None}


def agent_metrics(cur) -> list[dict]:
    """Per mode: runs, duration, reviewer pass rate, revisions, disagreements, tokens, v1 -> first agent draft, time per agent."""
    cur.execute("""WITH s AS (SELECT r.*, COALESCE(a.mode, 'no alert row') AS mode FROM agent_run_summary r LEFT JOIN alerts a USING (alert_id))
                   SELECT mode, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_s), percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_s),
                          count(*) FILTER (WHERE last_verdict = 'pass'), count(*) FILTER (WHERE last_verdict IS NOT NULL), avg(review_rounds)
                   FROM s GROUP BY mode ORDER BY mode""")
    out = [{"mode": m, "runs": n, "median_s": _num(med), "p95_s": _num(p95), "passed": ok, "judged": judged, "avg_review_rounds": _num(rounds)}
           for m, n, med, p95, ok, judged, rounds in cur.fetchall()]
    for row in out:
        mode = row["mode"]
        cur.execute("""SELECT count(*) FILTER (WHERE e.kind = 'revision'), count(*) FILTER (WHERE e.kind = 'disagreement'), count(*) FILTER (WHERE e.kind = 'tool_call'),
                              COALESCE(sum((e.detail->>'tokens')::numeric) FILTER (WHERE e.kind = 'end'), 0)
                       FROM agent_events e LEFT JOIN alerts a USING (alert_id) WHERE COALESCE(a.mode, 'no alert row') = %s""", (mode,))
        row["revisions"], row["disagreements"], row["tool_calls"], tokens = cur.fetchone()
        row["tokens_per_run"] = round(float(tokens) / row["runs"]) if row["runs"] else None
        cur.execute("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (v2.created_at - v1.created_at))), count(*)
                       FROM advisories v2 JOIN advisories v1 ON v1.alert_id = v2.alert_id AND v1.version = 1 JOIN alerts a ON a.alert_id = v2.alert_id
                       WHERE v2.version = 2 AND a.mode = %s""", (mode,))
        v, n = cur.fetchone()
        row["v1_to_v2_s"], row["v1_to_v2_n"] = _num(v), n
        cur.execute("""SELECT agent, percentile_cont(0.5) WITHIN GROUP (ORDER BY ms), percentile_cont(0.5) WITHIN GROUP (ORDER BY tokens), count(*) FROM (
                         SELECT e.run_id, e.agent, sum(COALESCE((e.detail->>'ms')::numeric, 0)) AS ms, sum(COALESCE((e.detail->>'tokens')::numeric, 0)) AS tokens
                         FROM agent_events e LEFT JOIN alerts a USING (alert_id) WHERE e.kind = 'end' AND COALESCE(a.mode, 'no alert row') = %s GROUP BY 1, 2) x
                       GROUP BY agent ORDER BY 2 DESC""", (mode,))
        row["per_agent"] = [{"agent": a, "median_ms": _num(ms), "median_tokens": _num(t), "runs": c} for a, ms, t, c in cur.fetchall()]
    return out


def drill_rows(cur) -> list[dict]:
    cur.execute("SELECT run_label, status, recovery_time_s, failure_reason, data_source, created_at FROM evaluation_runs "
                "WHERE run_label LIKE 'fault_drill:%%' ORDER BY created_at DESC, run_id DESC LIMIT 40")
    return [{"scenario": lb.split(":", 1)[1], "status": st, "recovery_s": rec, "reason": why, "data_source": src, "at": at.isoformat()}
            for lb, st, rec, why, src, at in cur.fetchall()]


def _num(v):
    return None if v is None else float(v)


def timeline_events(times: list[float], alerts: list[float] = (), gap_h: float = 6.0, keep: int = 20) -> list[dict]:
    """Ascending window starts that have readings -> contiguous stretches, newest first, with how many alerts each holds."""
    runs: list[list[float]] = []
    for t in times:
        if runs and t - runs[-1][1] <= gap_h * 3600:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    return [{"start": a, "end": b + 3600, "alerts": sum(1 for x in alerts if a <= x <= b + 3600)} for a, b in reversed(runs)][:keep]


def timeline_series(rows: list[tuple]) -> dict:
    """(site_no, window_start epoch, stage_last, n_readings, rain_3h) ascending -> site_no: [[t, stage, n, rain_3h], ...]; a window with no stage is left out."""
    out: dict[str, list] = {}
    for site, t, stage, n, rain in rows:
        if stage is not None:
            out.setdefault(site, []).append([t, round(float(stage), 3), n, None if rain is None else round(float(rain), 2)])
    return out


def rain_summary(rows: list[tuple]) -> dict:
    """(site_no, hour_end epoch, rain_mm) newest first per site -> site_no: newest hour, and the sums over the hours ending then.
    A sum with an hour missing is null, as in the windows."""
    out: dict[str, dict] = {}
    by: dict[str, dict] = {}
    for site, t, mm in rows:
        by.setdefault(site, {})[int(t)] = mm
    for site, hours in by.items():
        end = max(hours)
        def total(n):
            vals = [hours.get(end - k * 3600) for k in range(n)]
            return None if any(v is None for v in vals) else round(sum(vals), 2)
        out[site] = {"hour_end": end, "rain_1h": total(1), "rain_3h": total(3), "rain_6h": total(6)}
    return out


def rain_api(mode: str) -> dict:
    """Newest catchment rain per gauge for the Map's rain layer; live and replay kept apart."""
    if CTX["demo"]:
        return {"ok": True, "rain": {}}
    db_mode = "SIMULATION" if mode == "replay" else "LIVE"
    try:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT site_no, EXTRACT(EPOCH FROM window_end), rain_mm FROM catchment_rain c
                               WHERE mode = %s AND window_end > (SELECT max(window_end) - interval '6 hours' FROM catchment_rain x
                                                                 WHERE x.site_no = c.site_no AND x.mode = c.mode)""", (db_mode,))
                return {"ok": True, "mode": db_mode, "rain": rain_summary([(s, float(t), m) for s, t, m in cur.fetchall()])}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": (str(e).strip().splitlines() or ["error"])[0][:120]}


TIMELINE_MAX_S = 21 * 86400


def timeline_api(mode: str, start: float | None, end: float | None) -> dict:
    """Past windows from PostgreSQL for the map's scrubber. Without a range: the stretches of data to choose from, and the newest one."""
    if CTX["demo"]:
        return {"ok": True, "events": [], "series": {}, "alerts": []}
    db_mode = "SIMULATION" if mode == "replay" else "LIVE"
    try:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT alert_id, site_no, category, status, EXTRACT(EPOCH FROM COALESCE((details->>'window_start')::timestamptz,
                               split_part(alert_id, '|', 3)::timestamptz)) FROM alerts WHERE mode = %s ORDER BY 5""", (db_mode,))
                alerts = [{"alert_id": i, "site_no": s, "category": c, "status": st, "t": float(t)} for i, s, c, st, t in cur.fetchall()]
                cur.execute("SELECT DISTINCT EXTRACT(EPOCH FROM window_start) FROM windowed_aggregates WHERE mode = %s AND n_readings > 0 ORDER BY 1", (db_mode,))
                events = timeline_events([float(r[0]) for r in cur.fetchall()], [a["t"] for a in alerts])
                if start is None or end is None:
                    if not events:
                        return {"ok": True, "mode": db_mode, "events": [], "series": {}, "alerts": []}
                    pick = next((e for e in events if e["alerts"]), events[0])
                    start, end = pick["start"], pick["end"]
                start = max(start, end - TIMELINE_MAX_S)
                cur.execute("""SELECT site_no, EXTRACT(EPOCH FROM window_start), stage_last, n_readings, rain_3h FROM windowed_aggregates
                               WHERE mode = %s AND window_start >= to_timestamp(%s) AND window_start < to_timestamp(%s) ORDER BY 2""", (db_mode, start, end))
                rows = [(s, float(t), st, n, r) for s, t, st, n, r in cur.fetchall()]
            return {"ok": True, "mode": db_mode, "start": start, "end": end, "events": events, "series": timeline_series(rows),
                    "alerts": [a for a in alerts if start <= a["t"] <= end]}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": (str(e).strip().splitlines() or ["error"])[0][:120]}


def safe_trace_url(url: str | None) -> str | None:
    """Only a LangSmith address becomes a link on the page."""
    return url if isinstance(url, str) and url.startswith("https://smith.langchain.com/") else None


def agent_run_view(events: list[dict]) -> dict:
    """Events of one run -> per agent: visited, time taken (from `end` events), tool calls, tokens, last verdict and attempts.
    `open` = started and not ended yet: that agent is running now, if the run is still recent."""
    agents: dict[str, dict] = {}
    for e in events:
        a = agents.setdefault(e["agent"], {"agent": e["agent"], "starts": 0, "ends": 0, "ms": 0, "tool_calls": 0, "tokens": 0, "verdict": None})
        d = e.get("detail") or {}
        if e["kind"] == "start":
            a["starts"] += 1
        elif e["kind"] == "end":
            a["ends"] += 1
            a["ms"] += d.get("ms") or 0
            a["tokens"] += d.get("tokens") or 0
        elif e["kind"] == "tool_call":
            a["tool_calls"] += 1
        elif e["kind"] == "verdict":
            a["verdict"] = d.get("verdict")
    for a in agents.values():
        a["open"] = a["starts"] > a["ends"]
    return {"agents": list(agents.values()), "disagreements": sum(1 for e in events if e["kind"] == "disagreement"),
            "revisions": sum(1 for e in events if e["kind"] == "revision")}


ACTIVE_RUN_S = 120      # a run with an agent still open but silent for longer than this crashed; it is not shown as running


def agents_api(run_id: str | None = None) -> dict:
    """The newest run in agent_events (or `run_id`), without the writer's token stream (read-only), and the recent runs to pick from."""
    if CTX["demo"]:
        return {"ok": True, "run": None, "langsmith": langsmith_status()}
    try:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id, alert_id, started_at, ended_at, duration_s, events, tool_calls, review_rounds, last_verdict "
                            "FROM agent_run_summary WHERE %s::text IS NULL OR run_id = %s ORDER BY ended_at DESC LIMIT 1", (run_id, run_id))
                row = cur.fetchone()
                if not row:
                    return {"ok": True, "run": None, "langsmith": langsmith_status()}
                cols = [d[0] for d in cur.description]
                run = {k: (v.isoformat() if hasattr(v, "isoformat") else (float(v) if k == "duration_s" and v is not None else v)) for k, v in zip(cols, row)}
                cur.execute("SELECT mode FROM alerts WHERE alert_id = %s", (run["alert_id"],))
                m = cur.fetchone()
                run["mode"] = m[0] if m else None
                cur.execute("SELECT agent, kind, detail, ts FROM agent_events WHERE run_id = %s ORDER BY event_id", (run["run_id"],))
                events = [{"agent": a, "kind": k, "detail": d, "ts": t.isoformat()} for a, k, d, t in cur.fetchall()]
                cur.execute("SELECT version, source, status FROM advisories WHERE alert_id = %s ORDER BY version DESC LIMIT 1", (run["alert_id"],))
                adv = cur.fetchone()
                cur.execute("SELECT EXTRACT(EPOCH FROM (now() - max(ts))) FROM agent_events WHERE run_id = %s", (run["run_id"],))
                age = float(cur.fetchone()[0] or 0)
                cur.execute("SELECT run_id, alert_id, ended_at FROM agent_run_summary ORDER BY ended_at DESC LIMIT 20")
                runs = [{"run_id": r, "alert_id": a, "ended_at": e.isoformat()} for r, a, e in cur.fetchall()]
                cur.execute("SELECT trace_url FROM agent_runs WHERE run_id = %s AND trace_url IS NOT NULL LIMIT 1", (run["run_id"],))
                tr = cur.fetchone()
                run["trace_url"] = safe_trace_url(tr[0]) if tr else None
            view = agent_run_view(events)
            return {"ok": True, "run": run, "runs": runs, **view, "active": age < ACTIVE_RUN_S and any(a["open"] for a in view["agents"]), "langsmith": langsmith_status(),
                    "advisory": {"version": adv[0], "source": adv[1], "status": adv[2]} if adv else None,
                    "events": [e for e in events if e["kind"] != "token_chunk"][-400:]}
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
            if path == "/api/control":
                from dashboard import control
                if CTX["demo"] or not control.origin_ok(self.headers, self.server.server_address[1]):
                    return self._send(403, "application/json", b'{"error":"not allowed"}')
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    return self._send(400, "application/json", b'{"error":"bad json"}')
                code, out = CTX["control"].act(body.get("action"), body.get("target"))
                return self._send(code, "application/json", json.dumps(out).encode())
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
                ctype = {".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg"}.get(
                    os.path.splitext(full)[1], "application/octet-stream")
                with open(full, "rb") as f:
                    return self._send(200, ctype, f.read())
            if urlparse(self.path).path in ("/", "/index.html"):
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
            elif self.path == "/api/metrics":
                self._send(200, "application/json", json.dumps(metrics_api()).encode())
            elif self.path == "/api/control":
                self._send(200, "application/json", json.dumps(CTX["control"].status() if CTX.get("control") else {"ok": False, "error": "not available with demo data"}).encode())
            elif self.path.startswith("/api/rain"):
                self._send(200, "application/json", json.dumps(rain_api(parse_qs(urlparse(self.path).query).get("mode", ["live"])[0])).encode())
            elif self.path.startswith("/api/timeline"):
                q = parse_qs(urlparse(self.path).query)
                num = lambda k: float(q[k][0]) if q.get(k) else None  # noqa: E731
                self._send(200, "application/json", json.dumps(timeline_api(q.get("mode", ["live"])[0], num("start"), num("end"))).encode())
            elif self.path.startswith("/api/agents"):
                run = parse_qs(urlparse(self.path).query).get("run", [None])[0]
                self._send(200, "application/json", json.dumps(agents_api(run), default=str).encode())
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
    if not a.demo:
        from dashboard.control import Control
        CTX["control"] = Control(log.note)
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
