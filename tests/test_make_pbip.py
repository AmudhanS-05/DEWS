"""The generated Power BI Project: structure and contents. Power BI itself is the only full check of the files."""
import json

from tools import make_pbip as mp

SCHEMA = {"gauges": [("site_no", "text"), ("name", "text")],
          "evaluation_runs": [("run_id", "bigint"), ("site_no", "text"), ("f1", "double precision"), ("false_alarms", "integer"), ("median_lead_time_h", "double precision"), ("recovery_time_s", "double precision"), ("status", "text")],
          "latency_waterfall": [("site_no", "text"), ("mode", "text"), ("feed_delay_s", "numeric")],
          "agent_run_metrics": [("run_id", "text"), ("site_no", "text"), ("tokens", "numeric"), ("duration_s", "numeric")],
          "alerts": [("site_no", "text"), ("details", "jsonb"), ("raised_at", "timestamp with time zone")]}
FILES = mp.files_for(SCHEMA, "localhost:5432", "disaster_warning")


def test_project_files_are_there_and_json_parses():
    for rel in ("DEWS.pbip", "DEWS.Report/definition.pbir", "DEWS.SemanticModel/definition.pbism", "DEWS.SemanticModel/definition/model.tmdl", "DEWS.SemanticModel/definition/relationships.tmdl"):
        assert rel in FILES
    assert json.loads(FILES["DEWS.pbip"])["artifacts"][0]["report"]["path"] == "DEWS.Report"
    assert json.loads(FILES["DEWS.Report/definition.pbir"])["datasetReference"]["byPath"]["path"] == "../DEWS.SemanticModel"


def test_report_config_includes_a_base_theme():
    report = json.loads(FILES["DEWS.Report/report.json"])
    config = json.loads(report["config"])
    assert config["themeCollection"]["baseTheme"]["name"] == "CY24SU06"


def test_tables_are_directquery_to_the_local_database_and_hold_no_secret():
    t = FILES["DEWS.SemanticModel/definition/tables/alerts.tmdl"]
    assert "mode: directQuery" in t and 'PostgreSQL.Database("localhost:5432", "disaster_warning")' in t and 'Item="alerts"' in t
    assert "details" not in t.split("partition")[0]                         # JSON columns are left out
    assert "dataType: dateTime" in t
    assert not any("password" in v.lower() for v in FILES.values())


def test_relationships_key_on_gauges_and_only_for_tables_present():
    r = FILES["DEWS.SemanticModel/definition/relationships.tmdl"]
    assert "fromColumn: alerts.site_no" in r and "toColumn: gauges.site_no" in r and "gauge_readings" not in r


def test_measures_requested_are_defined():
    tm = FILES["DEWS.SemanticModel/definition/tables/evaluation_runs.tmdl"]
    assert "measure 'F1 (mean of rows)' = AVERAGE(evaluation_runs[f1])" in tm and "False alarms" in tm and "Lead time" in tm
    assert "MEDIAN(latency_waterfall[feed_delay_s])" in FILES["DEWS.SemanticModel/definition/tables/latency_waterfall.tmdl"]
    assert "Tokens per run" in FILES["DEWS.SemanticModel/definition/tables/agent_run_metrics.tmdl"]


def test_model_refs_every_table():
    m = FILES["DEWS.SemanticModel/definition/model.tmdl"]
    assert all(f"ref table {t}" in m for t in SCHEMA)
