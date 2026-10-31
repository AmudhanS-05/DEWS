"""Flood forecast bulletin (C1.5): the email an approved advisory becomes, laid out like a public warning bulletin
(headline, issuer, target, situation, forecast, official stages, approved advisory text, notes).

Facts (stages, levels, probability, model) come from the alert record, so every number is formatted the same way
whatever wrote the advisory text. Missing facts are left out or said to be missing, never invented. The advisory
text itself is the reviewed, approved text; only long decimals in it are rounded for display (42.2609... -> 42.3).
A bulletin is a prototype product: it says so, and a SIMULATION alert says it is replayed historical data.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from common.nwps import CATEGORIES, category_of

ISSUER = "Flood early-warning prototype (RVCE student project). Not an official NWS or government warning."
CAT_COLOUR = {"action": "#b8942f", "minor": "#c76f1c", "moderate": "#b83d32", "major": "#7a56ad"}
WIDTH = 66


def ft(x) -> str:
    return f"{x:.1f} ft" if isinstance(x, (int, float)) else "not available"


def round_long_decimals(text: str) -> str:
    return re.sub(r"\d+\.\d{3,}", lambda m: f"{float(m.group()):.1f}", text)


def when(iso: str | None) -> str:
    if not iso:
        return "not recorded"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def facts(rec: dict, alert: dict | None, site_name: str | None) -> dict:
    """One flat dict from the approved record plus whatever the alert row knows (details JSON, columns)."""
    a = alert or {}
    d = a.get("details") or {}
    levels = d.get("official_levels") or {}
    cur = d.get("current_stage")
    cat = a.get("category") or d.get("category") or "minor"
    return {
        "alert_id": rec["alert_id"], "mode": rec.get("mode") or a.get("mode") or "LIVE", "site_no": a.get("site_no") or rec["alert_id"].split("|")[0],
        "site_name": d.get("site_name") or site_name or rec["alert_id"].split("|")[0], "category": cat, "kind": d.get("kind", "new"),
        "probability": a.get("probability") if a.get("probability") is not None else d.get("probability"),
        "horizon_h": a.get("horizon_h") if a.get("horizon_h") is not None else d.get("horizon_h"),
        "forecast_stage": d.get("forecast_stage"), "current_stage": cur, "levels": levels,
        "now_category": category_of(cur, levels) if isinstance(cur, (int, float)) and levels else None,
        "model": d.get("model"), "provisional": a.get("provisional_data") if a.get("provisional_data") is not None else d.get("provisional_data"),
        "approved_by": rec["approved_by"], "approved_at": rec["approved_at"], "version": rec["version"],
        "advisory_text": "" if rec["version"] == 1 else round_long_decimals(rec["text"]).strip(),
    }


def headline(f: dict) -> str:
    cat = f["category"].upper()
    lvl = f["levels"].get(f["category"])
    h = f["horizon_h"]
    tail = f" within {h:g} h" if isinstance(h, (int, float)) else ""
    return f"The stage is forecast to reach the {cat} flood category{f' ({lvl:.1f} ft)' if lvl is not None else ''}{tail}."


def subject(f: dict) -> str:
    tag = "[SIMULATION] " if f["mode"] == "SIMULATION" else ""
    return f"{tag}Flood forecast bulletin: {f['site_name']} ({f['category'].upper()})"


def notes(f: dict) -> list[str]:
    n = []
    if f["provisional"]:
        n.append("Readings are provisional USGS data and may be revised.")
    if f["probability"] is None:
        n.append(f"The {f['model'] or 'forecast'} model gives no probability; none is stated.")
    if f["now_category"] and CATEGORIES.index(f["now_category"]) >= CATEGORIES.index(f["category"]):
        n.append(f"The observed stage is already in the {f['now_category'].upper()} category, at or above the category named in the headline.")
    if f["kind"] == "upgrade":
        n.append("This bulletin upgrades an earlier forecast for the same rise.")
    n.append("A further bulletin follows only if an updated advisory is approved.")
    return n


def _rows(f: dict) -> list[tuple[str, str]]:
    h = f["horizon_h"]
    r = [("Observed stage", ft(f["current_stage"]) + (f"  ({f['now_category'].upper()} category)" if f["now_category"] else ""))]
    r.append((f"Forecast stage in {h:g} h" if isinstance(h, (int, float)) else "Forecast stage", ft(f["forecast_stage"])))
    r.append(("Probability", f"{f['probability'] * 100:.0f} %" if isinstance(f["probability"], (int, float)) else "not given by this model"))
    r.append(("Forecast model", f["model"] or "not recorded"))
    return r


def render_text(f: dict) -> str:
    bar = "=" * WIDTH
    out = [bar, " FLOOD FORECAST BULLETIN", f" Forecast to reach {f['category'].upper()} flood category", bar]
    if f["mode"] == "SIMULATION":
        out += ["", " *** SIMULATION: replayed historical data, not a real event ***"]
    out += ["", f" Issued      {when(f['approved_at'])}", f" Approved by {f['approved_by']}",
            f" Bulletin    {f['alert_id']}  (advisory v{f['version']})", f" Issuer      {ISSUER}", "",
            "■ Target", f"   {f['site_name']}  (USGS {f['site_no']})", "", "■ Headline", f"   {headline(f)}", "",
            "■ Current situation and forecast"]
    out += [f"   {k:<26}{v}" for k, v in _rows(f)]
    if f["levels"]:
        out += ["", "■ Official flood stages (NWS)"]
        out += [f"   {c.capitalize():<10}{f['levels'][c]:.1f} ft" for c in CATEGORIES if c in f["levels"]]
    if f["advisory_text"]:
        out += ["", "■ Advisory (reviewed and approved)"] + [f"   {ln}" if ln.strip() else "" for ln in f["advisory_text"].splitlines()]
    out += ["", "■ Notes"] + [f"   - {n}" for n in notes(f)] + ["", bar]
    return "\n".join(out)


def render_html(f: dict) -> str:
    e = html.escape
    col = CAT_COLOUR.get(f["category"], "#555")
    rows = "".join(f'<tr><td style="padding:5px 12px 5px 0;color:#5b6978;width:190px">{e(k)}</td><td style="padding:5px 0;font-weight:600">{e(v)}</td></tr>' for k, v in _rows(f))
    levels = ""
    if f["levels"]:
        cells = "".join(
            f'<td style="padding:6px 10px;border:1px solid #d3dae2;text-align:center;{"background:#f3f5f8;" if c == f["category"] else ""}">'
            f'<div style="font-size:11px;color:#5b6978;text-transform:uppercase;letter-spacing:.06em">{c}</div><div style="font-weight:600">{f["levels"][c]:.1f} ft</div></td>'
            for c in CATEGORIES if c in f["levels"])
        levels = f'<h3 style="{H3}">Official flood stages (NWS)</h3><table style="border-collapse:collapse;margin:0 0 4px"><tr>{cells}</tr></table>'
    adv = ""
    if f["advisory_text"]:
        adv = (f'<h3 style="{H3}">Advisory (reviewed and approved)</h3><div style="white-space:pre-wrap;border-left:3px solid #c2ccd6;padding:4px 0 4px 12px;margin:0">'
               f'{e(f["advisory_text"])}</div>')
    sim = ('<div style="margin:0 0 14px;padding:8px 12px;border:1px solid #a9801c;background:#fbf6e6;color:#6b5210;font-weight:600">'
           'SIMULATION: replayed historical data, not a real event</div>') if f["mode"] == "SIMULATION" else ""
    note_items = "".join(f"<li>{e(n)}</li>" for n in notes(f))
    return f"""<!doctype html><html><body style="margin:0;background:#eceff3;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1b2530">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #d3dae2;max-width:600px">
<tr><td style="background:{col};height:6px;font-size:0;line-height:0">&nbsp;</td></tr>
<tr><td style="padding:20px 24px 4px">
<div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#5b6978">Flood forecast bulletin</div>
<div style="font-size:22px;font-weight:650;margin:4px 0 2px">Forecast to reach {e(f['category'].upper())} flood category</div>
<div style="color:#5b6978;font-size:13px">{e(f['site_name'])} · USGS {e(f['site_no'])}</div></td></tr>
<tr><td style="padding:14px 24px 4px">{sim}
<table role="presentation" style="font-size:13px;color:#5b6978;margin:0 0 6px"><tr><td style="padding-right:14px">Issued</td><td style="color:#1b2530">{e(when(f['approved_at']))}</td></tr>
<tr><td style="padding-right:14px">Approved by</td><td style="color:#1b2530">{e(f['approved_by'])}</td></tr>
<tr><td style="padding-right:14px">Bulletin</td><td style="color:#1b2530">{e(f['alert_id'])} · advisory v{f['version']}</td></tr></table>
<h3 style="{H3}">Headline</h3><p style="margin:0;font-size:15px;line-height:1.5">{e(headline(f))}</p>
<h3 style="{H3}">Current situation and forecast</h3><table style="border-collapse:collapse;font-size:14px">{rows}</table>
{levels}{adv}
<h3 style="{H3}">Notes</h3><ul style="margin:0;padding-left:18px;font-size:13px;color:#33414f;line-height:1.5">{note_items}</ul></td></tr>
<tr><td style="padding:18px 24px 20px;font-size:11.5px;color:#5b6978;border-top:1px solid #e3e8ee;margin-top:14px">{e(ISSUER)}</td></tr>
</table></td></tr></table></body></html>"""


H3 = "font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#5b6978;margin:18px 0 6px;font-weight:650"
