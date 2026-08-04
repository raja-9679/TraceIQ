"""stdio entrypoint — exposes TraceIQ as typed MCP tools to AI coding agents.

v2: built on FastMCP. Every tool declares Pydantic input AND output models, so
clients get inputSchema + outputSchema and validated structuredContent (with a
JSON text fallback for older clients). Tool names are unchanged from v1.

Env: TRACEIQ_BASE_URL + TRACEIQ_API_KEY (checked at startup so the agent sees
a clear error immediately), optional TRACEIQ_AGENT_ID / TRACEIQ_AGENT_SESSION_ID.
"""
from __future__ import annotations

import os

from traceiq_mcp.app import mcp
from traceiq_mcp.tools import register_all

register_all()


def main() -> None:
    """Entrypoint registered as the `traceiq-mcp` console script."""
    if not os.environ.get("TRACEIQ_BASE_URL") or not os.environ.get("TRACEIQ_API_KEY"):
        raise SystemExit(
            "TRACEIQ_BASE_URL and TRACEIQ_API_KEY must be set in the MCP server's environment."
        )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
