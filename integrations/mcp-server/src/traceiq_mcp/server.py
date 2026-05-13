"""MCP server entrypoint — exposes TraceIQ as a tool to AI coding agents.

Uses the official `mcp` Python SDK with stdio transport. Tools are
registered with JSON schemas so clients (Claude Code, Cursor) can call
them with typed arguments.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .client import TraceIQClient


TERMINAL_STATUSES = {"passed", "failed", "error"}


def _new_client() -> TraceIQClient:
    return TraceIQClient()


def _format_run_summary(run: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "id": run.get("id"),
            "status": run.get("status"),
            "suite_name": run.get("suite_name"),
            "test_case_name": run.get("test_case_name"),
            "total_tests": run.get("total_tests"),
            "passed_tests": run.get("passed_tests"),
            "failed_tests": run.get("failed_tests"),
            "duration_ms": run.get("duration_ms"),
            "error_message": run.get("error_message"),
            "git_commit": run.get("git_commit"),
            "git_branch": run.get("git_branch"),
            "triggered_by": run.get("triggered_by"),
            "agent_id": run.get("agent_id"),
            "trace_url": run.get("trace_url"),
            "video_url": run.get("video_url"),
        },
        indent=2,
    )


server = Server("traceiq")


@server.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name="list_projects",
            description="List TraceIQ projects this agent can see.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_suites",
            description="List test suites within a TraceIQ project.",
            inputSchema={
                "type": "object",
                "properties": {"project_id": {"type": "integer"}},
                "required": ["project_id"],
            },
        ),
        Tool(
            name="run_suite",
            description=(
                "Trigger a regression run for a TraceIQ suite. Tag the run "
                "with the git commit/branch/PR the agent is verifying so "
                "results can be tied back to the change."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "suite_id": {"type": "integer"},
                    "case_id": {"type": "integer", "description": "Optional. Run only this test case."},
                    "browser": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['chromium']"},
                    "device": {"type": "array", "items": {"type": "string"}},
                    "git_commit": {"type": "string"},
                    "git_branch": {"type": "string"},
                    "git_pr_url": {"type": "string"},
                    "git_repo": {"type": "string"},
                    "triggered_by": {
                        "type": "string",
                        "enum": ["human", "schedule", "api_agent", "ci", "webhook"],
                    },
                },
                "required": ["suite_id"],
            },
        ),
        Tool(
            name="get_run",
            description="Fetch a run's current status, counts, and (when complete) per-case results.",
            inputSchema={
                "type": "object",
                "properties": {"run_id": {"type": "integer"}},
                "required": ["run_id"],
            },
        ),
        Tool(
            name="get_run_results",
            description="Fetch only the per-test-case results for a run (test name, status, error, trace URL).",
            inputSchema={
                "type": "object",
                "properties": {"run_id": {"type": "integer"}},
                "required": ["run_id"],
            },
        ),
        Tool(
            name="get_failure_analysis",
            description="Fetch the structured AI failure analysis for a finalized run, if any.",
            inputSchema={
                "type": "object",
                "properties": {"run_id": {"type": "integer"}},
                "required": ["run_id"],
            },
        ),
        Tool(
            name="get_artifact_url",
            description="Resolve a presigned URL for an artifact (trace.zip, video, screenshot) given its object path.",
            inputSchema={
                "type": "object",
                "properties": {"object_path": {"type": "string"}},
                "required": ["object_path"],
            },
        ),
        Tool(
            name="wait_for_run",
            description="Poll a run until it reaches a terminal status (passed/failed/error) or the timeout elapses. Returns the final run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer"},
                    "timeout_seconds": {"type": "integer", "default": 600},
                    "poll_interval_seconds": {"type": "integer", "default": 5},
                },
                "required": ["run_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    client = _new_client()
    if name == "list_projects":
        data = await client.list_projects()
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "list_suites":
        data = await client.list_suites(arguments["project_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "run_suite":
        data = await client.run_suite(
            suite_id=arguments["suite_id"],
            case_id=arguments.get("case_id"),
            browser=arguments.get("browser"),
            device=arguments.get("device"),
            git_commit=arguments.get("git_commit"),
            git_branch=arguments.get("git_branch"),
            git_pr_url=arguments.get("git_pr_url"),
            git_repo=arguments.get("git_repo"),
            triggered_by=arguments.get("triggered_by"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "get_run":
        run = await client.get_run(arguments["run_id"])
        return [TextContent(type="text", text=_format_run_summary(run))]

    if name == "get_run_results":
        run = await client.get_run(arguments["run_id"])
        results = run.get("results") or []
        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    if name == "get_failure_analysis":
        run = await client.get_run(arguments["run_id"])
        ai = run.get("ai_analysis") or {}
        return [TextContent(type="text", text=json.dumps(ai, indent=2))]

    if name == "get_artifact_url":
        data = await client.get_artifact_url(arguments["object_path"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "wait_for_run":
        run_id = arguments["run_id"]
        timeout = int(arguments.get("timeout_seconds", 600))
        interval = max(1, int(arguments.get("poll_interval_seconds", 5)))
        elapsed = 0
        last: Dict[str, Any] = {}
        while elapsed <= timeout:
            last = await client.get_run(run_id)
            status = (last.get("status") or "").lower()
            if status in TERMINAL_STATUSES:
                return [TextContent(type="text", text=_format_run_summary(last))]
            await asyncio.sleep(interval)
            elapsed += interval
        return [TextContent(type="text", text=_format_run_summary(last))]

    raise ValueError(f"Unknown tool: {name}")


async def _amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entrypoint registered as `traceiq-mcp` console script."""
    # Fail fast if env is missing so the agent sees a clear error at startup.
    if not os.environ.get("TRACEIQ_BASE_URL") or not os.environ.get("TRACEIQ_API_KEY"):
        raise SystemExit(
            "TRACEIQ_BASE_URL and TRACEIQ_API_KEY must be set in the MCP server's environment."
        )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
