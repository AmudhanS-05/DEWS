"""M2.1: MRMS hourly rainfall history around observed floods plus matched dry periods (offline).

What it does
  1. Events: every crossing of the official MINOR stage in the cached USGS history (common.hydro.find_crossings, the
     same rule as ml/dataset.py), from the start of the MRMS archive on. `slow` = rise >= 3 h is recorded per event.
  2. Hours: for each event the hours [crossing - before_h, crossing + after_h]; for each event one matched DRY window of
     the same length at the same gauge (same calendar month in another year if possible, stage below the action stage
     throughout and no crossing at any gauge within +-3 days), chosen with a fixed seed.
  3. For each hour, download the hourly gauge-corrected product (MultiSensor_QPE_01H_Pass2, mm, valid at HH:00 =
     the end of the accumulation hour), decode it IN MEMORY (eccodes) and keep, for every gauge and every radius
     (default 5, 10 and 25 km, so S3's radius sensitivity needs no second download), the MEAN DEPTH over the cells
     whose centres are inside the circle (cells < 0 = no coverage are excluded) and the covered fraction.
     The raw GRIB2 is never written to disk.
Output: `<DATA_DIR>/rain/mrms_circle.parquet` (site_no, hour_ts, radius_km, rain_mm, coverage, product) and
`<DATA_DIR>/rain/mrms_hours.csv` (hour_ts, reason, status). Resumable: hours already in the parquet are skipped.

Limits to state with any result: the public archive (noaa-mrms-pds) starts 2020-10-14, so floods before that
(including the 51 of 2018) have no radar rainfall here. This is replayed history: rows built from it are SIMULATION.

    python -m ml.mrms_history [--radii 5,10,25] [--before-h 48] [--after-h 12] [--workers 4]
"""
from __future__ import annotations

import csv
import gzip
import math
import os
import random
import time
from datetime import datetime, timezone

import numpy as np

from common import config, hydro

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "MultiSensor_QPE_01H_Pass2_00.00"
ARCHIVE_START = datetime(2020, 10, 14, 0, tzinfo=timezone.utc).timestamp()
EARTH_R_KM = 6371.0088


# ---------- pure helpers (tested) ----------

def hour_key(hour_ts: float, product: str = PRODUCT) -> str:
    d = datetime.fromtimestamp(hour_ts, timezone.utc)
    return f"CONUS/{product}/{d:%Y%m%d}/MRMS_{product}_{d:%Y%m%d-%H%M%S}.grib2.gz"


def floor_hour(ts: float) -> float:
    return float(int(ts // 3600) * 3600)


def circle_indices(lat0: float, lon0: float, radius_km: float, geom: dict) -> np.ndarray:
    """Flat indices of the grid cells whose centres lie within radius_km of (lat0, lon0).
    geom: lat_first, lon_first (degrees, lon in -180..180 or 0..360), dlat, dlon (> 0), ni (columns), nj (rows),
    rows run north -> south (jScansPositively = 0), columns west -> east."""
    lon_first = geom["lon_first"] - 360.0 if geom["lon_first"] > 180 else geom["lon_first"]
    dlat, dlon = geom["dlat"], geom["dlon"]
    pad_lat = radius_km / 111.0 + dlat
    pad_lon = radius_km / (111.0 * max(math.cos(math.radians(lat0)), 1e-6)) + dlon
    j0 = max(int(math.floor((geom["lat_first"] - (lat0 + pad_lat)) / dlat)), 0)
    j1 = min(int(math.ceil((geom["lat_first"] - (lat0 - pad_lat)) / dlat)), geom["nj"] - 1)
    i0 = max(int(math.floor((lon0 - pad_lon - lon_first) / dlon)), 0)
    i1 = min(int(math.ceil((lon0 + pad_lon - lon_first) / dlon)), geom["ni"] - 1)
    if j1 < j0 or i1 < i0:
        return np.array([], dtype=np.int64)
    jj, ii = np.meshgrid(np.arange(j0, j1 + 1), np.arange(i0, i1 + 1), indexing="ij")
    lat = np.radians(geom["lat_first"] - jj * dlat)
    lon = np.radians(lon_first + ii * dlon)
    p0, l0 = math.radians(lat0), math.radians(lon0)
    a = np.sin((lat - p0) / 2) ** 2 + math.cos(p0) * np.cos(lat) * np.sin((lon - l0) / 2) ** 2
    d = 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))
    m = d <= radius_km
    return (jj[m] * geom["ni"] + ii[m]).astype(np.int64)


def circle_mean(values: np.ndarray, idx: np.ndarray, missing_value: float | None = 9999.0) -> tuple[float, float]:
    """(mean depth over covered cells, covered fraction). Negative codes (-3 = no coverage) and the GRIB missing
    value are excluded. NaN mean when nothing is covered."""
    if not len(idx):
        return float("nan"), 0.0
    v = values[idx]
    ok = (v >= 0) & np.isfinite(v)
    if missing_value is not None:
        ok &= v != missing_value
    cov = float(ok.mean())
    return (float(v[ok].mean()) if ok.any() else float("nan")), cov


def event_windows(events: list[dict], before_h: float, after_h: float) -> list[tuple[float, float, str]]:
    """events: [{site_no, crossing_ts}] -> (start_hour, end_hour, reason) per event."""
    return [(floor_hour(e["crossing_ts"] - before_h * 3600), floor_hour(e["crossing_ts"] + after_h * 3600) + 3600,
             f"event {e['site_no']}|{int(e['crossing_ts'])}") for e in events]


def matched_dry_windows(events: list[dict], series_by_site: dict[str, tuple[np.ndarray, np.ndarray]],
                        action_by_site: dict[str, float], all_crossings: list[float], before_h: float,
                        after_h: float, earliest_ts: float, latest_ts: float, seed: int = 7,
                        quiet_days: float = 3.0) -> list[tuple[float, float, str]]:
    """One dry window per event: same gauge, same length, stage below ACTION throughout (and data present), no
    crossing at any gauge within +-quiet_days. Prefers the same calendar month in another year; else any month."""
    rng = random.Random(seed)
    span = (before_h + after_h) * 3600
    cross = np.sort(np.array(all_crossings, dtype=float))
    out, used = [], []
    for e in events:
        times, vals = series_by_site[e["site_no"]]
        act = action_by_site[e["site_no"]]
        month = datetime.fromtimestamp(e["crossing_ts"], timezone.utc).month
        days = np.arange(floor_hour(earliest_ts) + 86400, latest_ts - span, 86400.0)
        same = [d for d in days if datetime.fromtimestamp(d, timezone.utc).month == month]
        pick = None
        for pool in (same, list(days)):
            cand = list(pool)
            rng.shuffle(cand)
            for s in cand[:400]:
                s = floor_hour(s + rng.randrange(24) * 3600)
                e_ = s + span
                if abs(s - e["crossing_ts"]) < 30 * 86400 or any(abs(s - u) < span for u in used):
                    continue
                k = np.searchsorted(cross, s - quiet_days * 86400)
                if k < len(cross) and cross[k] <= e_ + quiet_days * 86400:
                    continue
                lo, hi = np.searchsorted(times, s), np.searchsorted(times, e_)
                if hi - lo < 0.5 * span / 900 or np.nanmax(vals[lo:hi]) >= act:   # need data, and stay below action
                    continue
                pick = s
                break
            if pick is not None:
                break
        if pick is not None:
            used.append(pick)
            out.append((pick, pick + span + 3600, f"dry {e['site_no']}|{int(e['crossing_ts'])}"))
    return out


def hours_from_windows(windows: list[tuple[float, float, str]]) -> dict[float, str]:
    """Union of hourly valid times; the first reason wins (events are listed before dry windows)."""
    out: dict[float, str] = {}
    for s, e, why in windows:
        h = floor_hour(s)
        while h < e:
            out.setdefault(h, why)
            h += 3600
    return out


# ---------- download + decode (needs network and eccodes) ----------

_G: dict = {}


def _init_worker(gauges: list[dict], radii: list[float]):
    _G["gauges"], _G["radii"], _G["idx"] = gauges, radii, {}


def _geometry(h) -> dict:
    import eccodes
    g = {k: eccodes.codes_get(h, k) for k in ("Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
                                               "longitudeOfFirstGridPointInDegrees", "iDirectionIncrementInDegrees",
                                               "jDirectionIncrementInDegrees", "jScansPositively", "iScansNegatively")}
    if g["jScansPositively"] != 0 or g["iScansNegatively"] != 0:
        raise ValueError(f"unexpected MRMS scan order {g}")
    return {"ni": g["Ni"], "nj": g["Nj"], "lat_first": g["latitudeOfFirstGridPointInDegrees"],
            "lon_first": g["longitudeOfFirstGridPointInDegrees"], "dlat": g["jDirectionIncrementInDegrees"],
            "dlon": g["iDirectionIncrementInDegrees"]}


def process_hour(hour_ts: float, tries: int = 5) -> tuple[float, str, list[tuple]]:
    """Download + decode one hourly file in memory; return (hour_ts, status, rows)."""
    import eccodes
    import requests
    url = f"{BUCKET}/{hour_key(hour_ts)}"
    body = None
    for k in range(tries):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": config.USER_AGENT})
            if r.status_code in (403, 404):
                return hour_ts, "missing", []
            r.raise_for_status()
            body = r.content
            break
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                return hour_ts, f"error {str(e)[:120]}", []
            time.sleep(2 * 2 ** k)
    h = eccodes.codes_new_from_message(gzip.decompress(body))
    try:
        geom = _geometry(h)
        try:
            missing = float(eccodes.codes_get(h, "missingValue"))
        except Exception:  # noqa: BLE001
            missing = None
        vals = eccodes.codes_get_values(h)
    finally:
        eccodes.codes_release(h)
    key = tuple(sorted(geom.items()))
    rows = []
    for g in _G["gauges"]:
        for rad in _G["radii"]:
            ik = (key, g["site_no"], rad)
            if ik not in _G["idx"]:
                _G["idx"][ik] = circle_indices(g["lat"], g["lon"], rad, geom)
            mean, cov = circle_mean(vals, _G["idx"][ik], missing)
            rows.append((g["site_no"], hour_ts, rad, mean, cov, PRODUCT))
    return hour_ts, "ok", rows


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def main(argv=None):
    import argparse
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import pandas as pd
    from ml.dataset import primary_series
    from ml.gauges import count_gauges, iter_gauges
    from ml.progress import Timer, bar
    p = argparse.ArgumentParser(description="M2.1: MRMS rain over circles around each gauge, event + dry hours.")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--gauges-only", help="restrict the EVENT gauges (rain is still summed for every gauge)")
    p.add_argument("--start", default="2020-10-14")
    p.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    p.add_argument("--radii", default="5,10,25", help="km; 10 is the decided radius")
    p.add_argument("--before-h", type=float, default=48.0)
    p.add_argument("--after-h", type=float, default=12.0)
    p.add_argument("--slow-only", action="store_true", help="only events with rise >= 3 h (default: all crossings)")
    p.add_argument("--no-dry", action="store_true")
    p.add_argument("--max-hours", type=int, help="stop after this many new hours (smoke test)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--data-dir", help="cache root (default: DATA_DIR env or ./data)")
    a = p.parse_args(argv)
    if a.data_dir:
        config.DATA_DIR = a.data_dir
    timer = Timer()
    radii = [float(x) for x in a.radii.split(",")]
    out_dir = os.path.join(config.DATA_DIR, "rain")
    os.makedirs(out_dir, exist_ok=True)
    out_pq, out_hours = os.path.join(out_dir, "mrms_circle.parquet"), os.path.join(out_dir, "mrms_hours.csv")
    print(f"ml.mrms_history: events from cached USGS history {a.start}..{a.end}, product {PRODUCT}, "
          f"radii {radii} km -> {out_pq}")

    with open(a.gauges, encoding="utf-8") as f:
        all_g = [{"site_no": g["site_no"], "lid": g["lid"], "lat": float(g["lat"]), "lon": float(g["lon"])}
                 for g in csv.DictReader(f)]
    events, series, action, all_cross = [], {}, {}, []
    t_lo = max(ARCHIVE_START, datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp())
    for g in bar(iter_gauges(a.gauges, a.gauges_only, a.start, a.end, config.DATA_DIR), "events per gauge",
                 total=count_gauges(a.gauges, a.gauges_only)):
        rs = primary_series(g["readings"])
        ser = sorted((r["reading_ts"], r["value"]) for r in rs)
        series[g["site_no"]] = (np.array([t for t, _ in ser]), np.array([v for _, v in ser]))
        action[g["site_no"]] = g["cats"]["action"]
        for i in hydro.find_crossings(ser, g["cats"]["minor"]):
            ct = ser[i][0]
            all_cross.append(ct)
            if ct - a.before_h * 3600 < t_lo:
                continue
            slow = hydro.rise_time_h(ser, i) >= 3.0
            if a.slow_only and not slow:
                continue
            events.append({"site_no": g["site_no"], "lid": g["lid"], "crossing_ts": ct, "slow": slow})
        del g, rs, ser
    if not series:
        raise SystemExit("ERROR: no gauge had cached USGS history (run python -m ml.fetch_history first)")
    events.sort(key=lambda e: e["crossing_ts"])
    t_hi = max(float(s[0][-1]) for s in series.values() if len(s[0]))
    wins = event_windows(events, a.before_h, a.after_h)
    dry = [] if a.no_dry else matched_dry_windows(events, series, action, all_cross, a.before_h, a.after_h,
                                                  t_lo, t_hi)
    hours = hours_from_windows(wins + dry)
    print(f"  {len(events)} minor crossings since {_iso(t_lo)} ({sum(e['slow'] for e in events)} slow), "
          f"{len(dry)} matched dry windows -> {len(hours)} distinct hours (one file serves all {len(all_g)} gauges)")
    for e in events:
        print(f"    event {e['lid']:6s} {_iso(e['crossing_ts'])} {'slow' if e['slow'] else 'fast'}")

    done = set()
    if os.path.exists(out_pq):
        done = set(pd.read_parquet(out_pq, columns=["hour_ts"])["hour_ts"].unique().tolist())
    status: dict[float, str] = {}
    if os.path.exists(out_hours):
        with open(out_hours, encoding="utf-8") as f:
            status = {float(r["hour_ts"]): r["status"] for r in csv.DictReader(f)}
    todo = sorted(h for h in hours if h not in done and status.get(h) != "missing")
    n_skip = len(hours) - len(todo)
    if a.max_hours:
        todo = todo[:a.max_hours]
    print(f"  {n_skip} hours already done or known missing, {len(todo)} to download now "
          f"(~0.5 MB each, decoded in memory)")

    buf: list[tuple] = []

    def flush():
        if not buf:
            return
        new = pd.DataFrame(buf, columns=["site_no", "hour_ts", "radius_km", "rain_mm", "coverage", "product"])
        if os.path.exists(out_pq):
            new = pd.concat([pd.read_parquet(out_pq), new], ignore_index=True)
        new.to_parquet(out_pq + ".part", index=False)
        os.replace(out_pq + ".part", out_pq)
        buf.clear()

    def save_status():
        with open(out_hours, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["hour_ts", "hour_utc", "reason", "status"])
            for h in sorted(hours):
                wr.writerow([int(h), _iso(h), hours[h], status.get(h, "ok" if h in done else "todo")])

    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init_worker, initargs=(all_g, radii)) as ex:
        futs = [ex.submit(process_hour, h) for h in todo]
        for n, fut in enumerate(bar(as_completed(futs), "MRMS hours", total=len(futs)), 1):
            h, st, rows = fut.result()
            status[h] = st
            buf.extend(rows)
            if st == "ok":
                done.add(h)
            if n % 200 == 0:
                flush()
                save_status()
    flush()
    save_status()
    n_ok = sum(1 for h in hours if h in done)
    n_miss = sum(1 for h in hours if status.get(h) == "missing")
    n_err = sum(1 for h in hours if str(status.get(h, "")).startswith("error"))
    size = os.path.getsize(out_pq) / 1e6 if os.path.exists(out_pq) else 0
    print(f"done: {n_ok}/{len(hours)} hours decoded, {n_miss} missing in the archive, {n_err} errors; "
          f"{out_pq} {size:.1f} MB in {timer.elapsed()}")


if __name__ == "__main__":
    main()
