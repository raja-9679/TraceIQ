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
    from traceiq_mcp._context import get_request_api_key
    req_key = get_request_api_key()
    if req_key:
        # HTTP transport: use the per-request key from the client's X-API-Key header.
        # TRACEIQ_BASE_URL still comes from env (set on the Docker service).
        return TraceIQClient(api_key=req_key)
    return TraceIQClient()  # stdio: reads TRACEIQ_BASE_URL + TRACEIQ_API_KEY from env


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
            name="list_workspaces",
            description="List TraceIQ workspaces the agent's API key can access. Call this first to get a workspace_id before creating a project.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="create_project",
            description="Create a new TraceIQ project inside a workspace. Returns the project id needed for all subsequent suite and case operations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "integer", "description": "ID of the workspace to create the project in. Get this from list_workspaces."},
                    "name": {"type": "string", "description": "Human-readable project name (e.g. 'My App')."},
                    "description": {"type": "string", "description": "Optional short description of the project."},
                },
                "required": ["workspace_id", "name"],
            },
        ),
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
                    "environment_id": {
                        "type": "integer",
                        "description": "Optional ProjectEnvironment id to run against; defaults to the project's default environment.",
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

        # ---------- Phase D — agent ownership ----------
        Tool(
            name="discover_app_surface",
            description=(
                "Return what's currently tested in a TraceIQ project: suite tree, "
                "routes covered (distinct goto URLs), code-path coverage, recent runs, "
                "and case counts (total, AI-authored, human-reviewed). Use this before "
                "proposing new test cases so you don't duplicate existing ones."
            ),
            inputSchema={
                "type": "object",
                "properties": {"project_id": {"type": "integer"}},
                "required": ["project_id"],
            },
        ),
        Tool(
            name="crawl_app_surface",
            description=(
                "Mode-2 (URL-only) discovery: crawl a LIVE application you have no "
                "source access to and return its interactable surface — forms (with "
                "inputs), buttons, and internal links per page. Use this to propose "
                "smoke tests for a deployed app when you cannot read its code. Runs "
                "authenticated if the project has a stored auth session. Budget the "
                "crawl with max_pages (default 10)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "base_url": {"type": "string", "description": "Entry URL to crawl from (same-origin only)."},
                    "max_pages": {"type": "integer", "description": "Max pages to visit (default 10, hard cap 50)."},
                },
                "required": ["project_id", "base_url"],
            },
        ),
        Tool(
            name="select_tests_for_diff",
            description=(
                "Given a list of files changed in a PR, return the subset of test "
                "cases that should run for that diff (based on each case's "
                "`code_paths` field). The response also lists files that are NOT "
                "covered by any test — these are candidates for new cases."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "changed_files": {"type": "array", "items": {"type": "string"}},
                    "include_no_code_paths": {"type": "boolean", "default": False},
                },
                "required": ["project_id", "changed_files"],
            },
        ),
        Tool(
            name="get_run_history",
            description="Return the last N runs that exercised a specific test case, with summary counts (passes, failures, last failure timestamp).",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_id": {"type": "integer"},
                    "limit": {"type": "integer", "default": 30},
                },
                "required": ["case_id"],
            },
        ),
        Tool(
            name="create_suite",
            description="Create a new test suite under a project (optionally nested under a parent suite). Agents should use this only after `discover_app_surface` confirms a suitable home doesn't already exist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "parent_id": {"type": "integer"},
                    "execution_mode": {"type": "string", "enum": ["continuous", "separate", "parallel"]},
                    "description": {"type": "string"},
                },
                "required": ["project_id", "name"],
            },
        ),
        Tool(
            name="propose_create_case",
            description=(
                "Propose creating a new TestCase. Goes into the human-review queue "
                "(CaseProposal) — nothing is created until a reviewer accepts. Use "
                "this for any case authored from a description; selectors generated "
                "without observing the real app are notoriously brittle."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "test_suite_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "steps": {"type": "array"},
                    "code_paths": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "ai_confidence": {"type": "number"},
                },
                "required": ["project_id", "test_suite_id", "name", "steps"],
            },
        ),
        Tool(
            name="propose_update_case",
            description="Propose updating an existing TestCase (name, steps, code_paths). Queued for human review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "target_case_id": {"type": "integer"},
                    "patch": {"type": "object"},
                    "rationale": {"type": "string"},
                    "ai_confidence": {"type": "number"},
                },
                "required": ["project_id", "target_case_id", "patch"],
            },
        ),
        Tool(
            name="propose_delete_case",
            description="Propose deleting an obsolete TestCase. Queued for human review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "target_case_id": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["project_id", "target_case_id", "rationale"],
            },
        ),
        Tool(
            name="set_code_paths",
            description=(
                "Set the `code_paths` array on a TestCase — the file prefixes / globs "
                "that this case exercises. Used by `select_tests_for_diff`. Patterns "
                "may be bare prefixes (e.g. 'frontend/src/Checkout/') or globs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "case_id": {"type": "integer"},
                    "code_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["case_id", "code_paths"],
            },
        ),
        Tool(
            name="generate_case_proposal",
            description=(
                "LLM-generate a TestCase from a natural-language description and "
                "enqueue it as a CaseProposal for human review. Use when no existing "
                "case covers the change and you need a starting draft. The reviewer "
                "edits and accepts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "test_suite_id": {"type": "integer"},
                    "target_url": {"type": "string"},
                    "case_name": {"type": "string"},
                    "code_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["description", "test_suite_id"],
            },
        ),

        # ---------- Phase E — reads, structural writes, bulk, reference ----------
        Tool(
            name="get_authoring_guide",
            description=(
                "Return the AGENT_GUIDE.md content — TraceIQ's authoritative "
                "reference for how to author test suites and cases. Read this at "
                "the start of every session before proposing anything. Covers "
                "step-type shapes, suite organization conventions, code_paths "
                "globs, persona/auth setup, and the most common authoring "
                "pitfalls."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="describe_step_types",
            description=(
                "Catalogue of every step type the runner supports, with the "
                "expected `params` shape, an example, and notes on common "
                "gotchas (e.g. `fill` doesn't work on <select>; `wait-for-selector` "
                "blocks on in-flight navigations). Use this whenever you're "
                "constructing a `steps` array."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_cases",
            description=(
                "List test cases in a project, optionally filtered by suite. "
                "Use BEFORE proposing a new case to check whether one already "
                "exists — avoids duplicates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "test_suite_id": {"type": "integer", "description": "Optional; limits to a specific suite."},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="get_case",
            description="Fetch a single TestCase with full steps + code_paths. Use before proposing an UPDATE so the agent can construct a diff.",
            inputSchema={
                "type": "object",
                "properties": {"case_id": {"type": "integer"}},
                "required": ["case_id"],
            },
        ),
        Tool(
            name="get_suite",
            description="Fetch a single TestSuite (name, parent_id, execution_mode, settings).",
            inputSchema={
                "type": "object",
                "properties": {"suite_id": {"type": "integer"}},
                "required": ["suite_id"],
            },
        ),
        Tool(
            name="list_case_proposals",
            description=(
                "List CaseProposal rows. Use this to see your own pending work "
                "queue and avoid re-submitting the same proposal."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["pending", "accepted", "rejected"], "default": "pending"},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        ),
        Tool(
            name="delete_suite",
            description=(
                "Delete a TestSuite and all of its contents (cases + sub-suites). "
                "Cascades correctly through Phase B/C/D dependents. Returns 409 if "
                "a TestSchedule references this suite or any sub-suite. "
                "Policy: only delete suites YOU created this session — for "
                "pre-existing suites, ask the human user first."
            ),
            inputSchema={
                "type": "object",
                "properties": {"suite_id": {"type": "integer"}},
                "required": ["suite_id"],
            },
        ),
        Tool(
            name="bulk_propose_cases",
            description=(
                "Submit MANY CaseProposals in a single round-trip. Best-effort: a "
                "single bad item doesn't reject the rest. Each item is returned "
                "with its index + status. Use when generating coverage for a "
                "whole feature or all OpenAPI operations at once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "proposals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "project_id": {"type": "integer"},
                                "test_suite_id": {"type": "integer"},
                                "target_case_id": {"type": "integer"},
                                "action": {"type": "string", "enum": ["create", "update", "delete", "move"]},
                                "payload": {"type": "object"},
                                "rationale": {"type": "string"},
                                "ai_confidence": {"type": "number"},
                            },
                            "required": ["project_id", "action"],
                        },
                    },
                },
                "required": ["project_id", "proposals"],
            },
        ),
        Tool(
            name="bulk_set_code_paths",
            description=(
                "Set `code_paths` on MANY existing cases in one call. Use after "
                "walking source code locally to map every case to the files it "
                "exercises. Cases outside the caller's project are silently "
                "skipped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "mapping": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                        "description": "Object mapping case_id → array of code path strings",
                    },
                },
                "required": ["project_id", "mapping"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    client = _new_client()
    if name == "list_workspaces":
        data = await client.list_workspaces()
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "create_project":
        data = await client.create_project(
            workspace_id=arguments["workspace_id"],
            name=arguments["name"],
            description=arguments.get("description"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

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
            environment_id=arguments.get("environment_id"),
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

    # ---------- Phase D ----------
    if name == "crawl_app_surface":
        data = await client.crawl_app_surface(
            arguments["project_id"],
            arguments["base_url"],
            int(arguments.get("max_pages", 10)),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "discover_app_surface":
        data = await client.discover_app_surface(arguments["project_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "select_tests_for_diff":
        data = await client.select_tests_for_diff(
            project_id=arguments["project_id"],
            changed_files=arguments["changed_files"],
            include_no_code_paths=arguments.get("include_no_code_paths", False),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "get_run_history":
        data = await client.get_run_history(
            case_id=arguments["case_id"],
            limit=int(arguments.get("limit", 30)),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "create_suite":
        data = await client.create_suite(
            project_id=arguments["project_id"],
            name=arguments["name"],
            parent_id=arguments.get("parent_id"),
            execution_mode=arguments.get("execution_mode"),
            description=arguments.get("description"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "propose_create_case":
        data = await client.propose_case(
            project_id=arguments["project_id"],
            action="create",
            test_suite_id=arguments["test_suite_id"],
            payload={
                "name": arguments["name"],
                "steps": arguments["steps"],
                "code_paths": arguments.get("code_paths") or [],
            },
            rationale=arguments.get("rationale"),
            ai_confidence=float(arguments.get("ai_confidence", 0.5)),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "propose_update_case":
        data = await client.propose_case(
            project_id=arguments["project_id"],
            action="update",
            target_case_id=arguments["target_case_id"],
            payload=arguments["patch"],
            rationale=arguments.get("rationale"),
            ai_confidence=float(arguments.get("ai_confidence", 0.5)),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "propose_delete_case":
        data = await client.propose_case(
            project_id=arguments["project_id"],
            action="delete",
            target_case_id=arguments["target_case_id"],
            payload={"reason": arguments["rationale"]},
            rationale=arguments["rationale"],
            ai_confidence=1.0,
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "set_code_paths":
        data = await client.update_case(
            case_id=arguments["case_id"],
            code_paths=arguments["code_paths"],
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "generate_case_proposal":
        data = await client.generate_case_proposal(
            description=arguments["description"],
            test_suite_id=arguments["test_suite_id"],
            target_url=arguments.get("target_url"),
            case_name=arguments.get("case_name"),
            code_paths=arguments.get("code_paths"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    # ---------- Phase E ----------
    if name == "get_authoring_guide":
        data = await client.get_authoring_guide()
        return [TextContent(type="text", text=data.get("guide", "(guide unavailable)"))]

    if name == "describe_step_types":
        data = await client.describe_step_types()
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "list_cases":
        data = await client.list_cases(
            project_id=arguments["project_id"],
            test_suite_id=arguments.get("test_suite_id"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "get_case":
        data = await client.get_case(arguments["case_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "get_suite":
        data = await client.get_suite(arguments["suite_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "list_case_proposals":
        data = await client.list_case_proposals(
            project_id=arguments.get("project_id"),
            status=arguments.get("status", "pending"),
            limit=int(arguments.get("limit", 100)),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "delete_suite":
        data = await client.delete_suite(arguments["suite_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "bulk_propose_cases":
        data = await client.bulk_propose_cases(
            project_id=arguments["project_id"],
            proposals=arguments["proposals"],
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    if name == "bulk_set_code_paths":
        data = await client.bulk_set_code_paths(
            project_id=arguments["project_id"],
            mapping=arguments["mapping"],
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

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
