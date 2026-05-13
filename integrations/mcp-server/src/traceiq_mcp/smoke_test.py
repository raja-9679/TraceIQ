"""Quick sanity-check that the configured API key + base URL work.

Run with `python -m traceiq_mcp.smoke_test` after exporting env vars.
"""
import asyncio
import json

from .client import TraceIQClient


async def _main() -> None:
    client = TraceIQClient()
    projects = await client.list_projects()
    print(json.dumps(projects, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
