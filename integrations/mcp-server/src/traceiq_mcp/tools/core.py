"""Workspaces, projects, suites, cases — the catalogue."""
from __future__ import annotations

from typing import Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.common import OkResult
from traceiq_mcp.schemas.core import (
    CaseDetail,
    CaseList,
    Project,
    ProjectList,
    SuiteDetail,
    SuiteList,
    SuiteSummary,
    WorkspaceList,
)


@mcp.tool()
async def list_workspaces() -> WorkspaceList:
    """List TraceIQ workspaces the API key can access. Call this first to get
    a workspace_id before creating a project."""
    data = await new_client().list_workspaces()
    return WorkspaceList(items=data)


@mcp.tool()
async def list_projects() -> ProjectList:
    """List TraceIQ projects this agent can see."""
    data = await new_client().list_projects()
    return ProjectList(items=data)


@mcp.tool()
async def create_project(workspace_id: int, name: str,
                         description: Optional[str] = None) -> Project:
    """Create a new TraceIQ project inside a workspace. Returns the project id
    needed for all subsequent suite and case operations."""
    data = await new_client().create_project(workspace_id, name, description)
    return Project.model_validate(data)


@mcp.tool()
async def list_suites(project_id: int) -> SuiteList:
    """List test suites within a TraceIQ project (nested modules included)."""
    data = await new_client().list_suites(project_id)
    return SuiteList(items=data)


@mcp.tool()
async def get_suite(suite_id: int) -> SuiteDetail:
    """Fetch a single TestSuite (name, parent, execution_mode, settings)."""
    data = await new_client().get_suite(suite_id)
    return SuiteDetail.model_validate(data)


@mcp.tool()
async def create_suite(project_id: int, name: str,
                       parent_id: Optional[int] = None,
                       execution_mode: Optional[str] = None,
                       description: Optional[str] = None) -> SuiteSummary:
    """Create a test suite under a project (optionally nested under a parent
    suite). Use only after discover_app_surface confirms a suitable home
    doesn't already exist. execution_mode: continuous | separate | parallel."""
    data = await new_client().create_suite(
        project_id, name, parent_id, execution_mode, description)
    return SuiteSummary.model_validate(data)


@mcp.tool()
async def delete_suite(suite_id: int) -> OkResult:
    """Delete a TestSuite and all of its contents (cases + sub-suites).
    Returns 409 if a TestSchedule references it. Policy: only delete suites
    YOU created this session — ask the human first for pre-existing ones."""
    data = await new_client().delete_suite(suite_id)
    if isinstance(data, dict):
        return OkResult.model_validate(data)
    return OkResult(message=str(data))


@mcp.tool()
async def list_cases(project_id: int, test_suite_id: Optional[int] = None,
                     tag: Optional[str] = None,
                     limit: int = 100, offset: int = 0) -> CaseList:
    """List test cases in a project (slim rows: name, tags, priority,
    code_paths, last_validated_commit). Filter by suite or tag. Use BEFORE
    proposing a new case to avoid duplicates; use get_case for full steps."""
    data = await new_client().list_cases(project_id, test_suite_id, tag,
                                         limit, offset)
    return CaseList.model_validate(data)


@mcp.tool()
async def get_case(case_id: int) -> CaseDetail:
    """Fetch a single TestCase with full steps, code_paths, tags, and
    validation stamps. Use before proposing an UPDATE so you can diff."""
    data = await new_client().get_case(case_id)
    return CaseDetail.model_validate(data)
