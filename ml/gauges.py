"""Shared loader for the hand-run CLIs: gauge list + official categories + cached readings, with per-gauge lines."""
from __future__ import annotations

import csv

from common.nwps import CATEGORIES


def load_gauges(path: str, only: str | None, start: str, end: str, data_dir: str, loader=None) -> list[dict]:
    """[{site_no, lid, cats, readings}] for all gauges at once (small inputs and tests; see iter_gauges)."""
    return list(iter_gauges(path, only, start, end, data_dir, loader))


def count_gauges(path: str, only: str | None) -> int:
    want = {x.strip().upper() for x in only.split(",")} if only else None
    with open(path, encoding="utf-8") as f:
        return sum(1 for g in csv.DictReader(f)
                   if not want or g["site_no"].upper() in want or g.get("lid", "").upper() in want)


def iter_gauges(path: str, only: str | None, start: str, end: str, data_dir: str, loader=None):
    """Yields {site_no, lid, cats, readings} ONE gauge at a time, so 38 gauges of 5-min history (~1 GB of reading
    dicts each) never sit in memory together. Prints one line per gauge, including why a gauge was skipped.
    A gauge is skipped only if its categories are missing/non-numeric or it has no cached data; equal
    action and minor stages are valid (labels are 'crosses minor')."""
    want = {x.strip().upper() for x in only.split(",")} if only else None
    with open(path, encoding="utf-8") as f:
        for g in csv.DictReader(f):
            if want and g["site_no"].upper() not in want and g.get("lid", "").upper() not in want:
                continue
            tag = f"{g['site_no']} {g.get('lid', '')}".strip()
            try:
                cats = {k: float(g[k]) for k in CATEGORIES}
            except (ValueError, TypeError):
                print(f"  skip {tag}: official categories missing or not numeric")
                continue
            if loader is None:
                from producers.replay_producer import load_readings as loader
            try:
                rs = loader(g["site_no"], start, end)
            except SystemExit:
                print(f"  skip {tag}: no cached data under {data_dir}/raw/usgs")
                continue
            if not rs:
                print(f"  skip {tag}: cached files hold no readings in {start}..{end}")
                continue
            print(f"  ok   {tag}: {len(rs)} readings")
            yield {"site_no": g["site_no"], "lid": g.get("lid", ""), "cats": cats, "readings": rs}
