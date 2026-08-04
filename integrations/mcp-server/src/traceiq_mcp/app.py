"""The FastMCP instance shared by every tool module and both transports.

Tool modules import `mcp` from here and register with `@mcp.tool()`. The
stdio entrypoint (server.py) and the HTTP entrypoint (server_http.py) both
import `tools.register_all()` which pulls in every tool module exactly once.

`stateless_http=True`: each Streamable-HTTP POST is independent, matching the
per-request API-key model (_context.py) — no server-side session state.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "traceiq",
    instructions=(
        "TraceIQ is a UI/API/mobile test platform. Start every session with "
        "get_authoring_guide + describe_step_types, then discover_app_surface "
        "for your project. After changing application code, call "
        "select_tests_for_diff with the changed file paths — it returns which "
        "cases to run and which likely need editing (suggested_action). "
        "Keep every case's code_paths current via set_code_paths; that mapping "
        "is what makes future impact analysis work."
    ),
    stateless_http=True,
    streamable_http_path="/mcp",
)
