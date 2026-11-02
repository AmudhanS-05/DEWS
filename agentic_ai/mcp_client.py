"""MCP client for the graph (A4.1): the agents call their tools through `agentic_ai.mcp_server` (stdio subprocess),
with a direct Python call as the fallback.

`McpTools(direct)` looks like `tools.Tools`: every tool the server offers (mcp_server.TOOL_NAMES) goes over MCP;
everything else (the run log `log_agent_event` / `log_agent_run`, internal helpers) goes to `direct`. If the server
cannot start, a call fails or times out (CALL_TIMEOUT_S), the same call is made directly, so a broken MCP path never
changes the outcome (it shows as `via: direct (mcp failed: ...)` in the tool_call event).

Selection: `AGENT_TOOLS` = `mcp` (default) | `direct`. `graph.run_alert` wraps a real database `Tools` only; the
in-memory test fakes always run direct. The server subprocess is started once per process (about 1 s) and stopped at
exit; it gets the same database (DSN and search_path) as `direct`.
"""
from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import os
import sys
import threading
import time
from pathlib import Path

from agentic_ai.env import load_env

CALL_TIMEOUT_S = 10.0
START_TIMEOUT_S = 20.0
ROOT = Path(__file__).resolve().parents[1]


def mode() -> str:
    load_env()
    m = os.getenv("AGENT_TOOLS", "mcp").strip().lower()
    return m if m in ("mcp", "direct") else "mcp"


def _conn_env(conn) -> dict:
    """PG_DSN (with password) and search_path of a psycopg2 connection, for the server subprocess."""
    env = {}
    try:
        i = conn.info
        parts = {"host": i.host, "port": i.port, "dbname": i.dbname, "user": i.user, "password": i.password}
        env["PG_DSN"] = " ".join(f"{k}={v}" for k, v in parts.items() if v not in (None, ""))
        with conn.cursor() as cur:
            cur.execute("SHOW search_path")
            env["AGENTS_PG_SEARCH_PATH"] = cur.fetchone()[0]
    except Exception:  # noqa: BLE001 - fall back to the server's own PG_DSN
        pass
    return env


class McpSession:
    """One stdio MCP session on a background event loop (the graph is synchronous)."""

    def __init__(self, env: dict | None = None):
        self.env = env or {}
        self.error: str | None = None
        self.tool_names: set[str] = set()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._session = None
        self._thread = threading.Thread(target=self._run, name="mcp-client", daemon=True)
        self.started_s: float | None = None

    def start(self) -> "McpSession":
        t0 = time.monotonic()
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT_S):
            self.error = self.error or f"server did not start within {START_TIMEOUT_S:g} s"
        self.started_s = round(time.monotonic() - t0, 2)
        return self

    @property
    def ok(self) -> bool:
        return self._session is not None and self.error is None

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            self.error = self.error or f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            self._ready.set()

    async def _main(self) -> None:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        env = {k: v for k, v in os.environ.items()} | self.env
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentic_ai.mcp_server"], env=env,
                                       cwd=str(ROOT))
        self._stop = asyncio.Event()
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    self.tool_names = {t.name for t in (await s.list_tools()).tools}
                    self._session = s
                    self._ready.set()
                    await self._stop.wait()
        self._session = None

    def call(self, name: str, arguments: dict, timeout: float = CALL_TIMEOUT_S):
        """Call a tool; returns the decoded JSON result. Raises on any failure (the caller falls back)."""
        if not self.ok:
            raise RuntimeError(self.error or "MCP session not running")
        fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, arguments), self._loop)
        r = fut.result(timeout)
        text = "".join(getattr(c, "text", "") for c in r.content)
        if getattr(r, "is_error", False):
            raise RuntimeError(text[:200])
        return json.loads(text) if text else None

    def close(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(5)


_SESSIONS: dict[tuple, McpSession] = {}
_LOCK = threading.Lock()


def session_for(env: dict) -> McpSession:
    """One server per database (DSN + search_path) per process."""
    key = tuple(sorted(env.items()))
    with _LOCK:
        s = _SESSIONS.get(key)
        if s is None or (not s.ok and s.error):
            s = McpSession(env).start()
            _SESSIONS[key] = s
        return s


@atexit.register
def close_all() -> None:
    for s in list(_SESSIONS.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    _SESSIONS.clear()


class McpTools:
    """Tools-like proxy: MCP for the server's tools, direct for the rest and as the fallback."""

    def __init__(self, direct, session: McpSession | None = None):
        self.direct = direct
        self.session = session if session is not None else session_for(_conn_env(getattr(direct, "conn", None)))
        self.last_via: str = "direct"
        self.calls = {"mcp": 0, "direct": 0, "fallback": 0}

    def __getattr__(self, name):
        target = getattr(self.direct, name)
        from agentic_ai.mcp_server import TOOL_NAMES
        if name not in TOOL_NAMES or not callable(target):
            return target
        sig = inspect.signature(target)

        def call(*args, **kwargs):
            if self.session.ok and name in self.session.tool_names:
                try:
                    bound = sig.bind(*args, **kwargs)
                    arguments = {k: _plain(v) for k, v in bound.arguments.items() if v is not None}
                    if name == "get_impacts":          # the server takes a category name or a stage as text
                        arguments["level"] = str(arguments["level"])
                    res = self.session.call(name, arguments)
                    self.last_via = "mcp"
                    self.calls["mcp"] += 1
                    return res
                except Exception as e:  # noqa: BLE001 - fallback: the same call, directly
                    self.last_via = f"direct (mcp failed: {type(e).__name__}: {str(e)[:120]})"
                    self.calls["fallback"] += 1
                    return target(*args, **kwargs)
            self.last_via = f"direct (mcp unavailable: {self.session.error or 'tool not offered'})"
            self.calls["fallback"] += 1
            return target(*args, **kwargs)
        return call


def _plain(v):
    from datetime import datetime, timezone
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def wrap(tools):
    """The graph's tool object: McpTools over a real database Tools when AGENT_TOOLS=mcp, else `tools` unchanged."""
    from agentic_ai.tools import Tools
    if mode() != "mcp" or type(tools) is not Tools:
        return tools
    try:
        return McpTools(tools)
    except Exception:  # noqa: BLE001 - no mcp package or no server: direct calls
        return tools
