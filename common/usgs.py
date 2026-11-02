"""USGS Instantaneous Values (IV) service: URL building and parsing.

The parser follows the WaterML-JSON layout the IV service returns
(value -> timeSeries[] -> values[] -> value[] points, with a method[] entry per
values block). One site can return SEVERAL values blocks for the same parameter
(e.g. a primary and a backup sensor) at identical timestamps, so the series
identity is part of every reading's key.

VERIFY ON YOUR MACHINE: run `python -m tools.fetch_sample`, then `pytest`. The
test suite re-runs the parser against any real response saved in
tests/fixtures/live_usgs_iv.json, so a layout mismatch shows up as a failing test.
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlencode

from common import config
from common.timeutil import to_epoch


def build_iv_url(sites: Iterable[str], parameter_cds: Iterable[str] = (config.PARAM_STAGE,),
                 period: str | None = None, start: str | None = None, end: str | None = None,
                 base: str = config.USGS_IV_URL) -> str:
    """period like 'PT3H' / 'P7D', or start/end as 'YYYY-MM-DD' or ISO datetimes."""
    params = {
        "format": "json",
        "sites": ",".join(sites),
        "parameterCd": ",".join(parameter_cds),
        "siteStatus": "all",
    }
    if period and (start or end):
        raise ValueError("use either period or start/end, not both")
    if period:
        params["period"] = period
    if start:
        params["startDT"] = start
    if end:
        params["endDT"] = end
    return f"{base}?{urlencode(params, safe=',')}"


def _first(lst, key="value", default=None):
    if isinstance(lst, list) and lst:
        item = lst[0]
        return item.get(key, default) if isinstance(item, dict) else item
    return default


def parse_iv_json(doc: dict) -> list[dict]:
    """Flatten an IV response into one dict per reading.

    Skips points equal to the series' noDataValue and points whose value is not
    numeric. Raises ValueError if the top-level layout is not recognised, so a
    format change fails loudly instead of silently producing nothing.
    """
    try:
        series_list = doc["value"]["timeSeries"]
    except (KeyError, TypeError) as e:
        raise ValueError("unrecognised IV response: missing value.timeSeries") from e

    out: list[dict] = []
    for ts in series_list:
        src = ts.get("sourceInfo", {})
        var = ts.get("variable", {})
        site_no = _first(src.get("siteCode"))
        site_name = src.get("siteName")
        parameter = _first(var.get("variableCode"))
        unit = (var.get("unit") or {}).get("unitCode")
        no_data = var.get("noDataValue")
        for block_idx, block in enumerate(ts.get("values", [])):
            method = block.get("method") or [{}]
            series = str(method[0].get("methodID", block_idx))
            method_desc = method[0].get("methodDescription", "")
            for pt in block.get("value", []):
                try:
                    v = float(pt["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if no_data is not None and v == float(no_data):
                    continue
                rt = pt["dateTime"]
                out.append({
                    "site_no": site_no,
                    "site_name": site_name,
                    "parameter": parameter,
                    "unit": unit,
                    "series": series,
                    "method": method_desc,
                    "value": v,
                    "qualifiers": list(pt.get("qualifiers") or []),
                    "reading_time": rt,
                    "reading_ts": to_epoch(rt),
                })
    return out


def reading_key(r: dict) -> str:
    """Dedupe key: site + parameter + series + reading time (UTC epoch)."""
    return f"{r['site_no']}|{r['parameter']}|{r['series']}|{int(r['reading_ts'])}"


def is_provisional(r: dict) -> bool:
    return "P" in (r.get("qualifiers") or [])
