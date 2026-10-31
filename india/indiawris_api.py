"""I2.2: India-WRIS API client (spec: docs/indiawris_api.json on claude/core, OpenAPI 3.0.1, server
https://indiawris.gov.in, no auth). Every endpoint is a POST with QUERY parameters only:
  basin scope   /Dataset/Basin/<dataset>   basinName, tributaryName, agencyName, startdate, enddate, page, size, download
  state scope   /Dataset/<dataset>         stateName, districtName, agencyName, startdate, enddate, page, size, download
Dataset names differ between scopes (basin: "River WaterLevel", state: "River Water Level"); see DATASETS.
Dates are YYYY-MM-DD, page >= 0, size <= 1000.

UNVERIFIED: the spec types the response only as StatusHandler {statusCode, message, data: object}; the record
layout inside `data` is unknown, and indiawris.gov.in is not reachable from the cloud session where this was written
(DNS fails; it may also refuse non-Indian IPs). `extract_records` and `to_readings` therefore accept several plausible
shapes and field names, and every raw response is saved, so the parser can be fixed from a real body. Run --probe on
the laptop first and paste its output back.

What it does: splits the date range into chunks (default 365 days), pages through each chunk until a short or empty
page, retries with exponential backoff (2, 4, 8, 16, 32 s) on network errors / 5xx / 429, saves every raw response
under data/raw/indiawris/api/<dataset>/<scope>/<start>_<end>_p<page>.<json|txt>, then converts the records it can
read into per-station CSV files under data/raw/indiawris/api_csv/ in the layout india/indiawris_loader.py reads
(metadata rows + "Data Time, Water Level (m)" header), so india.audit / india.table1 use them unchanged
(mode = SIMULATION, source = INDIA-WRIS, as for the Excel downloads).

    python -m india.indiawris_api --probe                                     # one small request, prints raw body
    python -m india.indiawris_api --discover                                  # ~11 requests: which names return data
    python -m india.indiawris_api --basin Godavari --tributary Godavari --agency CWC \
        --start 2015-01-01 --end 2025-12-31                                   # page through and save
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import date, timedelta
from urllib.parse import quote

import requests

BASE = "https://indiawris.gov.in"
DATASETS = {   # key -> (basin-scope name, state-scope name), exactly as spelled in the spec
    "water_level": ("River WaterLevel", "River Water Level"),
    "discharge": ("River Water Discharge", "River Water Discharge"),
    "rainfall": ("RainFall", "RainFall"),
}
MAX_SIZE = 1000
# candidate field names, compared case-insensitively with non-letters removed
F_CODE = ("stationcode", "stationid", "sitecode", "code", "station")
F_NAME = ("stationname", "sitename", "name")
F_TIME = ("datatime", "datetime", "date", "time", "observationtime", "dataacquisitiontime", "timestamp")
F_VALUE = ("datavalue", "value", "waterlevel", "level", "wl", "discharge", "rainfall")
F_UNIT = ("unit", "unitcode", "uom")


def endpoint(dataset: str, scope: str = "basin") -> str:
    basin_name, state_name = DATASETS[dataset]
    path = f"/Dataset/Basin/{basin_name}" if scope == "basin" else f"/Dataset/{state_name}"
    return BASE + quote(path)


def query(scope: str, start: str, end: str, page: int, size: int, agency: str, basin: str | None = None,
          tributary: str | None = None, state: str | None = None, district: str | None = None,
          download: bool = False) -> dict:
    if not 0 < size <= MAX_SIZE:
        raise ValueError(f"size must be 1..{MAX_SIZE}")
    q = {"agencyName": agency, "startdate": start, "enddate": end, "page": page, "size": size,
         "download": str(download).lower()}
    if scope == "basin":
        q.update(basinName=basin, tributaryName=basin if tributary is None else tributary)
    else:
        q.update(stateName=state, districtName=district)
    return q


def date_chunks(start: str, end: str, days: int = 365) -> list[tuple[str, str]]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while a <= b:
        c = min(a + timedelta(days=days - 1), b)
        out.append((a.isoformat(), c.isoformat()))
        a = c + timedelta(days=1)
    return out


def post_with_backoff(url: str, params: dict, session=None, tries: int = 6, base_s: float = 2.0,
                      timeout_s: float = 120.0, sleep=time.sleep):
    """POST with query parameters; returns the requests.Response. Retries network errors, 5xx and 429."""
    s = session or requests
    last = None
    for k in range(tries):
        try:
            r = s.post(url, params=params, timeout=timeout_s,
                       headers={"User-Agent": "dews-flood-early-warning", "Accept": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except requests.exceptions.ProxyError:
            raise
        except Exception as e:  # noqa: BLE001 - network errors, timeouts, 5xx
            last = e
            if k < tries - 1:
                sleep(base_s * 2 ** k)
    raise RuntimeError(f"POST failed after {tries} tries: {url}: {last}")


def _key(s) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def _pick(rec: dict, names) -> object:
    keys = {_key(k): k for k in rec}
    for n in names:
        if n in keys:
            return rec[keys[n]]
    return None


def extract_records(doc) -> list[dict]:
    """Record list from a parsed body. Accepts: a list; StatusHandler {data: list | {content|records|rows|data|
    result: list}}; Spring-style pages {content: [...]}. Returns [] when nothing list-like is found."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if not isinstance(doc, dict):
        return []
    for k in ("data", "content", "records", "rows", "result", "results", "items"):
        v = doc.get(k)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
        if isinstance(v, dict):
            inner = extract_records(v)
            if inner:
                return inner
    return []


def to_readings(records: list[dict], meta: dict | None = None) -> list[dict]:
    """Records -> india/indiawris_loader reading dicts (records without code, time or numeric value are skipped)."""
    from india.indiawris_loader import MODE, SOURCE, parse_time
    out = []
    for rec in records:
        code, t, v = _pick(rec, F_CODE), _pick(rec, F_TIME), _pick(rec, F_VALUE)
        dt = parse_time(t)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if code in (None, "") or dt is None or v != v:
            continue
        out.append({"site_no": str(code).strip(), "site_name": _pick(rec, F_NAME), "parameter": "water_level",
                    "unit": _pick(rec, F_UNIT) or "m", "series": SOURCE, "method": "India-WRIS API", "value": v,
                    "qualifiers": [], "reading_time": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "reading_ts": dt.timestamp(), "source": SOURCE, "mode": MODE, "meta": dict(meta or {})})
    return out


def write_loader_csv(readings: list[dict], out_dir: str) -> list[str]:
    """One CSV per station in the layout india/indiawris_loader.load_file reads. Returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    by = {}
    for r in readings:
        by.setdefault(r["site_no"], {})[r["reading_ts"]] = r
    paths = []
    for code, d in sorted(by.items()):
        rs = [d[t] for t in sorted(d)]
        p = os.path.join(out_dir, f"{re.sub(r'[^A-Za-z0-9_-]', '_', code)}_api.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Station Code :", code])
            w.writerow(["Station Name :", rs[0].get("site_name") or ""])
            w.writerow(["Unit :", rs[0].get("unit") or "m"])
            w.writerow([])
            w.writerow(["Data Time", "Water Level (m)"])
            for r in rs:   # times written back in IST, as the exports are
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["reading_ts"] + 19800))
                w.writerow([t, r["value"]])
        paths.append(p)
    return paths


def _save_raw(resp, raw_dir: str, start: str, end: str, page: int) -> tuple[str, object]:
    os.makedirs(raw_dir, exist_ok=True)
    try:
        doc = resp.json()
        p = os.path.join(raw_dir, f"{start}_{end}_p{page}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))
    except ValueError:
        doc = None
        p = os.path.join(raw_dir, f"{start}_{end}_p{page}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(resp.text)
    return p, doc


def fetch(dataset: str, scope: str, start: str, end: str, agency: str, data_dir: str, size: int = MAX_SIZE,
          chunk_days: int = 365, session=None, progress=None, max_pages: int = 10000, **where) -> dict:
    """Page through every chunk; save raw bodies; return {records, raw_files, pages, errors}."""
    prog = progress or (lambda it, desc, total=None: it)
    scope_key = re.sub(r"[^A-Za-z0-9_-]+", "_", "_".join(str(v) for v in where.values() if v) + f"_{agency}")
    raw_dir = os.path.join(data_dir, "raw", "indiawris", "api", dataset, scope_key)
    url = endpoint(dataset, scope)
    out = {"records": [], "raw_files": [], "pages": 0, "errors": []}
    for s, e in prog(date_chunks(start, end, chunk_days), "date chunks"):
        for page in range(max_pages):
            try:
                r = post_with_backoff(url, query(scope, s, e, page, size, agency, **where), session=session)
            except Exception as ex:  # noqa: BLE001
                out["errors"].append(f"{s}..{e} p{page}: {str(ex)[:160]}")
                break
            p, doc = _save_raw(r, raw_dir, s, e, page)
            out["raw_files"].append(p)
            out["pages"] += 1
            if r.status_code >= 400:
                out["errors"].append(f"{s}..{e} p{page}: HTTP {r.status_code}")
                break
            recs = extract_records(doc)
            out["records"].extend(recs)
            last = isinstance(doc, dict) and (doc.get("last") is True or
                                              (isinstance(doc.get("data"), dict) and doc["data"].get("last") is True))
            if len(recs) < size or last:
                break
    return out


def probe(dataset: str = "water_level", basin: str = "Godavari", tributary: str | None = None, agency: str = "CWC",
          days: int = 7, end: str | None = None, size: int = 10, data_dir: str = "data", session=None,
          lines: int = 60, printer=print) -> int:
    """One small request; prints status, headers and the first `lines` lines of the body; saves the body."""
    end_d = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    start_d = end_d - timedelta(days=days - 1)
    url, q = endpoint(dataset, "basin"), query("basin", start_d.isoformat(), end_d.isoformat(), 0, size, agency,
                                                basin=basin, tributary=tributary)
    printer(f"india.indiawris_api --probe: POST {url}")
    printer(f"  query: {q}")
    try:
        r = post_with_backoff(url, q, session=session, tries=2, timeout_s=60)
    except Exception as e:  # noqa: BLE001
        printer(f"  FAILED: {type(e).__name__}: {str(e)[:300]}")
        printer("  (from outside India the host may not resolve or may refuse the connection)")
        return 2
    printer(f"  HTTP {r.status_code}")
    for k, v in r.headers.items():
        printer(f"  {k}: {v}")
    try:
        body = json.dumps(r.json(), indent=1, ensure_ascii=False)
    except ValueError:
        body = r.text
    printer("  ---- body (first %d lines) ----" % lines)
    for ln in body.splitlines()[:lines]:
        printer("  " + ln)
    p, doc = _save_raw(r, os.path.join(data_dir, "raw", "indiawris", "api", "probe"), start_d.isoformat(),
                       end_d.isoformat(), 0)
    recs = extract_records(doc)
    printer(f"  ---- saved {p}; {len(recs)} record(s) found by extract_records, "
            f"{len(to_readings(recs))} parsed as readings ----")
    if recs:
        printer(f"  first record keys: {sorted(recs[0])}")
    return 0 if r.status_code < 400 else 1


# --discover matrix: (label, scope, where, agency, start, end). Names of tributaries, districts and the state agency are
# GUESSES at India-WRIS spellings; the point is to find which combination returns records. Edit freely.
DISCOVER_WINDOWS = {"godavari_flood_2022": ("2022-07-08", "2022-07-18"),   # Godavari flood at Bhadrachalam
                    "kerala_flood_2018": ("2018-08-10", "2018-08-20"),     # Periyar / Kerala floods
                    "dry_week_2023": ("2023-02-01", "2023-02-07")}         # non-monsoon week
DISCOVER = [
    ("basin Godavari / trib=Godavari / CWC", "basin", {"basin": "Godavari", "tributary": "Godavari"}, "CWC",
     "godavari_flood_2022"),
    ("basin Godavari / trib='' / CWC", "basin", {"basin": "Godavari", "tributary": ""}, "CWC", "godavari_flood_2022"),
    ("basin Godavari / trib=Pranhita / CWC", "basin", {"basin": "Godavari", "tributary": "Pranhita"}, "CWC",
     "godavari_flood_2022"),
    ("basin Godavari / trib=Godavari / agency=''", "basin", {"basin": "Godavari", "tributary": "Godavari"}, "",
     "godavari_flood_2022"),
    ("basin Godavari / trib=Godavari / CWC / dry", "basin", {"basin": "Godavari", "tributary": "Godavari"}, "CWC",
     "dry_week_2023"),
    ("basin Periyar / trib=Periyar / CWC", "basin", {"basin": "Periyar", "tributary": "Periyar"}, "CWC",
     "kerala_flood_2018"),
    ("state Telangana / Bhadradri Kothagudem / CWC", "state",
     {"state": "Telangana", "district": "Bhadradri Kothagudem"}, "CWC", "godavari_flood_2022"),
    ("state Telangana / Bhadradri Kothagudem / state agency", "state",
     {"state": "Telangana", "district": "Bhadradri Kothagudem"}, "Telangana Water Resources Department",
     "godavari_flood_2022"),
    ("state Kerala / Ernakulam / CWC", "state", {"state": "Kerala", "district": "Ernakulam"}, "CWC",
     "kerala_flood_2018"),
    ("state Kerala / Ernakulam / state agency", "state", {"state": "Kerala", "district": "Ernakulam"},
     "Kerala Water Resources Department", "kerala_flood_2018"),
    ("state Kerala / Ernakulam / CWC / dry", "state", {"state": "Kerala", "district": "Ernakulam"}, "CWC",
     "dry_week_2023"),
]


def discover(dataset: str = "water_level", matrix=None, size: int = 5, data_dir: str = "data", session=None,
             sleep_s: float = 3.0, sleep=time.sleep, printer=print) -> list[dict]:
    """One small request per combination (polite: `sleep_s` between calls, no retries beyond one); prints one line
    per combination (HTTP status, body statusCode/message, record count) and the first record of any non-empty
    response. Raw bodies go to data/raw/indiawris/api/discover/."""
    matrix = matrix if matrix is not None else DISCOVER
    raw = os.path.join(data_dir, "raw", "indiawris", "api", "discover")
    out = []
    printer(f"india.indiawris_api --discover: {len(matrix)} requests, {sleep_s:g} s apart, size {size}")
    for k, (label, scope, where, agency, win) in enumerate(matrix):
        if k:
            sleep(sleep_s)
        start, end = DISCOVER_WINDOWS.get(win, (None, None)) if isinstance(win, str) else win
        q = query(scope, start, end, 0, size, agency, **where)
        row = {"label": label, "window": f"{start}..{end}", "http": None, "status_code": None, "message": "",
               "records": 0}
        try:
            r = post_with_backoff(endpoint(dataset, scope), q, session=session, tries=2, timeout_s=60, sleep=sleep)
            row["http"] = r.status_code
            p, doc = _save_raw(r, os.path.join(raw, f"{k:02d}"), start, end, 0)
            if isinstance(doc, dict):
                row["status_code"], row["message"] = doc.get("statusCode"), str(doc.get("message") or "")[:80]
            recs = extract_records(doc)
            row["records"] = len(recs)
            if recs:
                row["first_record"] = recs[0]
        except Exception as e:  # noqa: BLE001
            row["message"] = f"FAILED {type(e).__name__}: {str(e)[:100]}"
        out.append(row)
        printer(f"  [{k:02d}] {label:55s} {row['window']}  HTTP {row['http']}  statusCode {row['status_code']}  "
                f"records {row['records']}  {row['message']}")
        if row.get("first_record"):
            printer(f"       first record: {json.dumps(row['first_record'], ensure_ascii=False)[:600]}")
    hits = [r for r in out if r["records"]]
    printer(f"discover done: {len(hits)} of {len(out)} combinations returned records"
            + (": " + "; ".join(r["label"] for r in hits) if hits else ""))
    return out


def main(argv=None):
    import argparse
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="India-WRIS API client (response layout unverified; run --probe first).")
    p.add_argument("--probe", action="store_true", help="one small request; print status, headers, raw body")
    p.add_argument("--discover", action="store_true", help="small matrix of names/agencies/windows; record counts")
    p.add_argument("--sleep", type=float, default=3.0, help="seconds between --discover requests")
    p.add_argument("--dataset", default="water_level", choices=sorted(DATASETS))
    p.add_argument("--scope", default="basin", choices=["basin", "state"])
    p.add_argument("--basin", default="Godavari")
    p.add_argument("--tributary", help="tributaryName (required by the API; default = basin name)")
    p.add_argument("--state")
    p.add_argument("--district")
    p.add_argument("--agency", default="CWC")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    p.add_argument("--days", type=int, default=7, help="probe window")
    p.add_argument("--size", type=int, default=MAX_SIZE)
    p.add_argument("--chunk-days", type=int, default=365)
    p.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data"))
    a = p.parse_args(argv)
    if a.probe:
        raise SystemExit(probe(a.dataset, a.basin, a.tributary, a.agency, a.days, None, 10, a.data_dir))
    if a.discover:
        discover(a.dataset, data_dir=a.data_dir, sleep_s=a.sleep)
        return
    timer = Timer()
    where = ({"basin": a.basin, "tributary": a.tributary or a.basin} if a.scope == "basin"
             else {"state": a.state, "district": a.district})
    if a.scope == "state" and not (a.state and a.district):
        raise SystemExit("ERROR: --scope state needs --state and --district")
    print(f"india.indiawris_api: {a.dataset} ({a.scope}: {where}, agency {a.agency}) {a.start}..{a.end}, "
          f"chunks of {a.chunk_days} days, page size {a.size} -> {a.data_dir}/raw/indiawris/api/")
    res = fetch(a.dataset, a.scope, a.start, a.end, a.agency, a.data_dir, a.size, a.chunk_days, progress=bar, **where)
    rs = to_readings(res["records"], {"dataset": a.dataset, **where, "agency": a.agency})
    paths = write_loader_csv(rs, os.path.join(a.data_dir, "raw", "indiawris", "api_csv")) if rs else []
    for e in res["errors"][:20]:
        print(f"  error {e}")
    stations = sorted({r["site_no"] for r in rs})
    print(f"done: {res['pages']} pages, {len(res['raw_files'])} raw files, {len(res['records'])} records, "
          f"{len(rs)} readings at {len(stations)} stations -> {len(paths)} loader CSVs, {len(res['errors'])} errors "
          f"in {timer.elapsed()}")
    if res["records"] and not rs:
        print("  records were returned but none parsed: the response layout differs from the guesses in "
              "india/indiawris_api.py (F_CODE/F_TIME/F_VALUE); send a raw file from data/raw/indiawris/api/.")


if __name__ == "__main__":
    main()
