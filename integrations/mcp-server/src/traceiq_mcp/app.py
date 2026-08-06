"""The FastMCP instance shared by every tool module and both transports.

Tool modules import `mcp` from here and register with `@mcp.tool()`. The
stdio entrypoint (server.py) and the HTTP entrypoint (server_http.py) both
import `tools.register_all()` which pulls in every tool module exactly once.

`stateless_http=True`: each Streamable-HTTP POST is independent, matching the
per-request API-key model (_context.py) — no server-side session state.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection for the streamable-HTTP transport.

    Without an explicit Host allowlist a browser page on any origin can drive
    this endpoint via DNS rebinding. Allowed hosts come from
    MCP_ALLOWED_HOSTS (comma-separated Host header values, `:*` wildcards
    permitted); the default covers localhost on any port. Set it to your
    deployment's public host(s) when serving off-box.
    """
    default_hosts = "127.0.0.1:*,localhost:*,[::1]:*"
    hosts = [
        h.strip()
        for h in os.environ.get("MCP_ALLOWED_HOSTS", default_hosts).split(",")
        if h.strip()
    ]
    origins: list[str] = []
    for h in hosts:
        origins.append(f"http://{h}")
        origins.append(f"https://{h}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


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
    transport_security=_transport_security(),
)
