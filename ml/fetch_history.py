"""Rebuild the cached USGS IV stage history (parameter 00065) for every gauge in config/gauge_locations.csv.

Writes exactly the layout the existing loaders read (`producers/replay_producer.load_readings`,
`tools/gate_check.fetch_year`): one raw WaterML-JSON response per gauge and calendar year,
`<DATA_DIR>/raw/usgs/<site>_00065_<year>.json` (minified). Requests are built with `common.usgs.build_iv_url`
and parsed with `common.usgs.parse_iv_json`, so nothing is forked.

* chunked by year, resumable (an existing year file is kept unless --refresh; the current year is always refetched),
* retries with exponential backoff (2, 4, 8, 16, 32 s), a preflight request that stops at once with a clear
  message if the network policy blocks the host (history older than ~120 days is served from
  nwis.waterservices.usgs.gov via a 301 redirect),
* tqdm bar over gauge-years, per-gauge row counts and gaps, a summary CSV.

    python -m ml.fetch_history [--start-year 2015] [--gauges-only DRWT2,LCPT2] [--workers 4] \
        [--summary ml/figures/usgs_fetch_summary.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

import requests

from common import config, hydro, usgs

SUMMARY_COLUMNS = ["site_no", "lid", "years_ok", "years_failed", "n_readings", "first_reading", "last_reading",
                   "median_interval_min", "largest_gap_h", "largest_gap_start", "gaps_over_24h", "empty_years",
                   "errors"]


class Blocked(RuntimeError):
    """The proxy / network policy refused the host: retrying will not help."""


def year_path(site: str, year: int) -> str:
    return os.path.join(config.DATA_DIR, "raw", "usgs", f"{site}_{config.PARAM_STAGE}_{year}.json")


def year_url(site: str, year: int, today: date) -> str:
    end = f"{year}-12-31" if year < today.year else today.isoformat()
    return usgs.build_iv_url([site], [config.PARAM_STAGE], start=f"{year}-01-01", end=end)


def get_with_backoff(url: str, tries: int = 6, base_s: float = 2.0, timeout_s: float = 300.0,
                     session: requests.Session | None = None, sleep=time.sleep) -> dict:
    """GET JSON; exponential backoff on network errors and 5xx/429. A proxy CONNECT refusal raises Blocked."""
    s = session or requests
    last = None
    for k in range(tries):
        try:
            r = s.get(url, timeout=timeout_s, headers={"User-Agent": config.USER_AGENT,
                                                       "Accept": "application/json"})
            if r.status_code in (429,) or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ProxyError as e:
            raise Blocked(str(e)[:300]) from e
        except requests.exceptions.HTTPError:
            raise
        except Exception as e:  # timeouts, resets, 5xx, truncated JSON
            last = e
            if k < tries - 1:
                sleep(base_s * 2 ** k)
    raise RuntimeError(f"GET failed after {tries} tries: {last}")


def fetch_year(site: str, year: int, today: date, refresh: bool = False) -> tuple[str, int]:
    """Download one gauge-year into the cache (minified JSON). Returns (status, n_readings)."""
    path = year_path(site, year)
    if os.path.exists(path) and not refresh and year < today.year:
        with open(path, encoding="utf-8") as f:
            return "cached", len(usgs.parse_iv_json(json.load(f)))
    doc = get_with_backoff(year_url(site, year, today))
    n = len(usgs.parse_iv_json(doc))                       # fails loudly on a layout change, before writing
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    return "fetched", n


def gauge_summary(site: str, lid: str, years: list[int], status: dict[int, str]) -> dict:
    """Row counts and gaps of the primary series across all cached years of one gauge."""
    from producers.replay_producer import load_readings
    from ml.dataset import primary_series
    row = {"site_no": site, "lid": lid,
           "years_ok": sum(1 for y in years if not status.get(y, "").startswith("error")),
           "years_failed": sum(1 for y in years if status.get(y, "").startswith("error")),
           "errors": "; ".join(f"{y}: {status[y][6:]}" for y in years if status.get(y, "").startswith("error"))}
    try:
        rs = load_readings(site, f"{years[0]}-01-01", f"{years[-1]}-12-31")
    except SystemExit:
        rs = []
    rs = primary_series(rs) if rs else []
    series = sorted((r["reading_ts"], r["value"]) for r in rs)
    row["n_readings"] = len(series)
    per_year = {y: 0 for y in years}
    for t, _ in series:
        per_year[datetime.fromtimestamp(t, timezone.utc).year] = per_year.get(
            datetime.fromtimestamp(t, timezone.utc).year, 0) + 1
    row["empty_years"] = ",".join(str(y) for y in years if per_year.get(y, 0) == 0)
    if len(series) >= 2:
        gaps = [(series[i + 1][0] - series[i][0], series[i][0]) for i in range(len(series) - 1)]
        g, g0 = max(gaps)
        row.update(first_reading=_iso(series[0][0]), last_reading=_iso(series[-1][0]),
                   median_interval_min=round(hydro.median_interval_min(series), 1),
                   largest_gap_h=round(g / 3600, 1), largest_gap_start=_iso(g0),
                   gaps_over_24h=sum(1 for d, _ in gaps if d > 86400))
    return row


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def main(argv=None):
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="Download USGS IV stage history into data/raw/usgs/ (one file per year).")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only", help="comma list of NWPS ids or site numbers")
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--end-year", type=int, default=date.today().year)
    p.add_argument("--workers", type=int, default=4, help="parallel requests (keep it polite)")
    p.add_argument("--refresh", action="store_true", help="refetch years that are already cached")
    p.add_argument("--data-dir", help="cache root (default: DATA_DIR env or ./data)")
    p.add_argument("--summary", default="ml/figures/usgs_fetch_summary.csv")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer, today = Timer(), date.today()
    want = {x.strip().upper() for x in a.gauges_only.split(",")} if a.gauges_only else None
    with open(a.gauges, encoding="utf-8") as f:
        gauges = [g for g in csv.DictReader(f)
                  if not want or g["site_no"].upper() in want or g.get("lid", "").upper() in want]
    years = list(range(a.start_year, min(a.end_year, today.year) + 1))
    os.makedirs(os.path.join(config.DATA_DIR, "raw", "usgs"), exist_ok=True)
    jobs = [(g["site_no"], y) for g in gauges for y in years]
    print(f"ml.fetch_history: {len(gauges)} gauges x {len(years)} years ({years[0]}..{years[-1]}) = {len(jobs)} "
          f"requests -> {config.DATA_DIR}/raw/usgs (workers={a.workers})")
    if not jobs:
        raise SystemExit("ERROR: no gauges selected")

    # preflight: one old year, so a blocked redirect host stops the run at once instead of after 456 retries
    try:
        get_with_backoff(usgs.build_iv_url([jobs[0][0]], [config.PARAM_STAGE], start=f"{years[0]}-01-01",
                                           end=f"{years[0]}-01-02"), tries=2)
    except Blocked as e:
        raise SystemExit("ERROR: the network policy blocks USGS history. Requests older than ~120 days are "
                         "redirected to nwis.waterservices.usgs.gov; allow that host. Detail: " + str(e)[:200])

    status: dict[str, dict[int, str]] = {g["site_no"]: {} for g in gauges}
    counts: dict[str, int] = {g["site_no"]: 0 for g in gauges}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(fetch_year, s, y, today, a.refresh): (s, y) for s, y in jobs}
        for fut in bar(as_completed(futs), "gauge-years", total=len(futs)):
            s, y = futs[fut]
            try:
                st, n = fut.result()
                status[s][y] = st
                counts[s] += n
            except Exception as e:
                status[s][y] = "error " + str(e)[:150]

    rows = []
    for g in bar(gauges, "summaries"):
        r = gauge_summary(g["site_no"], g.get("lid", ""), years, status[g["site_no"]])
        rows.append(r)
        print(f"  {g['site_no']} {g.get('lid', ''):6s} rows={r['n_readings']:>7} years_ok={r['years_ok']}"
              f"/{len(years)} largest_gap={r.get('largest_gap_h', '')} h gaps>24h={r.get('gaps_over_24h', '')}"
              f"{' empty=' + r['empty_years'] if r['empty_years'] else ''}"
              f"{' ERR ' + r['errors'][:120] if r['errors'] else ''}")
    os.makedirs(os.path.dirname(a.summary) or ".", exist_ok=True)
    with open(a.summary, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        wr.writeheader()
        wr.writerows(rows)
    size = sum(os.path.getsize(os.path.join(config.DATA_DIR, "raw", "usgs", x))
               for x in os.listdir(os.path.join(config.DATA_DIR, "raw", "usgs")))
    failed = sum(r["years_failed"] for r in rows)
    print(f"done: {sum(r['n_readings'] for r in rows)} readings, {len(rows)} gauges, {failed} failed gauge-years, "
          f"cache {size / 1e6:.0f} MB -> {a.summary} in {timer.elapsed()}")


if __name__ == "__main__":
    main()
