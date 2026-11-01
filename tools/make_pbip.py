"""Generate the Power BI Project dashboard/powerbi/DEWS.pbip (TMDL semantic model, DirectQuery to the local PostgreSQL).

    python -m tools.make_pbip                # needs PostgreSQL up: columns are read from the live schema

Every table and view of storage/schema.sql and storage/views.sql, relationships keyed on gauges[site_no], and the measures
below. No password is written: Power BI asks for it the first time. Open the .pbip in Power BI Desktop (Project files preview
feature on); the report starts with one empty page, built from dashboard/powerbi/README.md.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from common import config

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "powerbi"
NAME = "DEWS"
TYPES = {"text": "string", "character varying": "string", "integer": "int64", "bigint": "int64", "boolean": "boolean", "numeric": "double",
         "double precision": "double", "real": "double", "timestamp with time zone": "dateTime", "timestamp without time zone": "dateTime",
         "date": "dateTime", "jsonb": "string", "json": "string", "ARRAY": "string"}
SKIP_COLS = {"detail", "details", "output", "tool_calls", "qualifiers"}     # JSON and arrays are not usable over DirectQuery
# many (table.site_no) -> one (gauges.site_no); single direction, so a slicer on gauges filters them
RELATED = ["gauge_readings", "catchment_rain", "windowed_aggregates", "risk_scores", "alerts", "latency_trace", "evaluation_runs", "feed_staleness",
           "latency_waterfall", "alert_overview", "agent_run_metrics", "gauge_impacts", "past_floods"]
# tokens sit inside agent_events' JSON, which DirectQuery cannot read as a column: one native query, and a table in the model
NATIVE = {"agent_run_metrics": ("SELECT e.run_id, e.alert_id, COALESCE(a.mode, 'no alert row') AS mode, a.site_no, EXTRACT(EPOCH FROM (max(e.ts) - min(e.ts)))::float8 AS duration_s, "
                                "COALESCE(sum((e.detail->>'tokens')::numeric) FILTER (WHERE e.kind = 'end'), 0)::float8 AS tokens, count(*) FILTER (WHERE e.kind = 'tool_call') AS tool_calls, "
                                "count(*) FILTER (WHERE e.kind = 'revision') AS revisions, count(*) FILTER (WHERE e.kind = 'disagreement') AS disagreements "
                                "FROM agent_events e LEFT JOIN alerts a USING (alert_id) GROUP BY 1, 2, 3, 4",
                                [("run_id", "text"), ("alert_id", "text"), ("mode", "text"), ("site_no", "text"), ("duration_s", "double precision"), ("tokens", "double precision"),
                                 ("tool_calls", "bigint"), ("revisions", "bigint"), ("disagreements", "bigint")])}
MEASURES = {
    "evaluation_runs": [("F1 (mean of rows)", "AVERAGE(evaluation_runs[f1])", "0.00"), ("False alarms", "SUM(evaluation_runs[false_alarms])", "0"),
                        ("Lead time, median of rows (h)", "MEDIAN(evaluation_runs[median_lead_time_h])", "0.00"),
                        ("Drill recovery, median (s)", 'CALCULATE(MEDIAN(evaluation_runs[recovery_time_s]), evaluation_runs[status] = "passed")', "0.0"),
                        ("Drills failed", 'CALCULATE(COUNTROWS(evaluation_runs), evaluation_runs[status] = "failed")', "0")],
    "latency_waterfall": [(f"{label}, median (s)", f"MEDIAN(latency_waterfall[{col}])", "0.000") for col, label in
                          (("feed_delay_s", "Feed delay"), ("ingest_s", "Ingest"), ("windowing_s", "Windowing"), ("forecast_s", "Forecast"), ("alert_s", "Alert"),
                           ("human_s", "Human approval"), ("delivery_s", "Delivery"), ("net_total_s", "Total net of feed delay"), ("gross_total_s", "Total gross"))],
    "agent_run_metrics": [("Tokens per run", "AVERAGE(agent_run_metrics[tokens])", "0"), ("Agent runs", "COUNTROWS(agent_run_metrics)", "0"),
                          ("Agent duration, median (s)", "MEDIAN(agent_run_metrics[duration_s])", "0.0")],
}


def q(name: str) -> str:
    """TMDL needs quotes around names that are not plain identifiers."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def guid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "dews-pbip:" + seed))


def table_tmdl(table: str, columns: list[tuple[str, str]], server: str, database: str) -> str:
    lines = [f"table {q(table)}", f"\tlineageTag: {guid(table)}", ""]
    for name, expr, fmt in MEASURES.get(table, []):
        lines += [f"\tmeasure {q(name)} = {expr}", f"\t\tformatString: {fmt}", f"\t\tlineageTag: {guid(table + '/m/' + name)}", ""]
    for col, dtype in columns:
        if col in SKIP_COLS:
            continue
        t = TYPES.get(dtype, "string")
        lines += [f"\tcolumn {q(col)}", f"\t\tdataType: {t}", f"\t\tlineageTag: {guid(table + '/' + col)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {col}", ""]
    data = (f'Value.NativeQuery(Source, "{NATIVE[table][0]}", null, [EnableFolding=true])' if table in NATIVE
            else f'Source{{[Schema="public",Item="{table}"]}}[Data]')
    lines += [f"\tpartition {q(table)} = m", "\t\tmode: directQuery", "\t\tsource =", "\t\t\t\tlet",
              f'\t\t\t\t\tSource = PostgreSQL.Database("{server}", "{database}"),',
              f"\t\t\t\t\tData = {data}", "\t\t\t\tin", "\t\t\t\t\tData", ""]
    return "\n".join(lines)


def relationships_tmdl(tables: set[str]) -> str:
    out = []
    for t in RELATED:
        if t in tables:
            out += [f"relationship {guid('rel/' + t)}", f"\tfromColumn: {t}.site_no", "\ttoColumn: gauges.site_no", ""]
    for t in ("advisories", "agent_events"):      # these reach a gauge through their alert
        if t in tables and "alerts" in tables:
            out += [f"relationship {guid('rel/' + t)}", f"\tfromColumn: {t}.alert_id", "\ttoColumn: alerts.alert_id", ""]
    return "\n".join(out)


def model_tmdl(tables: list[str]) -> str:
    return "\n".join(["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3", "\tsourceQueryCulture: en-US",
                      "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", ""] + [f"ref table {q(t)}" for t in tables] + [""])


def files_for(schema: dict[str, list[tuple[str, str]]], server: str, database: str) -> dict[str, str]:
    """Relative path -> text, pure so it can be tested without a database."""
    sm, rp = f"{NAME}.SemanticModel", f"{NAME}.Report"
    tables = sorted(schema)
    out = {
        f"{NAME}.pbip": json.dumps({"version": "1.0", "artifacts": [{"report": {"path": rp}}], "settings": {"enableAutoRecovery": True}}, indent=2),
        f"{sm}/definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2),
        f"{sm}/.platform": json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                                       "metadata": {"type": "SemanticModel", "displayName": NAME},
                                       "config": {"version": "2.0", "logicalId": guid("sm")}}, indent=2),
        f"{sm}/definition/database.tmdl": "database\n\tcompatibilityLevel: 1600\n",
        f"{sm}/definition/model.tmdl": model_tmdl(tables),
        f"{sm}/definition/relationships.tmdl": relationships_tmdl(set(tables)),
        f"{rp}/definition.pbir": json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{sm}"}}}, indent=2),
        f"{rp}/.platform": json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                                       "metadata": {"type": "Report", "displayName": NAME},
                                       "config": {"version": "2.0", "logicalId": guid("rp")}}, indent=2),
        f"{rp}/report.json": json.dumps({"config": json.dumps({"version": "5.53", "themeCollection": {"baseTheme": {"name": "CY24SU06", "version": "5.53", "type": 2}}, "activeSectionIndex": 0}), "layoutOptimization": 0, "resourcePackages": [],
                                         "sections": [{"config": "{}", "displayName": "Page 1", "displayOption": 1, "filters": "[]", "height": 720, "name": "page1", "visualContainers": [], "width": 1280}]}, indent=2),
    }
    for t in tables:
        out[f"{sm}/definition/tables/{t}.tmdl"] = table_tmdl(t, schema[t], server, database)
    return out


def read_schema() -> dict[str, list[tuple[str, str]]]:
    import psycopg2
    with psycopg2.connect(config.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
        out: dict[str, list[tuple[str, str]]] = {}
        for t, c, d in cur.fetchall():
            out.setdefault(t, []).append((c, d))
    return {**out, **{t: cols for t, (_, cols) in NATIVE.items()}}


def main() -> int:
    schema = read_schema()
    for sub in (f"{NAME}.SemanticModel", f"{NAME}.Report"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    files = files_for(schema, "localhost:5432", "disaster_warning")
    for rel, text in files.items():
        p = OUT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(f"make_pbip: {len(schema)} tables and views, {len(files)} files, {sum(len(v) for v in MEASURES.values())} measures -> {OUT / (NAME + '.pbip')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
