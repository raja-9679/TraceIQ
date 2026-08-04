"""Tool registration. Importing the modules registers their @mcp.tool()
functions on the shared FastMCP instance (traceiq_mcp.app.mcp)."""


def register_all() -> None:
    # Import order groups tools in list_tools output: core → runs → impact →
    # authoring → quality → security → mobile.
    from traceiq_mcp.tools import (  # noqa: F401
        core,
        runs,
        impact,
        authoring,
        quality,
        security,
        mobile,
    )
