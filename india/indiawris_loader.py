"""I2.2 / INDIA_STUDY step B: India-WRIS water-level exports -> our reading records and envelopes.

India-WRIS (indiawris.gov.in) river water-level data is downloaded by hand in the browser, one station and one year
per file (Excel). The layout ASSUMED here, and reproduced in the hand-made fixture
tests/india/fixtures/indiawris_sample.xlsx (NOT a real download), is:
  * a block of metadata rows on top, "<Key> : <Value>" in the first column or key / value in two columns
    (Station Code, Station Name, River, Basin, State, Agency, ...);
  * one header row containing a date/time column ("Date", "Data Time", "Date Time", ...) and a level column
    ("Water Level (m)", "Level", "Value", "Data Value", ...), then one row per observation.
The loader finds the header row by those names, so small differences are tolerated; if the first real download
differs, adjust HEADER_* below and the fixture together, and say so in a breadcrumb.

Times are Indian Standard Time (UTC+05:30) and converted to UTC. A date without a time (daily data, or an Excel date at
exactly 00:00, which Excel cannot tell apart from a bare date) is stamped at
`assume_time` IST, default 08:00 (the CWC morning observation); change it if the export says otherwise.
Output readings have the shape of common.usgs.parse_iv_json (site_no = CWC station code, parameter "water_level",
unit "m", series "INDIA-WRIS", qualifiers []), so ml/ and india/audit.py take them unchanged. `to_envelopes` wraps
them with producers.tracking.make_envelope, mode = SIMULATION and source = INDIA-WRIS (always replayed history).

Thresholds come from india/thresholds.csv (CWC warning / danger level / HFL, copied by hand from CWC bulletins):
`categories()` maps warning -> action and danger -> minor (INDIA_STUDY step B); moderate and major stay None.

    python -m india.indiawris_loader <file-or-folder> [...]       # prints per-file station, rows, span, interval
"""
from __future__ import annotations

import csv
import glob
import os
import re
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SOURCE = "INDIA-WRIS"
MODE = "SIMULATION"
HEADER_TIME = ("date time", "datetime", "data time", "date", "time", "observation time", "data acquisition time")
HEADER_LEVEL = ("water level", "level", "data value", "value", "wl")
META_KEYS = {"station code": "station_code", "station name": "station_name", "river": "river", "basin": "basin",
             "sub basin": "sub_basin", "state": "state", "district": "district", "agency": "agency",
             "latitude": "lat", "longitude": "lon", "unit": "unit"}
THRESHOLD_COLUMNS = ["station_code", "station_name", "river", "basin", "state", "lat", "lon", "warning_level_m",
                     "danger_level_m", "hfl_m", "hfl_date", "source", "source_date", "notes"]


def _norm(x) -> str:
    return re.sub(r"\s+", " ", str(x if x is not None else "")).strip().lower()


def _cells(path: str) -> list[list]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [list(r) for r in csv.reader(f)]
    import pandas as pd
    df = pd.read_excel(path, header=None, dtype=object)          # .xlsx (openpyxl) or .xls (xlrd)
    return [[None if (isinstance(v, float) and v != v) else v for v in row] for row in df.itertuples(index=False)]


def _find_header(rows: list[list]) -> tuple[int, int, int]:
    for i, row in enumerate(rows):
        names = [_norm(c) for c in row]
        t_col = next((j for j, n in enumerate(names) if n and any(n == h or n.startswith(h) for h in HEADER_TIME)),
                     None)
        l_col = next((j for j, n in enumerate(names) if n and j != t_col
                      and any(n == h or n.startswith(h) for h in HEADER_LEVEL)), None)
        if t_col is not None and l_col is not None:
            return i, t_col, l_col
    raise ValueError("no header row with a date/time column and a water-level column found "
                     f"(looked for {HEADER_TIME} and {HEADER_LEVEL})")


def _meta(rows: list[list], upto: int) -> dict:
    out = {}
    for row in rows[:upto]:
        vals = [c for c in row if c is not None and str(c).strip() != ""]
        if not vals:
            continue
        if len(vals) == 1 and ":" in str(vals[0]):
            k, v = str(vals[0]).split(":", 1)
        elif len(vals) >= 2:
            k, v = str(vals[0]).rstrip(" :"), vals[1]
        else:
            continue
        key = META_KEYS.get(_norm(k).rstrip(":").strip())
        if key:
            out[key] = str(v).strip()
    return out


def parse_time(v, assume_time: time = time(8, 0)) -> datetime | None:
    """Excel datetime/date or text in common Indian formats -> aware UTC datetime. Naive values are IST."""
    if v is None or str(v).strip() == "":
        return None
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        dt = v
        if dt.time() == time(0, 0):          # Excel stores a bare date as midnight: treat as date-only
            dt = datetime.combine(dt.date(), assume_time)
    else:
        s = str(v).strip()
        dt = None
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d-%b-%Y %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                pass
        if dt is None:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
                try:
                    dt = datetime.combine(datetime.strptime(s, fmt).date(), assume_time)
                    break
                except ValueError:
                    pass
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def load_file(path: str, assume_time: time = time(8, 0)) -> list[dict]:
    """One India-WRIS export -> readings (sorted, de-duplicated by time; non-numeric levels skipped)."""
    rows = _cells(path)
    hdr, t_col, l_col = _find_header(rows)
    meta = _meta(rows, hdr)
    code = meta.get("station_code") or os.path.splitext(os.path.basename(path))[0].split("_")[0]
    unit = meta.get("unit") or "m"
    out, seen = [], set()
    for row in rows[hdr + 1:]:
        if max(t_col, l_col) >= len(row):
            continue
        dt = parse_time(row[t_col], assume_time)
        try:
            v = float(str(row[l_col]).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if dt is None or v != v:
            continue
        ts = dt.timestamp()
        if ts in seen:
            continue
        seen.add(ts)
        out.append({"site_no": code, "site_name": meta.get("station_name"), "parameter": "water_level",
                    "unit": unit, "series": SOURCE, "method": "India-WRIS export", "value": v, "qualifiers": [],
                    "reading_time": dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "reading_ts": ts, "source": SOURCE,
                    "meta": meta})
    out.sort(key=lambda r: r["reading_ts"])
    return out


def load_station(paths: list[str], assume_time: time = time(8, 0)) -> list[dict]:
    """Several yearly files of one station -> one sorted, de-duplicated series."""
    by_ts = {}
    for p in sorted(paths):
        for r in load_file(p, assume_time):
            by_ts.setdefault(r["reading_ts"], r)
    return [by_ts[t] for t in sorted(by_ts)]


def load_folder(folder: str, assume_time: time = time(8, 0)) -> dict[str, list[dict]]:
    """All exports under a folder, grouped by station code."""
    files = [p for ext in ("xlsx", "xls", "csv") for p in glob.glob(os.path.join(folder, "**", f"*.{ext}"),
                                                                    recursive=True)]
    groups: dict[str, list[dict]] = {}
    for p in sorted(files):
        for r in load_file(p, assume_time):
            groups.setdefault(r["site_no"], {}).setdefault(r["reading_ts"], r)
    return {s: [d[t] for t in sorted(d)] for s, d in groups.items()}


def to_envelopes(readings: list[dict], fetched_ts: float | None = None) -> list[dict]:
    """replay.gauge envelopes: mode SIMULATION, source INDIA-WRIS. fetched_ts defaults to the reading time (no
    real publication delay is known for these files; step C models it explicitly)."""
    from producers.tracking import make_envelope
    return [make_envelope({k: v for k, v in r.items() if k != "meta"},
                          r["reading_ts"] if fetched_ts is None else fetched_ts, MODE, source=SOURCE)
            for r in readings]


def load_thresholds(path: str = "india/thresholds.csv") -> dict[str, dict]:
    """station_code -> row with floats for the level columns (None when blank)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(row for row in f if not row.startswith("#")):
            if not (r.get("station_code") or "").strip():
                continue
            for k in ("warning_level_m", "danger_level_m", "hfl_m", "lat", "lon"):
                try:
                    r[k] = float(r[k]) if (r.get(k) or "").strip() else None
                except ValueError:
                    r[k] = None
            out[r["station_code"].strip()] = r
    return out


def categories(th: dict) -> dict:
    """CWC levels as the pipeline's categories: warning -> action, danger -> minor (official CWC levels only)."""
    return {"action": th.get("warning_level_m"), "minor": th.get("danger_level_m"), "moderate": None, "major": None}


def main(argv=None):
    import argparse
    from statistics import median
    p = argparse.ArgumentParser(description="Parse India-WRIS exports and print what was found.")
    p.add_argument("paths", nargs="+", help="files or folders")
    p.add_argument("--assume-time", default="08:00", help="IST time stamped on date-only rows")
    a = p.parse_args(argv)
    at = time.fromisoformat(a.assume_time)
    groups: dict[str, list[dict]] = {}
    for x in a.paths:
        if os.path.isdir(x):
            got = load_folder(x, at)
        else:
            rs = load_file(x, at)
            got = {rs[0]["site_no"]: rs} if rs else {}
        for s, rs in got.items():
            groups.setdefault(s, []).extend(rs)
    if not groups:
        raise SystemExit("ERROR: no readings parsed from " + ", ".join(a.paths))
    for s, rs in sorted(groups.items()):
        rs = sorted({r["reading_ts"]: r for r in rs}.values(), key=lambda r: r["reading_ts"])
        gaps = [(b["reading_ts"] - a_["reading_ts"]) / 60 for a_, b in zip(rs, rs[1:])]
        print(f"{s} {rs[0].get('site_name') or ''}: {len(rs)} readings {rs[0]['reading_time']} .. "
              f"{rs[-1]['reading_time']}, median interval {median(gaps) if gaps else 'NA'} min, "
              f"range {min(r['value'] for r in rs):.2f}..{max(r['value'] for r in rs):.2f} {rs[0]['unit']}")


if __name__ == "__main__":
    main()
