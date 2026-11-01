"""Stdio MCP server for the agent tools (A4.1):  python -m agentic_ai.mcp_server

Wraps the PostgreSQL tools in `agentic_ai/tools.py` (docs/CONTRACTS.md "Agent tools") as MCP tools, so the graph's
agents reach the data through the Model Context Protocol. Read-only tools plus `save_advisory` (agents' own draft,
never Kafka, never email). The run log (`log_agent_event`, `log_agent_run`) is not an agent tool: the graph writes it
directly so the console's Agents tab stays in step.

Every tool returns JSON text. Environment: `PG_DSN` (default the docker database), optional `AGENTS_PG_SEARCH_PATH`
(a schema, used by the tests' throwaway schema). Client: `agentic_ai/mcp_client.py`.
"""
from __future__ import annotations

import json
import os
import sys

from mcp.server.mcpserver import MCPServer

from agentic_ai.tools import Tools

SERVER_NAME = "dews-agent-tools"
READ_TOOLS = ("get_alert", "get_recent_windows", "get_forecast", "get_official_levels", "get_past_floods", "get_rain",
              "get_neighbours", "get_gauge_info", "get_impacts", "get_episode_history", "get_edit_examples",
              "get_data_quality")
WRITE_TOOLS = ("save_advisory",)
TOOL_NAMES = READ_TOOLS + WRITE_TOOLS

mcp = MCPServer(SERVER_NAME, instructions="Flood-advisory agent tools on the DEWS PostgreSQL database. Read-only "
                "except save_advisory (an agents' draft; nothing is sent from here).")
_TOOLS: dict = {}


def _t() -> Tools:
    if "t" not in _TOOLS:
        t = Tools(dsn=os.getenv("PG_DSN") or None)
        sp = os.getenv("AGENTS_PG_SEARCH_PATH")
        if sp:
            with t.conn.cursor() as cur:
                cur.execute("SELECT set_config('search_path', %s, false)", (sp,))
        _TOOLS["t"] = t
    return _TOOLS["t"]


def _j(v) -> str:
    return json.dumps(v, default=str)


@mcp.tool(description="The alert as alerts.flood carried it, rebuilt from the tables (numbers from its own window).")
def get_alert(alert_id: str) -> str:
    return _j(_t().get_alert(alert_id))


@mcp.tool(description="Hourly windows for a gauge, oldest first, ending at `until` (ISO time).")
def get_recent_windows(site: str, hours: int = 12, until: str | None = None, mode: str | None = None) -> str:
    return _j(_t().get_recent_windows(site, hours, until, mode))


@mcp.tool(description="Latest forecast row at or before `until`; only the given horizon when horizon_h is set.")
def get_forecast(site: str, until: str | None = None, mode: str | None = None, horizon_h: float | None = None) -> str:
    return _j(_t().get_forecast(site, until, mode, horizon_h))


@mcp.tool(description="Official NWPS flood categories (action, minor, moderate, major) for a gauge.")
def get_official_levels(site: str) -> str:
    return _j(_t().get_official_levels(site))


@mcp.tool(description="Past floods at the gauge (n = 0 when the table is not loaded).")
def get_past_floods(site: str) -> str:
    return _j(_t().get_past_floods(site))


@mcp.tool(description="Catchment radar rain (1/3/6 h) from the windows; available false when none is joined.")
def get_rain(site: str, hours: int = 6, until: str | None = None, mode: str | None = None) -> str:
    return _j(_t().get_rain(site, hours, until, mode))


@mcp.tool(description="Upstream/downstream gauges first, then nearby ones, each with its recent stage change.")
def get_neighbours(site: str, until: str | None = None, mode: str | None = None, n: int = 3,
                   max_km: float = 60.0) -> str:
    return _j(_t().get_neighbours(site, until, mode, n, max_km))


@mcp.tool(description="Gauge name, NWPS id, county, state, location.")
def get_gauge_info(site: str) -> str:
    return _j(_t().get_gauge_info(site))


@mcp.tool(description="NWS impact statements at or below a category (name) or stage (ft); [] when none loaded.")
def get_impacts(site: str, level: str) -> str:
    try:
        lv: str | float = float(level)
    except ValueError:
        lv = level
    return _j(_t().get_impacts(site, lv))


@mcp.tool(description="Alerts of the same flood episode and their advisories.")
def get_episode_history(alert_id: str) -> str:
    return _j(_t().get_episode_history(alert_id))


@mcp.tool(description="Duty-officer edits of earlier advisories (wording examples; numbers are masked by the writer).")
def get_edit_examples(site: str | None = None, mode: str | None = None, limit: int = 2) -> str:
    return _j(_t().get_edit_examples(site, mode, limit))


@mcp.tool(description="Sensor-fault flag (ml.fault_rules.check_gauge) on the gauge's raw readings up to `until`.")
def get_data_quality(site: str, until: str | None = None, mode: str | None = None, hours: int = 48) -> str:
    return _j(_t().get_data_quality(site, until, mode, hours))


@mcp.tool(description="Save the agents' advisory draft (version 2+); earlier undecided agent drafts are superseded.")
def save_advisory(alert_id: str, text: str, confidence: str = "normal", reviewer_verdict: str | None = None,
                  source: str = "agents", version: int | None = None) -> str:
    return _j(_t().save_advisory(alert_id, text, confidence, reviewer_verdict, source, version))


def main() -> None:
    print(f"{SERVER_NAME}: MCP stdio server, {len(TOOL_NAMES)} tools", file=sys.stderr)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
