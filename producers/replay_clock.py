"""Shared replay clock: one data-time clock that the gauge and rain replay producers both follow.

The console's Control tab owns the file. It holds an anchor {"d0": data time, "w0": wall time, "speed": data s per wall s},
so data time now = d0 + (wall now - w0) * speed. Changing the speed re-anchors at the current data time; skipping ahead sets
d0 to the later time (the producers then send everything up to it at once: nothing is dropped, and a replay never goes back).
Speed 0 holds the clock.

The rain producer also writes how far it has published (`write_progress`). A gauge replay started with --wait-rain sends a
reading only once the rain of its hour is out, so a window never closes before its rain arrives, at any speed.
"""
from __future__ import annotations

import json
import math
import os
import time

RAIN_LEAD_S = 3600          # rain of the hour ending at h is sent when the clock reaches h - 1 h, ahead of its gauge window


def _write(path, obj) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    for k in range(20):                      # Windows: a reader holding the file open makes the replace fail for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (k + 1))
    os.remove(tmp)


def read(path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else None
    except (OSError, ValueError):
        return None


def write(path, d0: float, speed: float, w0: float | None = None, **extra) -> dict:
    c = {"d0": float(d0), "w0": time.time() if w0 is None else float(w0), "speed": max(float(speed), 0.0), **extra}
    _write(path, c)
    return c


def now_of(c: dict, t: float | None = None) -> float:
    return c["d0"] + max((time.time() if t is None else t) - c["w0"], 0.0) * c["speed"]


class Clock:
    """Data time as the file says, re-read at most every 0.25 s. Without a file: the fallback anchor (the old fixed pace)."""

    def __init__(self, path, d0: float, speed: float):
        self.path, self.fallback = path, {"d0": d0, "w0": time.time(), "speed": speed}
        self._c, self._at = None, 0.0

    def state(self) -> dict:
        if time.monotonic() - self._at > 0.25:
            self._c = read(self.path) or self._c or self.fallback
            self._at = time.monotonic()
        return self._c

    def now(self) -> float:
        return now_of(self.state())

    def wait_until(self, ts: float, sleep=time.sleep) -> None:
        while True:
            c = self.state()
            n = now_of(c)
            if n >= ts:
                return
            sleep(0.25 if c["speed"] <= 0 else min(0.25, (ts - n) / c["speed"]))


def write_progress(path, hour_end: float | None, done: bool = False) -> None:
    _write(path, {"at": hour_end, "done": done, "w": time.time()})


def wait_rain(path, ts: float, sleep=time.sleep, give_up_s: float = 90.0) -> bool:
    """Block until the rain of the hour holding reading `ts` is published (or the rain replay is done). False if the rain
    producer stopped reporting for give_up_s: then the gauges carry on without it."""
    need, t0 = math.ceil(ts / 3600.0) * 3600.0, time.time()
    while True:
        r = read(path)
        if r and (r.get("done") or (r.get("at") or 0) >= need):
            return True
        if time.time() - max(t0, (r or {}).get("w", 0)) > give_up_s:
            return False
        sleep(0.25)
