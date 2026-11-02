"""Progress bars for the hand-run CLIs. Uses tqdm when installed, plain lines otherwise."""
from __future__ import annotations

import sys
import time

try:
    from tqdm import tqdm as _tqdm
except ImportError:                                   # pragma: no cover
    _tqdm = None


def bar(iterable, desc: str, total: int | None = None):
    if _tqdm is not None:
        return _tqdm(iterable, desc=desc, total=total, file=sys.stderr)
    items = list(iterable)

    def gen():
        for i, x in enumerate(items, 1):
            print(f"{desc}: {i}/{len(items)}", file=sys.stderr, flush=True)
            yield x
    return gen()


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def elapsed(self) -> str:
        s = time.perf_counter() - self.t0
        return f"{int(s // 60)} min {s % 60:.0f} s"
