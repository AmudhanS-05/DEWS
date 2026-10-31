"""Console feedback for commands run by hand: a start line, a progress bar, elapsed time and a final summary.

    with Task("Faust kill drill", total=120) as t:
        t.update(done=30, note="killed worker 6068")
        ...
        t.summary("PASS: recovered in 41 s")
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

BAR_WIDTH = 30


def bar(done: float, total: float, width: int = BAR_WIDTH) -> str:
    frac = 1.0 if total <= 0 else min(max(done / total, 0.0), 1.0)
    filled = int(round(frac * width))
    return f"[{'#' * filled}{'.' * (width - filled)}] {frac * 100:3.0f}%"


class Task:
    def __init__(self, name: str, total: float = 0, unit: str = "", stream=None):
        self.name, self.total, self.unit = name, total, unit
        self.out = stream or sys.stdout
        self.t0 = time.monotonic()
        self.done = 0.0
        self._summary: str | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def __enter__(self):
        print(f"[start {datetime.now():%H:%M:%S}] {self.name}", file=self.out, flush=True)
        return self

    def update(self, done: float | None = None, note: str = ""):
        if done is not None:
            self.done = done
        tail = f"  {note}" if note else ""
        count = f" {self.done:g}/{self.total:g}{self.unit}" if self.total else ""
        print(f"  {bar(self.done, self.total)}{count}  {self.elapsed():5.1f}s{tail}", file=self.out, flush=True)

    def summary(self, text: str):
        self._summary = text

    def __exit__(self, exc_type, exc, tb):
        status = self._summary or ("FAILED: " + repr(exc) if exc else "done")
        print(f"[done {datetime.now():%H:%M:%S}] {self.name}: {status} (elapsed {self.elapsed():.1f}s)",
              file=self.out, flush=True)
        return False
