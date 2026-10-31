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
    r = [("OBSERVED", ft(f["current_stage"]) + (f" ({f['now_category'].upper()})" if f["now_category"] else ""))]
    r.append((f"FORECAST {h:g} H" if isinstance(h, (int, float)) else "FORECAST", ft(f["forecast_stage"])))
    r.append(("PROBABILITY", f"{f['probability'] * 100:.0f} %" if isinstance(f["probability"], (int, float)) else "not given"))
    r.append(("MODEL", f["model"] or "not recorded"))
    return r


def meter(category: str) -> str:
    """Category meter, one block per official category up to the forecast one: action > minor > moderate > major."""
    i = CATEGORIES.index(category) + 1 if category in CATEGORIES else 0
    return "■" * i + "□" * (len(CATEGORIES) - i)


def render_text(f: dict) -> str:
    bar, thin = "=" * WIDTH, "-" * WIDTH
    out = [f"▲ FLOOD FORECAST BULLETIN ▲  v{f['version']}", bar, f"{f['category'].upper()} FLOOD CATEGORY FORECAST"]
    if f["mode"] == "SIMULATION":
        out.append("[ SIMULATION: REPLAYED HISTORICAL DATA, NOT A REAL EVENT ]")
    out += [thin, f"{'TARGET':<14}{f['site_name']} (USGS {f['site_no']})", f"{'CATEGORY':<14}{f['category'].upper()}  [{meter(f['category'])}]  action / minor / moderate / major"]
    out += [f"{k:<14}{v}" for k, v in _rows(f)]
    out += [thin, "HEADLINE", f"  {headline(f)}"]
    if f["levels"]:
        out += ["", "OFFICIAL STAGES (NWS)", "  " + "  |  ".join(f"{c.upper()} {f['levels'][c]:.1f}" for c in CATEGORIES if c in f["levels"]) + "  (ft)"]
    if f["advisory_text"]:
        out += ["", "ADVISORY (REVIEWED AND APPROVED)"] + [f"  {ln}" if ln.strip() else "" for ln in f["advisory_text"].splitlines()]
    out += ["", "NOTES"] + [f"  - {n}" for n in notes(f)] + [thin,
            f"ISSUED {when(f['approved_at'])}  |  APPROVED BY {f['approved_by']}", f"BULLETIN {f['alert_id']}  (advisory v{f['version']})", ISSUER, bar]
    return "\n".join(out)


BLACK, AMBER, PAPER, INK, MUTED = "#101214", "#d9a441", "#e9ebee", "#1b2530", "#5b6978"
MONO = "Consolas,Menlo,'Courier New',monospace"
LABEL = f"font-family:{MONO};font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:{MUTED}"


def _stripe() -> str:
    cells = "".join(f'<td width="15" height="7" style="background:{AMBER if i % 2 == 0 else BLACK};font-size:0;line-height:0">&nbsp;</td>' for i in range(40))
    return f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr>{cells}</tr></table>'


def render_html(f: dict) -> str:
    e = html.escape
    idx = CATEGORIES.index(f["category"]) + 1 if f["category"] in CATEGORIES else 0
    meter_cells = "".join(
        f'<td style="padding:0 3px 0 0"><div style="height:8px;background:{AMBER if i < idx else "#2a2f35"}"></div>'
        f'<div style="font-family:{MONO};font-size:9px;letter-spacing:.14em;color:{"#e9edf1" if i < idx else "#6e7a87"};padding-top:4px;text-transform:uppercase">{c}</div></td>'
        for i, c in enumerate(CATEGORIES))
    sim = (f'<tr><td style="background:{BLACK};border-top:1px solid #2a2f35;padding:9px 24px;font-family:{MONO};font-size:11px;letter-spacing:.14em;color:{AMBER};font-weight:bold">'
           '&#9650; SIMULATION &mdash; REPLAYED HISTORICAL DATA &mdash; NOT A REAL EVENT</td></tr>') if f["mode"] == "SIMULATION" else ""
    tiles = "".join(
        f'<td width="50%" style="padding:0 6px 6px 0"><div style="background:#f2f4f6;border-left:3px solid {AMBER};padding:9px 12px"><div style="{LABEL}">{e(k)}</div>'
        f'<div style="font-family:{MONO};font-size:20px;font-weight:bold;color:{INK};padding-top:3px">{e(v)}</div></div></td>' + ("</tr><tr>" if n % 2 else "")
        for n, (k, v) in enumerate(_rows(f)))
    ladder = ""
    if f["levels"]:
        cells = "".join(
            f'<td style="padding:7px 4px;border:1px solid #cfd6dd;text-align:center;{"background:%s;color:#fff;" % BLACK if c == f["category"] else ""}">'
            f'<div style="font-family:{MONO};font-size:9px;letter-spacing:.14em;text-transform:uppercase;{"color:%s" % AMBER if c == f["category"] else "color:" + MUTED}">{c}</div>'
            f'<div style="font-family:{MONO};font-weight:bold;font-size:14px">{f["levels"][c]:.1f} ft</div></td>' for c in CATEGORIES if c in f["levels"])
        ladder = f'<div style="{LABEL};margin:20px 0 6px">Official flood stages (NWS)</div><table role="presentation" width="100%" style="border-collapse:collapse"><tr>{cells}</tr></table>'
    adv = ""
    if f["advisory_text"]:
        adv = (f'<div style="{LABEL};margin:20px 0 6px">Advisory (reviewed and approved)</div><div style="font-family:{MONO};font-size:12.5px;line-height:1.6;white-space:pre-wrap;'
               f'background:#f2f4f6;border-left:3px solid {BLACK};padding:10px 14px">{e(f["advisory_text"])}</div>')
    note_items = "".join(f"<li>{e(n)}</li>" for n in notes(f))
    return f"""<!doctype html><html><body style="margin:0;background:{PAPER};font-family:Segoe UI,Helvetica,Arial,sans-serif;color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid {BLACK};max-width:600px;border-collapse:collapse">
<tr><td style="padding:0">{_stripe()}</td></tr>
<tr><td style="background:{BLACK};padding:20px 24px 16px">
<div style="font-family:{MONO};font-size:11px;letter-spacing:.24em;color:{AMBER}">&#9650; FLOOD FORECAST BULLETIN &nbsp;/&nbsp; V{f['version']}</div>
<div style="font-size:26px;line-height:1.15;font-weight:800;letter-spacing:.02em;text-transform:uppercase;color:#f3f5f7;margin:8px 0 4px">{e(f['category'])} flood category forecast</div>
<div style="font-size:13px;color:#9aa5b1;margin-bottom:14px">{e(f['site_name'])} &middot; USGS {e(f['site_no'])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{meter_cells}</tr></table></td></tr>
{sim}
<tr><td style="padding:20px 24px 6px">
<div style="{LABEL};margin:0 0 6px">Headline</div><p style="margin:0 0 16px;font-size:15px;line-height:1.5;font-weight:600">{e(headline(f))}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{tiles}</tr></table>
{ladder}{adv}
<div style="{LABEL};margin:20px 0 6px">Notes</div><ul style="margin:0;padding-left:18px;font-size:13px;color:#33414f;line-height:1.55">{note_items}</ul></td></tr>
<tr><td style="background:{BLACK};padding:14px 24px;font-family:{MONO};font-size:11px;line-height:1.7;color:#9aa5b1">
ISSUED {e(when(f['approved_at']))} &nbsp;|&nbsp; APPROVED BY {e(f['approved_by'])}<br>BULLETIN {e(f['alert_id'])} &middot; ADVISORY V{f['version']}<br>
<span style="color:#7b8794">{e(ISSUER)}</span></td></tr>
<tr><td style="padding:0">{_stripe()}</td></tr>
</table></td></tr></table></body></html>"""
