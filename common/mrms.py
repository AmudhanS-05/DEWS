"""NOAA MRMS hourly QPE: bucket keys, in-memory GRIB2 decode and mean depth over a circle around each gauge.

Shared by the live/replay producer (producers/mrms_qpe_producer.py) and the ML history download
(ml/mrms_history.py, which can import these names unchanged: same signatures and behaviour as its own helpers).

Grid (confirmed on real files, 3 and 31 Oct 2026): 7000 x 3500 cells of 0.01 deg, first cell 54.995 N / 230.005 E,
rows north -> south, columns west -> east; values in mm for the hour ENDING at the file's valid time (HH:00:00);
-3 = no radar coverage, 9999 = GRIB missing value. Both are excluded from the mean.
"""
from __future__ import annotations

import gzip
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
# Short names -> bucket product folders. Latency = S3 LastModified minus the valid time, measured 31 Oct 2026
# 13Z-17Z (see FAQ.md): radaronly ~3 min (a rolling 1 h sum every 2 min; we use the HH:00
# file only), pass1 ~15.5 min, pass2 ~57 min (gauge-corrected; the ML training product).
PRODUCTS = {
    "radaronly": "RadarOnly_QPE_01H_00.00",
    "pass1": "MultiSensor_QPE_01H_Pass1_00.00",
    "pass2": "MultiSensor_QPE_01H_Pass2_00.00",
}
PRODUCT = PRODUCTS["pass2"]          # default for history (ml) and replay
ARCHIVE_START = datetime(2020, 10, 14, 0, tzinfo=timezone.utc).timestamp()   # first hour in noaa-mrms-pds
EARTH_R_KM = 6371.0088
_KEY_RE = re.compile(r"_(\d{8})-(\d{6})\.grib2\.gz$")


def product_name(p: str) -> str:
    """'pass1' -> 'MultiSensor_QPE_01H_Pass1_00.00'; a full folder name is returned unchanged."""
    return PRODUCTS.get(p.lower(), p)


# ---------- pure helpers (tested) ----------

def hour_key(hour_ts: float, product: str = PRODUCT) -> str:
    d = datetime.fromtimestamp(hour_ts, timezone.utc)
    return f"CONUS/{product}/{d:%Y%m%d}/MRMS_{product}_{d:%Y%m%d-%H%M%S}.grib2.gz"


def key_time(key: str) -> float | None:
    """Valid time (epoch) encoded in a bucket key, or None."""
    m = _KEY_RE.search(key)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()


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


class CircleCache:
    """Circle indices per (grid geometry, site, radius), computed once and reused for every hour."""

    def __init__(self):
        self._idx: dict = {}

    def get(self, geom: dict, site_no: str, lat: float, lon: float, radius_km: float) -> np.ndarray:
        k = (tuple(sorted(geom.items())), site_no, float(radius_km))
        if k not in self._idx:
            self._idx[k] = circle_indices(lat, lon, radius_km, geom)
        return self._idx[k]


def gauge_means(values: np.ndarray, geom: dict, missing: float | None, gauges: list[dict], radii: list[float],
                cache: CircleCache | None = None) -> dict[str, dict[float, tuple[float, float, int]]]:
    """{site_no: {radius_km: (mean_mm, coverage, n_cells)}} for every gauge ({site_no, lat, lon})."""
    cache = cache or CircleCache()
    out: dict[str, dict[float, tuple[float, float, int]]] = {}
    for g in gauges:
        per = {}
        for rad in radii:
            idx = cache.get(geom, g["site_no"], g["lat"], g["lon"], rad)
            mean, cov = circle_mean(values, idx, missing)
            per[float(rad)] = (mean, cov, int(len(idx)))
        out[g["site_no"]] = per
    return out


def parse_listing(xml_text: str) -> tuple[list[tuple[str, str]], str | None]:
    """S3 ListObjectsV2 XML -> ([(key, last_modified_iso)], next continuation token or None)."""
    root = ET.fromstring(xml_text)
    ns = root.tag[:root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    items = [(c.findtext(f"{ns}Key"), c.findtext(f"{ns}LastModified")) for c in root.iter(f"{ns}Contents")]
    tok = root.findtext(f"{ns}NextContinuationToken") if root.findtext(f"{ns}IsTruncated") == "true" else None
    return items, tok


# ---------- decode (needs eccodes) ----------

def geometry(h) -> dict:
    import eccodes
    g = {k: eccodes.codes_get(h, k) for k in ("Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
                                               "longitudeOfFirstGridPointInDegrees", "iDirectionIncrementInDegrees",
                                               "jDirectionIncrementInDegrees", "jScansPositively", "iScansNegatively")}
    if g["jScansPositively"] != 0 or g["iScansNegatively"] != 0:
        raise ValueError(f"unexpected MRMS scan order {g}")
    return {"ni": g["Ni"], "nj": g["Nj"], "lat_first": g["latitudeOfFirstGridPointInDegrees"],
            "lon_first": g["longitudeOfFirstGridPointInDegrees"], "dlat": g["jDirectionIncrementInDegrees"],
            "dlon": g["iDirectionIncrementInDegrees"]}


def decode(body: bytes) -> tuple[dict, np.ndarray, float | None]:
    """Gzipped (or plain) GRIB2 bytes -> (geometry, flat values, missing value). Nothing is written to disk."""
    import eccodes
    raw = gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
    h = eccodes.codes_new_from_message(raw)
    try:
        geom = geometry(h)
        try:
            missing = float(eccodes.codes_get(h, "missingValue"))
        except Exception:  # noqa: BLE001
            missing = None
        vals = eccodes.codes_get_values(h)
    finally:
        eccodes.codes_release(h)
    return geom, vals, missing
