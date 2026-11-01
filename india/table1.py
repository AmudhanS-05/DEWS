"""I2.1 / INDIA_STUDY step A: Table 1, US vs India, one row per property the pipeline needs.

US column: measured from india/figures/audit_us.csv (india.audit on the cached USGS history) plus the source facts
(access, thresholds) and the one live feed-delay sample recorded in FAQ.md.
India column: measured with the SAME audit function on India-WRIS exports (india.indiawris_loader) when any are under
--india-dir and thresholds are in india/thresholds.csv; otherwise every measured cell reads "pending (India-WRIS
downloads)". A separate column carries what the literature / INDIA_STUDY.md says, so a cited fact is never shown as
our measurement. Every row says where its numbers come from.

    python -m india.table1 [--us india/figures/audit_us.csv] [--india-dir data/raw/indiawris] \
        [--out india/figures/table1_us_vs_india.csv]          # also writes the .md next to it
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import median

PENDING = "pending (India-WRIS downloads)"
COLUMNS = ["property", "us_usgs", "us_provenance", "india_measured", "india_literature", "india_provenance"]


def _f(x) -> float | None:
    try:
        return None if x in (None, "", "None") else float(x)
    except (TypeError, ValueError):
        return None


def _med_range(vals, fmt="{:.0f}", unit="") -> str:
    v = [x for x in vals if x is not None]
    if not v:
        return "NA"
    u = f" {unit}" if unit else ""
    return f"{fmt.format(median(v))}{u} (range {fmt.format(min(v))} to {fmt.format(max(v))})"


def summarize(rows: list[dict]) -> dict:
    """Station audit rows (india.audit COLUMNS) -> the measured table cells."""
    if not rows:
        return {}
    ev = [_f(r["events"]) for r in rows]
    res = [r["rise_resolvable"] for r in rows if str(r.get("rise_resolvable")) in ("True", "False")]
    return {
        "n": len(rows),
        "interval": _med_range([_f(r["interval_min"]) for r in rows], "{:.0f}", "min"),
        "archive": _med_range([_f(r["archive_days"]) / 365.25 if _f(r["archive_days"]) else None for r in rows],
                              "{:.1f}", "years"),
        "missing": _med_range([_f(r["missing_pct"]) for r in rows], "{:.1f}", "%"),
        "gap": _med_range([_f(r["longest_gap_h"]) for r in rows], "{:.0f}", "h"),
        "events": f"{int(sum(e for e in ev if e is not None))} total, median {_med_range(ev)} per station",
        "rise": f"{sum(1 for x in res if str(x) == 'True')} of {len(res)} stations with events",
    }


def build_table(us_rows: list[dict], india_rows: list[dict], n_us_gauges_with_cats: int | None = None,
                n_india_with_levels: int = 0) -> list[dict]:
    us, ind = summarize(us_rows), summarize(india_rows)
    m = (lambda k: ind.get(k, PENDING)) if ind else (lambda k: PENDING)
    us_src = f"measured, india.audit on {us.get('n', 0)} USGS gauges" if us else "pending (run india.audit)"
    in_src = f"measured, india.audit on {ind['n']} India-WRIS stations" if ind else "literature only so far"
    u = (lambda k: us.get(k, "NA")) if us else (lambda k: "pending")
    cats = (f"yes: NWPS API, numeric categories for {n_us_gauges_with_cats} gauges in config/gauge_locations.csv"
            if n_us_gauges_with_cats is not None else "yes: NWPS API (JSON)")
    return [
        {"property": "Reporting interval (median per station)", "us_usgs": u("interval"), "us_provenance": us_src,
         "india_measured": m("interval"),
         "india_literature": "about 1 h at most CWC river stations in July 2022 (744 records in 31 days); dams and "
                             "reservoirs every 3 to 24 h; some stations 12 to 32 records a month; off-season unverified",
         "india_provenance": "user's --list-stations scan, July 2022, agency CWC, 83 stations (1 Nov 2026; stations.csv not in git)"},
        {"property": "Publication delay", "us_usgs": "24 to 69 min (one live sample)",
         "us_provenance": "measured once, FAQ.md (EDA, 3 Oct 2026)",
         "india_measured": "not measurable from archive files",
         "india_literature": "India-WRIS serves an archive (no live feed found); CWC real-time flood forecasts are "
                             "web pages and bulletins; delay not measured",
         "india_provenance": "INDIA_STUDY.md; India-WRIS API probes (31 Oct - 1 Nov 2026)"},
        {"property": "Archive length", "us_usgs": u("archive"), "us_provenance": us_src,
         "india_measured": m("archive"), "india_literature": "multi-year; one year per download request",
         "india_provenance": "INDIA_STUDY.md"},
        {"property": "Missing data vs the station's own interval", "us_usgs": u("missing"), "us_provenance": us_src,
         "india_measured": m("missing"), "india_literature": "gaps reported", "india_provenance": "CAMELS-IND, ESSD 2025"},
        {"property": "Longest gap", "us_usgs": u("gap"), "us_provenance": us_src, "india_measured": m("gap"),
         "india_literature": "", "india_provenance": ""},
        {"property": "Official thresholds machine-readable", "us_usgs": cats, "us_provenance": "source (NWPS API)",
         "india_measured": (f"{n_india_with_levels} stations with levels copied by hand into india/thresholds.csv"
                            if n_india_with_levels else PENDING),
         "india_literature": "warning / danger level / HFL in CWC web pages and daily PDF bulletins; no open API found",
         "india_provenance": "INDIA_STUDY.md"},
        {"property": "Measurement method", "us_usgs": "automatic sensors (USGS IV gage height)",
         "us_provenance": "source",
         "india_measured": (f"{sum('MANUAL' in str(r.get('method', '')).upper() for r in india_rows)} of "
                            f"{len(india_rows)} stations: manual staff-gauge readings"
                            if india_rows else PENDING),
         "india_literature": "a mix: 'MANUAL-Water Level by Staff Gauge (0)' (local gauge zero), 'MANUAL-WL by Staff "
                             "Gauge (MSL)' (mean sea level) and 'INSAT-WL by Telemetry'",
         "india_provenance": "user's --list-stations scan, July 2022, agency CWC, 83 stations (1 Nov 2026; stations.csv not in git)"},
        {"property": "Access method", "us_usgs": "REST API (JSON), no key", "us_provenance": "source",
         "india_measured": "manual browser download (Excel) or the India-WRIS POST API from an Indian connection "
                           "(no key; unreachable from the cloud session)" if ind else PENDING,
         "india_literature": "manual download, one year per Excel file; POST API (OpenAPI spec, no auth)",
         "india_provenance": "INDIA_STUDY.md; docs/indiawris_api.json"},
        {"property": "Share of stations free to use", "us_usgs": "all USGS IV data are public",
         "us_provenance": "source terms",
         "india_measured": "", "india_literature": "645 of 4,824 listed gauges (Peninsular)",
         "india_provenance": "CAMELS-IND paper, ESSD 2025"},
        {"property": "Events above the threshold (US: NWPS minor; India: CWC danger level)", "us_usgs": u("events"),
         "us_provenance": us_src, "india_measured": m("events"), "india_literature": "",
         "india_provenance": "" if ind else "needs downloads + india/thresholds.csv"},
        {"property": "Rise time resolvable (interval < median rise)", "us_usgs": u("rise"), "us_provenance": us_src,
         "india_measured": m("rise"), "india_literature": "hourly monsoon data resolves rises longer than ~1 h; dams (3 to 24 h) do not; "
                             "not measured per event",
         "india_provenance": "user's --list-stations scan, July 2022, agency CWC, 83 stations (1 Nov 2026; stations.csv not in git)"},
    ]


def to_markdown(table: list[dict]) -> str:
    hdr = ["Property", "US (USGS IV)", "US source", "India measured", "India literature", "India source"]
    out = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for r in table:
        out.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in COLUMNS) + " |")
    return "\n".join(out) + "\n"


def audit_india(india_dir: str, thresholds_path: str) -> tuple[list[dict], int]:
    """Audit rows per India-WRIS station; each row also carries `method` (the export's description field)."""
    from india import indiawris_loader as L
    from india.audit import audit_station
    if not os.path.isdir(india_dir):
        return [], 0
    th = L.load_thresholds(thresholds_path) if os.path.exists(thresholds_path) else {}
    rows = []
    for code, rs in sorted(L.load_folder(india_dir).items()):
        danger = (th.get(code) or {}).get("danger_level_m")
        row = audit_station(rs, danger)
        row["method"] = (rs[0].get("method") or "") if rs else ""
        rows.append(row)
        print(f"  india {code}: {len(rs)} readings, danger level {danger if danger is not None else 'MISSING'}")
    return rows, sum(1 for t in th.values() if t.get("danger_level_m") is not None)


def main(argv=None):
    from ml.progress import Timer
    p = argparse.ArgumentParser(description="Table 1: US vs India data audit.")
    p.add_argument("--us", default="india/figures/audit_us.csv")
    p.add_argument("--india-dir", default=os.path.join(os.getenv("DATA_DIR", "data"), "raw", "indiawris"))
    p.add_argument("--thresholds", default="india/thresholds.csv")
    p.add_argument("--gauges", default="config/gauge_locations.csv")
    p.add_argument("--out", default="india/figures/table1_us_vs_india.csv")
    a = p.parse_args(argv)
    timer = Timer()
    print(f"india.table1: US audit {a.us}, India exports {a.india_dir} -> {a.out}")
    us_rows = list(csv.DictReader(open(a.us, encoding="utf-8"))) if os.path.exists(a.us) else []
    if not us_rows:
        print(f"  US audit missing ({a.us}); run python -m india.audit first. US cells read 'pending'.")
    n_cats = None
    if os.path.exists(a.gauges):
        from common.nwps import CATEGORIES
        n_cats = sum(1 for g in csv.DictReader(open(a.gauges, encoding="utf-8"))
                     if all(_f(g.get(c)) is not None for c in CATEGORIES))
    india_rows, n_lv = audit_india(a.india_dir, a.thresholds)
    if not india_rows:
        print(f"  no India-WRIS exports under {a.india_dir}: India cells read '{PENDING}'")
    table = build_table(us_rows, india_rows, n_cats, n_lv)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(table)
    with open(a.out.replace(".csv", ".md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(table))
    if india_rows:
        from india.audit import write_csv
        write_csv(india_rows, os.path.join(os.path.dirname(a.out), "audit_india.csv"))
    print(f"done: {len(table)} properties, US {len(us_rows)} gauges, India {len(india_rows)} stations -> {a.out} "
          f"(+ .md) in {timer.elapsed()}")


if __name__ == "__main__":
    main()
