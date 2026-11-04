"""Flood forecast bulletin (C1.5): the email an approved advisory becomes. It reads like an ordinary email: one headline
sentence, the key figures in a small table, the approved advisory text, notes and who approved it. The HTML part follows the
reader's light or dark mode where the mail client supports prefers-color-scheme.

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

ISSUER = "DEWS flood early-warning prototype. Not an official NWS or government warning."
CAT_COLOUR = {"action": "#b8942f", "minor": "#c76f1c", "moderate": "#b83d32", "major": "#7a56ad"}


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


def model_wording(alert: dict) -> tuple[str, str | None]:
    """Headline and two-heads sentence of a model-probability alert: the v1 draft's probability headline and the agents' sentence
    (agentic_ai/advisory.py), so draft, advisory and email say the same thing. Never "forecast to reach": the probability raised
    this alert, not the stage forecast."""
    from agentic_ai import advisory
    from alerting import template
    head = next(ln for ln in template.render_v1(alert).splitlines() if "probability of crossing" in ln)
    return head, advisory.disagreement_sentence(alert)


def facts(rec: dict, alert: dict | None, site_name: str | None) -> dict:
    """One flat dict from the approved record plus whatever the alert row knows (details JSON, columns)."""
    a = alert or {}
    d = a.get("details") or {}
    levels = d.get("official_levels") or {}
    cur = d.get("current_stage")
    cat = a.get("category") or d.get("category") or "minor"
    model_head, uncertain = None, None
    if d.get("alert_rule") == "alert_by_model":
        p, h, name = a.get("probability") if a.get("probability") is not None else d.get("probability"), a.get("horizon_h") or d.get("horizon_h"), d.get("site_name") or site_name or rec["alert_id"].split("|")[0]
        try:
            model_head, uncertain = model_wording({**d, "category": cat, "probability": p, "horizon_h": h, "site_name": name, "site_no": a.get("site_no") or rec["alert_id"].split("|")[0],
                                                   "mode": rec.get("mode") or a.get("mode") or "LIVE", "official_levels": levels, "current_stage": cur})
        except Exception:  # noqa: BLE001 - a wording problem never stops an approved email; the plain sentence still names the probability
            tail = f" within {h:g} h" if isinstance(h, (int, float)) else ""
            model_head = f"{name}: probability of crossing {cat.upper()}{tail}: {p * 100:.0f}%." if isinstance(p, (int, float)) else f"{name}: model alert for {cat.upper()}{tail}."
    return {
        "model_headline": model_head, "uncertain": uncertain,
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
    if f.get("model_headline"):
        return f["model_headline"]
    cat = f["category"].upper()
    lvl = f["levels"].get(f["category"])
    h = f["horizon_h"]
    tail = f" within {h:g} h" if isinstance(h, (int, float)) else ""
    return f"The stage is forecast to reach the {cat} flood category{f' ({lvl:.1f} ft)' if lvl is not None else ''}{tail}."


def subject(f: dict) -> str:
    tag = "[SIMULATION] " if f["mode"] == "SIMULATION" else ""
    return f"{tag}Flood forecast: {f['site_name']} ({f['category'].upper()} category)"


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
    r = [("Latest reading", ft(f["current_stage"]) + (f" ({f['now_category']})" if f["now_category"] else ""))]
    r.append((f"Forecast in {h:g} h" if isinstance(h, (int, float)) else "Forecast", ft(f["forecast_stage"])))
    r.append(("Probability", f"{f['probability'] * 100:.0f} %" if isinstance(f["probability"], (int, float)) else "not given"))
    r.append(("Model", f["model"] or "not recorded"))
    return r


def _stages(f: dict) -> list[tuple[str, float]]:
    return [(c, f["levels"][c]) for c in CATEGORIES if c in f["levels"]]


def render_text(f: dict) -> str:
    out = []
    if f["mode"] == "SIMULATION":
        out += ["SIMULATION: replayed historical data, not a real event.", ""]
    out += [f"Flood forecast for {f['site_name']} (USGS {f['site_no']})", "", headline(f), ""]
    if f.get("uncertain"):
        out += [f["uncertain"], ""]
    rows = _rows(f) + ([("Official stages", ", ".join(f"{c} {v:.1f} ft" for c, v in _stages(f)) + " (NWS)")] if f["levels"] else [])
    out += [f"{k + ':':<18}{v}" for k, v in rows]
    if f["advisory_text"]:
        out += ["", "Approved advisory", *f["advisory_text"].splitlines()]
    out += ["", "Notes", *[f"- {n}" for n in notes(f)], "",
            f"Approved by {f['approved_by']} on {when(f['approved_at'])}.", f"Reference: {f['alert_id']}, advisory v{f['version']}", ISSUER]
    return "\n".join(out)


FONT = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
DARK_CSS = """@media (prefers-color-scheme: dark){
 .b-page{background:#0b1220!important}.b-card{background:#121b2c!important;border-color:#26344b!important}
 .b-ink{color:#e3ebf5!important}.b-muted{color:#93a4b8!important}.b-line{border-color:#26344b!important}
 .b-sim{background:#2a2413!important;color:#e6cf8f!important}.b-quote{border-color:#3a4c68!important}
}"""


def render_html(f: dict) -> str:
    e = html.escape
    accent = CAT_COLOUR.get(f["category"], "#5b7fa3")
    sim = ('<p class="b-sim" style="margin:0 0 18px;padding:9px 12px;border-radius:6px;background:#fbf5e4;color:#6b5518;font-size:13px">'
           '<b>Simulation.</b> This uses replayed historical data. It is not a real event.</p>') if f["mode"] == "SIMULATION" else ""
    rows = _rows(f)
    if f["levels"]:
        stages = ", ".join(f"<b>{c} {v:.1f} ft</b>" if c == f["category"] else f"{c} {v:.1f} ft" for c, v in _stages(f))
        rows_html = [(k, e(v)) for k, v in rows] + [("Official stages (NWS)", stages)]
    else:
        rows_html = [(k, e(v)) for k, v in rows]
    table = "".join(f'<tr><td class="b-muted b-line" style="padding:7px 16px 7px 0;border-top:1px solid #e3e9ef;color:#5d6e7f;white-space:nowrap;vertical-align:top">{e(k)}</td>'
                    f'<td class="b-ink b-line" style="padding:7px 0;border-top:1px solid #e3e9ef;color:#1a2a3a">{v}</td></tr>' for k, v in rows_html)
    adv = ""
    if f["advisory_text"]:
        adv = ('<p class="b-ink" style="margin:22px 0 6px;font-weight:600;color:#1a2a3a">Approved advisory</p>'
               '<div class="b-ink b-quote" style="border-left:2px solid #cdd8e3;padding:2px 0 2px 12px;color:#1a2a3a;white-space:pre-line">' + e(f["advisory_text"]) + '</div>')
    note_items = "".join(f'<li style="margin:3px 0">{e(n)}</li>' for n in notes(f))
    return f"""<!doctype html><html><head><meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark"><style>{DARK_CSS}</style></head>
<body class="b-page" style="margin:0;padding:0;background:#f1f5f9">
<table role="presentation" class="b-page" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" class="b-card" width="100%" cellpadding="0" cellspacing="0" style="max-width:580px;background:#ffffff;border:1px solid #e0e7ee;border-top:3px solid {accent};border-radius:8px;font-family:{FONT};font-size:14px;line-height:1.55">
<tr><td style="padding:24px 26px">
{sim}<h1 class="b-ink" style="margin:0 0 2px;font-size:19px;line-height:1.3;font-weight:600;color:#1a2a3a">Flood forecast for {e(f['site_name'])}</h1>
<p class="b-muted" style="margin:0 0 14px;font-size:13px;color:#5d6e7f">USGS gauge {e(f['site_no'])}</p>
<p class="b-ink" style="margin:0 0 16px;font-size:15px;color:#1a2a3a">{e(headline(f))}</p>
{f'<p class="b-ink" style="margin:0 0 16px;font-size:14px;color:#1a2a3a"><b>{e(f["uncertain"])}</b></p>' if f.get("uncertain") else ""}
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="font-size:14px;border-collapse:collapse">{table}</table>
{adv}
<p class="b-ink" style="margin:22px 0 4px;font-weight:600;color:#1a2a3a">Notes</p>
<ul class="b-ink" style="margin:0;padding-left:18px;color:#33465a">{note_items}</ul>
<p class="b-ink b-line" style="margin:22px 0 0;padding-top:14px;border-top:1px solid #e3e9ef;color:#1a2a3a">Approved by {e(f['approved_by'])} on {e(when(f['approved_at']))}.</p>
<p class="b-muted" style="margin:6px 0 0;font-size:12px;color:#5d6e7f">Reference: {e(f['alert_id'])}, advisory v{f['version']}<br>{e(ISSUER)}</p>
</td></tr></table></td></tr></table></body></html>"""
