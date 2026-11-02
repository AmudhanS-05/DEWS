"""Demo control panel: fixed actions only, each one an existing script. No free-text command reaches a process.

    POST /api/control  {"action": "start" | "stop" | "replay" | "reset" | "drill", "target": <an id from the lists below>}

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
    "drwt2_2019": {"label": "Too fast to warn: Onion Creek (DRWT2), 2 to 6 May 2019, no radar rain", "sites": ["08158700"], "start": "2019-05-02", "end": "2019-05-06", "speed": 3600, "rain": False},
}
DRILLS = {"faust": "Kill a Faust worker", "broker": "Stop a Kafka broker", "sink": "Kill the sink", "postgres": "Stop PostgreSQL",
          "notifier": "Kill the notifier", "producer": "Kill the live producer", "llm": "LLM down"}
RAIN_HEAD_S = 15     # a window takes only the rain already in when it closes; the rain producer starts slower, so it goes first
SLICES = {f"S{n}": f"verify_slice S{n}" for n in range(1, 6)}
ACTIONS = {"start": PROCESSES, "stop": PROCESSES, "replay": FLOODS, "reset": FLOODS, "agents": FLOODS, "drill": DRILLS, "verify": SLICES}


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
    if action == "verify":
        return [[py, "-m", "tools.verify_slice", target]]
    f = FLOODS[target]
    if action == "reset":
        return [[py, "-m", "tools.reset_replay", "--site", ",".join(f["sites"]), "--start", f["start"], "--end", f["end"], "--yes"]]
    cmds = [[py, "-m", "producers.replay_producer", "--site", s, "--start", f["start"], "--end", f["end"], "--speed", str(f["speed"])] for s in f["sites"]]
    if f["rain"]:
        cmds.insert(0, [py, "-m", "producers.mrms_qpe_producer", "--replay", "--start", f["start"], "--end", f["end"], "--speed", str(f["speed"])])
    return cmds


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
                "floods": [{"id": k, "label": v["label"]} for k, v in FLOODS.items()], "drills": [{"id": k, "label": v} for k, v in DRILLS.items()], "slices": list(SLICES), "job": self._job_view()}

    def _job_view(self):
        j = self.job
        if not j:
            return None
        codes = [p.poll() for p in j["procs"]]
        return {"label": j["label"], "running": any(c is None for c in codes), "ok": all(c == 0 for c in codes), "started": j["started"], "elapsed_s": round((j.get("ended") or time.time()) - j["started"], 1), "log": j["log"], "result": _last_line(ROOT / j["log"])}

    def act(self, action, target) -> tuple[int, dict]:
        why = allowed(action, target)
        if why:
            return 400, {"error": why}
        if action in ("start", "stop"):
            return self._process(action, target)
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
                    return 409, {"error": f"{', '.join(done)} already replayed since the last reset: press Reset first, or these readings go to the dead-letter topic"}
            if action == "agents":
                ids = _flood_alerts(spec)
                if not ids:
                    return 409, {"error": "no standing alert for this flood: replay it first"}
                cmds = [[sys.executable, "-m", "agentic_ai.run", "--alert-id", i] for i in ids]
            else:
                cmds = job_commands(action, target, sys.executable)
            first = 1 if action == "replay" and FLOODS[target]["rain"] else len(cmds)
            self.job = {"label": label, "started": time.time(), "log": f"gate_output/control_{name}_0.log",
                        "procs": [spawn(c, LOGS / f"control_{name}_{i}.log") for i, c in enumerate(cmds[:first])]}
            self.note("Control panel", label, "started")
            later = [(c, LOGS / f"control_{name}_{i + first}.log") for i, c in enumerate(cmds[first:])]
            threading.Thread(target=self._watch, args=(self.job, later), daemon=True).start()
        return 200, {"ok": True, "label": label}

    def _watch(self, job: dict, later: list | None = None) -> None:
        from tools.fault_drill import spawn
        if later:
            time.sleep(RAIN_HEAD_S)
            job["procs"].extend(spawn(c, log) for c, log in later)
        for p in job["procs"]:
            p.wait()
        job["ended"] = time.time()
        self.note("Control panel", job["label"], "ok finished" if all(p.returncode == 0 for p in job["procs"]) else "down failed, see the log")
