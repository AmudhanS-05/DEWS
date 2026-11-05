"""Demo control panel: fixed actions only, each one an existing script. No free-text command reaches a process.

    POST /api/control  {"action": "start" | "stop" | "replay" | "reset" | "drill" | "halt", "target": <an id from the lists below>}
    POST /api/replay_clock  {"speed": one of SPEEDS} or {"seek": data time, epoch s}: the running replay's pace, or skip ahead

The console binds to 127.0.0.1. A request must also carry the X-DEWS-Control header and, if it has an Origin, come from this
console: a web page elsewhere cannot send that, so it cannot press these buttons. Nothing here runs docker compose; the broker and
PostgreSQL drills stop one container and start it again. The console logs every action in its status log.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "gate_output"
PROCESSES = {
    "faust_6066": {"label": "Faust worker 6066", "port": 6066},
    "faust_6067": {"label": "Faust worker 6067", "port": 6067},
    "faust_6068": {"label": "Faust worker 6068", "port": 6068},
    "sink": {"label": "Postgres sink", "module": "storage.sink"},
    "forecaster": {"label": "Forecaster", "module": "streams.forecaster"},
    "notifier": {"label": "Notifier", "module": "alerting.notifier"},
    "agents": {"label": "Agents consumer (from now on, calls the LLM on each new alert)", "module": "agentic_ai.run"},
    "usgs": {"label": "USGS live producer", "module": "producers.usgs_iv_producer"},
    "mrms": {"label": "MRMS live rain producer", "module": "producers.mrms_qpe_producer"},
}
FLOODS = {
    "oct_2023": {"label": "Main scene: Shoal Creek (AHOT2) and Llano (LLAT2), 25 to 27 Oct 2023, with radar rain", "sites": ["08156800", "08151500"], "start": "2023-10-25", "end": "2023-10-27", "speed": 1800, "rain": True},
}
DRILLS = {"faust": "Kill a Faust worker", "broker": "Stop a Kafka broker", "sink": "Kill the sink"}
RAIN_HEAD_S = 15     # a window takes only the rain already in when it closes; the rain producer starts slower, so it goes first
ACTIONS = {"start": PROCESSES, "stop": PROCESSES, "replay": FLOODS, "reset": FLOODS, "agents": FLOODS, "drill": DRILLS,
           "halt": {"job": "Stop the running replay or drill"}}
DRILL_KILL_S = 20                      # tools.fault_drill --kill-after default
DRILL_DOWN_S = {"faust": 70, "broker": 30, "sink": 60}   # its per-scenario --down defaults
CLOCK = LOGS / "replay_clock.json"     # the shared replay clock (producers/replay_clock.py)
RAIN_AT = LOGS / "replay_rain_at.json"
DRILL_STATE = LOGS / "drill_state.json"
SPEEDS = [60, 120, 300, 900, 1800, 3600, 7200, 14400]   # data seconds per real second: 1 min/s .. 4 h/s
AUTO_SLOW = 120                       # auto: 2 min of data per second near a flood, so a 3 h alert lead lasts 90 s
AUTO_BEFORE_S, AUTO_AFTER_S = 4 * 3600, 3600   # ... from 4 h before each official crossing to 1 h after it


def allowed(action, target) -> str | None:
    """None if this is one of the fixed actions, else why not."""
    if not isinstance(action, str) or action not in ACTIONS:
        return "unknown action"
    if not isinstance(target, str) or target not in ACTIONS[action]:
        return "unknown target"
    return None


def origin_ok(headers, port: int) -> bool:
    """Our own page only: the custom header cannot be sent cross-site without a preflight, which this server never answers."""
    if headers.get("X-DEWS-Control") != "1":
        return False
    origin = headers.get("Origin")
    return origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def job_commands(action: str, target: str, py: str) -> list[list[str]]:
    """The argument lists for a replay, reset or drill. Every value comes from the tables above."""
    if action == "drill":
        return [[py, "-m", "tools.fault_drill", "--scenario", target]]
    f = FLOODS[target]
    if action == "reset":
        return [[py, "-m", "tools.reset_replay", "--site", ",".join(f["sites"]), "--start", f["start"], "--end", f["end"], "--yes"]]
    wait = ["--wait-rain", str(RAIN_AT)] if f["rain"] else []
    cmds = [[py, "-m", "producers.replay_producer", "--site", s, "--start", f["start"], "--end", f["end"], "--speed", str(f["speed"]),
             "--clock", str(CLOCK), *wait] for s in f["sites"]]
    if f["rain"]:
        cmds.insert(0, [py, "-m", "producers.mrms_qpe_producer", "--replay", "--start", f["start"], "--end", f["end"], "--speed", str(f["speed"]),
                        "--clock", str(CLOCK), "--progress", str(RAIN_AT)])
    return cmds


def flood_range(f: dict) -> tuple[float, float]:
    import datetime as dt
    t0 = dt.datetime.fromisoformat(f["start"] + "T00:00:00+00:00").timestamp()
    return t0, dt.datetime.fromisoformat(f["end"] + "T00:00:00+00:00").timestamp() + 86400


_CROSSINGS: dict = {}


def flood_crossings(target: str, cur=None) -> list[dict]:
    """First time each gauge of a flood reaches each official flood category (minor and up), from the cached USGS readings
    the replay sends: known before the replay reaches them."""
    if target in _CROSSINGS:
        return _CROSSINGS[target]
    from producers.replay_producer import load_readings
    f = FLOODS[target]
    q = "SELECT site_no, nwps_lid, minor_stage, moderate_stage, major_stage FROM gauges WHERE site_no = ANY(%s)"
    if cur is None:
        import psycopg2

        from common import config
        with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as c2:
            c2.execute(q, (f["sites"],))
            rows = c2.fetchall()
    else:
        cur.execute(q, (f["sites"],))
        rows = cur.fetchall()
    out = []
    for site, lid, *levels in rows:
        try:
            rs = load_readings(site, f["start"], f["end"])
        except SystemExit:
            continue
        for cat, lv in zip(("minor", "moderate", "major"), levels):
            if lv is None:
                continue
            t = next((r["reading_ts"] for r in rs if r["value"] is not None and r["value"] >= lv), None)
            if t is not None:
                out.append({"at": float(t), "kind": "flood", "cat": cat, "label": f"{cat.upper()} {lid or site}"})
    _CROSSINGS[target] = out
    return out


def auto_speed(now: float, crossings: list[dict], fast: float) -> float:
    """Auto pace: fast between floods, AUTO_SLOW from AUTO_BEFORE_S before each crossing to AUTO_AFTER_S after it."""
    near = any(m["at"] - AUTO_BEFORE_S <= now <= m["at"] + AUTO_AFTER_S for m in crossings)
    return AUTO_SLOW if near else fast


def kill_tree(p) -> None:
    """The job's process and everything it started (a drill's replay too)."""
    import os
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        p.kill()


def read_json(path: Path) -> dict | None:
    import json
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _last_line(path: Path) -> str:
    """The closing line of a job's log: its summary."""
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    except OSError:
        return ""
    return lines[-1][:400] if lines else ""


def _running_lines() -> list[str]:
    cmd = "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|faust)' } | ForEach-Object { $_.CommandLine }"
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=20).stdout.splitlines()


def is_running(spec: dict, lines: list[str]) -> bool:
    if "port" in spec:
        return any("faust_app" in ln and f"web-port {spec['port']}" in ln for ln in lines)
    return any(re.search(rf"-m {re.escape(spec['module'])}(\s|$)", ln) for ln in lines)


def _flood_alerts(flood: dict) -> list[str]:
    """Standing alerts of this flood's gauges and dates."""
    import psycopg2

    from common import config
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT alert_id FROM alerts WHERE site_no = ANY(%s) AND mode = 'SIMULATION' AND status = 'draft' AND split_part(alert_id, '|', 3) >= %s "
                    "AND split_part(alert_id, '|', 3) < %s ORDER BY 1", (flood["sites"], flood["start"], flood["end"] + "T99"))
        return [r[0] for r in cur.fetchall()]


def _agents_from_now() -> None:
    """The agent consumer would start at the earliest alert on the topic and call the LLM for each one. First start: from now on."""
    from confluent_kafka import Consumer, TopicPartition

    from common import config
    c = Consumer({"bootstrap.servers": config.KAFKA_BOOTSTRAP, "group.id": config.GROUP_AGENT, "enable.auto.commit": False})
    try:
        t = config.TOPIC_ALERTS
        tps = [TopicPartition(t, p) for p in c.list_topics(t, timeout=10).topics[t].partitions]
        fresh = [tp for tp in c.committed(tps, timeout=10) if tp.offset < 0]
        for tp in fresh:
            tp.offset = c.get_watermark_offsets(TopicPartition(t, tp.partition), timeout=10)[1]
        if fresh:
            c.commit(offsets=fresh, asynchronous=False)
    finally:
        c.close()


class Control:
    """One job (replay, reset or drill) at a time; starting and stopping a process is immediate."""

    def __init__(self, note=None):
        self.note, self.job, self.lock = note or (lambda *a: None), None, threading.Lock()

    def status(self) -> dict:
        try:
            lines = _running_lines()
        except (OSError, subprocess.SubprocessError):
            lines = []
        return {"ok": True, "processes": [{"id": k, "label": v["label"], "running": is_running(v, lines)} for k, v in PROCESSES.items()],
                "floods": [{"id": k, "label": v["label"]} for k, v in FLOODS.items()], "drills": [{"id": k, "label": v} for k, v in DRILLS.items()], "job": self._job_view()}

    def _job_view(self):
        j = self.job
        if not j:
            return None
        codes = [p.poll() for p in j["procs"]]
        return {"label": j["label"], "action": j.get("action"), "target": j.get("target"), "running": any(c is None for c in codes) and not j.get("stopped"),
                "stopped": bool(j.get("stopped")), "ok": all(c == 0 for c in codes) and not j.get("stopped"), "started": j["started"], "elapsed_s": round((j.get("ended") or time.time()) - j["started"], 1), "log": j["log"], "result": _last_line(ROOT / j["log"])}

    def act(self, action, target) -> tuple[int, dict]:
        why = allowed(action, target)
        if why:
            return 400, {"error": why}
        if action in ("start", "stop"):
            return self._process(action, target)
        if action == "halt":
            return self.halt()
        return self._start_job(action, target)

    def _process(self, action: str, target: str) -> tuple[int, dict]:
        from tools.fault_drill import find_pids, kill, module_pids, start_module, start_worker
        spec = PROCESSES[target]
        running = is_running(spec, _running_lines())
        if action == "stop":
            if running:
                kill(find_pids(spec["port"]) if "port" in spec else module_pids(spec["module"]))
        elif not running:
            if target == "agents":
                _agents_from_now()
            log = LOGS / f"{target}.log"
            start_worker(spec["port"], log) if "port" in spec else start_module(spec["module"], log)
        changed = running == (action == "stop")
        self.note(spec["label"], f"{action} requested", "done" if changed else f"already {'stopped' if action == 'stop' else 'running'}")
        return 200, {"ok": True, "changed": changed}

    def _start_job(self, action: str, target: str) -> tuple[int, dict]:
        import sys

        from tools.fault_drill import spawn
        with self.lock:
            v = self._job_view()
            if v and v["running"]:
                return 409, {"error": f"still running: {v['label']}"}
            name = f"{action}_{target}"
            spec = ACTIONS[action][target]
            label = f"{action} {spec['label'] if isinstance(spec, dict) else spec}"
            if action == "replay":
                from producers.replay_producer import already_replayed
                done = [s for s in spec["sites"] if already_replayed(s, spec["start"], spec["end"])]
                if done:
                    return 409, {"error": f"{', '.join(done)} already replayed since the last reset: press Reset first, or the pipeline drops them as too late"}
            if action == "agents":
                ids = _flood_alerts(spec)
                if not ids:
                    return 409, {"error": "no standing alert for this flood: replay it first"}
                cmds = [[sys.executable, "-m", "agentic_ai.run", "--alert-id", i] for i in ids]
            else:
                cmds = job_commands(action, target, sys.executable)
            if action == "replay":
                from producers import replay_clock
                RAIN_AT.unlink(missing_ok=True)
                replay_clock.write(CLOCK, flood_range(spec)[0], 0 if spec["rain"] else spec["speed"], target=target)   # held until the gauges start
            if action == "drill":
                DRILL_STATE.unlink(missing_ok=True)
            first = 1 if action == "replay" and FLOODS[target]["rain"] else len(cmds)
            self.job = {"label": label, "action": action, "target": target, "started": time.time(), "log": f"gate_output/control_{name}_0.log",
                        "procs": [spawn(c, LOGS / f"control_{name}_{i}.log") for i, c in enumerate(cmds[:first])]}
            self.note("Control panel", label, "started")
            later = [(c, LOGS / f"control_{name}_{i + first}.log") for i, c in enumerate(cmds[first:])]
            threading.Thread(target=self._watch, args=(self.job, later), daemon=True).start()
        return 200, {"ok": True, "label": label}

    def _watch(self, job: dict, later: list | None = None) -> None:
        from tools.fault_drill import spawn
        if later:
            time.sleep(RAIN_HEAD_S)
            if job.get("stopped"):
                return
            from producers import replay_clock
            f = FLOODS[job["target"]]
            replay_clock.write(CLOCK, flood_range(f)[0], job.get("speed") or f["speed"], target=job["target"])
            job["procs"].extend(spawn(c, log) for c, log in later)
            threading.Thread(target=self._auto, args=(job,), daemon=True).start()
        for p in job["procs"]:
            p.wait()
        if job.get("stopped"):
            return
        job["ended"] = time.time()
        self.note("Control panel", job["label"], "ok finished" if all(p.returncode == 0 for p in job["procs"]) else "down failed, see the log")

    def _auto(self, job: dict) -> None:
        """While the replay runs with speed auto: slow down near each flood, speed up between them."""
        from producers import replay_clock
        try:
            crossings = flood_crossings(job["target"])
        except Exception:  # noqa: BLE001 - no crossings known: auto stays at the flood's normal speed
            crossings = []
        fast = FLOODS[job["target"]]["speed"]
        while self.job is job and not job.get("stopped") and any(p.poll() is None for p in job["procs"]):
            if job.get("auto", True):
                with self.lock:
                    c = replay_clock.read(CLOCK)
                    if c and c["speed"] > 0 and c.get("target") == job["target"]:
                        now = replay_clock.now_of(c)
                        want = auto_speed(now, crossings, fast)
                        if want != c["speed"]:
                            replay_clock.write(CLOCK, now, want, target=job["target"])
            time.sleep(0.5)

    def clock(self) -> dict | None:
        """The running replay's clock: data time now and speed."""
        j = self.job
        if not j or j.get("action") != "replay":
            return None
        from producers import replay_clock
        c = replay_clock.read(CLOCK)
        if not c or c.get("target") != j["target"]:
            return None
        t1 = flood_range(FLOODS[j["target"]])[1]
        return {"now": min(replay_clock.now_of(c, j.get("ended")), t1), "speed": c["speed"], "speeds": SPEEDS, "auto": j.get("auto", True)}

    def set_clock(self, speed=None, seek=None) -> tuple[int, dict]:
        """Change the running replay's speed, or skip ahead (never back: those readings are already out)."""
        from producers import replay_clock
        with self.lock:
            v = self._job_view()
            if not v or not v["running"] or v["action"] != "replay":
                return 409, {"error": "no replay is running"}
            c = replay_clock.read(CLOCK)
            if not c or c.get("target") != v["target"]:
                return 409, {"error": "the replay has no clock yet"}
            t0, t1 = flood_range(FLOODS[v["target"]])
            now = replay_clock.now_of(c)
            new_speed = c["speed"]
            if speed == "auto":
                self.job["auto"] = True
                speed = None
            elif speed is not None:
                self.job["auto"] = False
            if speed is not None:
                if speed not in SPEEDS:
                    return 400, {"error": "speed must be one of " + ", ".join(map(str, SPEEDS))}
                if c["speed"] > 0:          # held while the rain head start runs: keep it held, the new speed applies after
                    new_speed = speed
                self.job["speed"] = speed
            d0 = now
            if seek is not None:
                if not isinstance(seek, (int, float)) or not t0 <= seek <= t1:
                    return 400, {"error": "seek must be a time inside the replay"}
                if seek < now:
                    return 409, {"error": "a replay cannot go back: those readings are already sent"}
                d0 = seek
            replay_clock.write(CLOCK, d0, new_speed, target=v["target"])
            self.note("Control panel", v["label"], f"speed {new_speed:g}x" + (f", skipped ahead {(d0 - now) / 3600:.1f} h" if d0 > now else ""))
            return 200, {"ok": True, "now": d0, "speed": new_speed}

    def halt(self) -> tuple[int, dict]:
        """Stop the running job mid-way. A drill also brings back what it took down."""
        with self.lock:
            j = self.job
            v = self._job_view()
            if not v or not v["running"]:
                return 409, {"error": "nothing is running"}
            j["stopped"] = True
            for p in j["procs"]:
                kill_tree(p)
            j["ended"] = time.time()
            restored = self._restore() if j["action"] == "drill" else None
            if j["action"] == "replay":
                CLOCK.unlink(missing_ok=True)
        self.note("Control panel", j["label"], "stopped" + (f", {restored}" if restored else ""))
        return 200, {"ok": True, "label": j["label"], "restored": restored}

    def _restore(self) -> str | None:
        """Restart the part a stopped drill left down (what tools.fault_drill's finally blocks would have done)."""
        import json
        st = read_json(DRILL_STATE)
        if not st or not st.get("killed_at") or st.get("restarted_at"):
            return None
        from tools.fault_drill import docker, start_module, start_worker
        node = st.get("node")
        if node == "faust":
            start_worker(int(st.get("port", 6068)), LOGS / f"faust_{st.get('port', 6068)}_drill.log")
        elif node == "sink":
            start_module("storage.sink", LOGS / "sink_drill.log")
        elif node == "broker":
            docker("start", st.get("name", "flood-kafka-2"))
        else:
            return None
        DRILL_STATE.write_text(json.dumps({**st, "restarted_at": time.time()}), encoding="utf-8")
        return f"restarted {node}"
