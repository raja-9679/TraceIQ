"""Impact analysis, discovery, run history, code-path maintenance.

This is the metadata loop that makes TraceIQ useful to a coding agent across
sessions: cases carry `code_paths`; select_tests_for_diff turns a changed-file
list into run/review decisions; set_code_paths keeps the mapping current.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.impact import (
    AppSurface,
    CodePathsResult,
    CrawlResult,
    ImpactAnalysis,
    RunHistory,
)


@mcp.tool()
async def select_tests_for_diff(project_id: int, changed_files: List[str],
                                include_no_code_paths: bool = False) -> ImpactAnalysis:
    """Given the files changed in a diff/PR, return which test cases are
    affected. Each matched case carries `suggested_action`:
      • run — just re-run it
      • review — the case likely needs EDITING first (last result failed,
        quarantined flaky, or AI-authored and never reviewed) — see `reasons`
      • run_then_review — run it, but its code_paths mapping looks too coarse;
        re-derive with set_code_paths afterwards
    Also returns `unmatched_files` (changed files no test covers — candidates
    for new proposals) and per-case match detail (which pattern hit which
    file), last result, and last_validated_commit."""
    data = await new_client().impact_analysis(
        project_id, changed_files, include_no_code_paths)
    return ImpactAnalysis.model_validate(data)


@mcp.tool()
async def discover_app_surface(project_id: int) -> AppSurface:
    """What's currently tested in a project: suite tree, routes covered
    (distinct goto URLs), code-path coverage, recent runs, case counts.
    Use this before proposing new cases so you don't duplicate coverage."""
    data = await new_client().app_surface(project_id)
    return AppSurface.model_validate(data)


@mcp.tool()
async def crawl_app_surface(project_id: int, base_url: str,
                            max_pages: int = 10) -> CrawlResult:
    """Mode-2 (URL-only) discovery: crawl a LIVE application you have no
    source access to and return its interactable surface — forms, buttons,
    internal links per page. Same-origin only; runs authenticated if the
    project has a stored auth session. max_pages caps the crawl (hard cap 50)."""
    data = await new_client().crawl_app_surface(project_id, base_url, max_pages)
    return CrawlResult.model_validate(data)


@mcp.tool()
async def get_run_history(case_id: int, limit: int = 30) -> RunHistory:
    """Last N results for one test case with pass/fail summary. Entries carry
    matched_by: 'id' (exact result↔case link) or 'name' (legacy fallback that
    can conflate same-named cases). Check this before deciding to modify a
    case — a long green history argues for editing carefully."""
    data = await new_client().run_history(case_id, limit)
    return RunHistory.model_validate(data)


@mcp.tool()
async def set_code_paths(case_id: int, code_paths: List[str]) -> CodePathsResult:
    """Set the `code_paths` array on one TestCase — the file prefixes/globs it
    exercises (e.g. 'frontend/src/Checkout/' or 'backend/app/api/**/*.py').
    This mapping is what select_tests_for_diff matches against — keep it
    current whenever you change what a case covers. List every file the case
    actually touches, frontend AND backend."""
    client = new_client()
    # The only write path for code_paths is the project-scoped bulk endpoint;
    # resolve the case's project first.
    case = await client.get_case(case_id)
    data = await client.bulk_set_code_paths(
        case["project_id"], {case_id: code_paths})
    return CodePathsResult.model_validate(data)


@mcp.tool()
async def bulk_set_code_paths(project_id: int,
                              mapping: Dict[str, List[str]]) -> CodePathsResult:
    """Set `code_paths` on MANY cases in one call: {case_id: [paths]}. Use
    after walking source locally to map every case to the files it exercises.
    Cases outside the project are skipped per-row (no info leak)."""
    data = await new_client().bulk_set_code_paths(
        project_id, {int(k): v for k, v in mapping.items()})
    return CodePathsResult.model_validate(data)
