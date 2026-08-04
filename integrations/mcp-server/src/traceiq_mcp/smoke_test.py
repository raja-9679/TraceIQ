"""Smoke test for the v2 MCP server.

Offline part (always runs):
  - every expected tool is registered,
  - every tool publishes a non-null outputSchema (the typed-contract invariant),
  - a real in-process MCP client session can list the tools.

Online part (only when TRACEIQ_BASE_URL + TRACEIQ_API_KEY are set):
  - calls list_projects and describe_step_types against the live backend and
    checks structuredContent came back.

Run:  python -m traceiq_mcp.smoke_test
"""
from __future__ import annotations

import asyncio
import os
import sys

EXPECTED_TOOLS = {
    # core
    "list_workspaces", "list_projects", "create_project",
    "list_suites", "get_suite", "create_suite", "delete_suite",
    "list_cases", "get_case",
    # runs
    "run_suite", "get_run", "wait_for_run", "get_run_results",
    "get_failure_analysis", "analyze_run", "get_artifact_url",
    # impact / discovery
    "select_tests_for_diff", "discover_app_surface", "crawl_app_surface",
    "get_run_history", "set_code_paths", "bulk_set_code_paths",
    # authoring
    "get_authoring_guide", "describe_step_types",
    "propose_create_case", "propose_update_case", "propose_delete_case",
    "bulk_propose_cases", "list_case_proposals", "generate_case_proposal",
    # quality & results
    "get_quality_snapshot", "evaluate_quality_gate", "get_run_report",
    "get_test_effectiveness", "list_failure_clusters", "get_failure_cluster",
    "list_flakes", "list_heal_proposals",
    "create_comparison_run", "get_comparison",
    "ingest_junit_report", "list_external_results",
    # security
    "run_security_scan", "get_run_security_findings",
    "start_project_security_scan", "list_security_scans",
    "get_security_scan", "get_security_scan_diff",
    # mobile
    "list_app_builds", "get_app_build",
}


async def _amain() -> int:
    from mcp.shared.memory import create_connected_server_and_client_session

    from traceiq_mcp.app import mcp
    from traceiq_mcp.tools import register_all

    register_all()

    async with create_connected_server_and_client_session(
            mcp._mcp_server) as client:
        listed = await client.list_tools()
        by_name = {t.name: t for t in listed.tools}

        missing = EXPECTED_TOOLS - set(by_name)
        extra = set(by_name) - EXPECTED_TOOLS
        assert not missing, f"missing tools: {sorted(missing)}"
        assert not extra, f"unexpected tools: {sorted(extra)}"

        untyped = [n for n, t in by_name.items() if not t.outputSchema]
        assert not untyped, f"tools without outputSchema: {sorted(untyped)}"
        print(f"OK: {len(by_name)} tools registered, all with outputSchema")

        if os.environ.get("TRACEIQ_BASE_URL") and os.environ.get("TRACEIQ_API_KEY"):
            for tool in ("list_projects", "describe_step_types"):
                res = await client.call_tool(tool, {})
                assert not res.isError, f"{tool} errored: {res.content}"
                assert res.structuredContent is not None, \
                    f"{tool} returned no structuredContent"
            projects = (await client.call_tool("list_projects", {})).structuredContent
            print(f"OK: live backend reachable, "
                  f"{len(projects.get('items', []))} project(s) visible")
        else:
            print("SKIP: live checks (set TRACEIQ_BASE_URL + TRACEIQ_API_KEY)")

    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
