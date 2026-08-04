"""Run lifecycle: trigger, poll, results, failure analysis, artifacts."""
from __future__ import annotations

import asyncio
from typing import List, Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.common import AcceptedResult, ArtifactUrl
from traceiq_mcp.schemas.runs import (
    CaseResult,
    FailureAnalysis,
    RunDetail,
    RunRefList,
    RunResults,
)

TERMINAL_STATUSES = {"passed", "failed", "error"}


@mcp.tool()
async def run_suite(
    suite_id: int,
    case_id: Optional[int] = None,
    browser: Optional[List[str]] = None,
    device: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    git_commit: Optional[str] = None,
    git_branch: Optional[str] = None,
    git_pr_url: Optional[str] = None,
    git_repo: Optional[str] = None,
    environment_id: Optional[int] = None,
    local_worker_id: Optional[str] = None,
    app_build_id: Optional[int] = None,
) -> RunRefList:
    """Trigger a regression run for a suite (or one case via case_id; or a
    tag-selected subset via tags). ALWAYS tag the run with the git commit /
    branch you are verifying — that is what stamps last_validated_commit on
    passing cases and powers future impact analysis.

    local_worker_id pins the run's jobs to a developer's polling worker
    (`npm run worker:local` in execution-engine/) so tests can hit a dev
    server on localhost before commit. app_build_id pins a mobile run to an
    uploaded APK/AAB/IPA (see list_app_builds). Fan-out: one run per
    case × browser × device in separate/parallel suites."""
    data = await new_client().create_run(
        suite_id=suite_id, case_id=case_id, browser=browser, device=device,
        tags=tags, git_commit=git_commit, git_branch=git_branch,
        git_pr_url=git_pr_url, git_repo=git_repo,
        environment_id=environment_id, local_worker_id=local_worker_id,
        app_build_id=app_build_id)
    runs = data if isinstance(data, list) else [data]
    return RunRefList(runs=runs)


@mcp.tool()
async def get_run(run_id: int) -> RunDetail:
    """Fetch a run's current status, counts, git context, and (when complete)
    per-case results."""
    data = await new_client().get_run(run_id)
    return RunDetail.model_validate(data)


@mcp.tool()
async def wait_for_run(run_id: int, timeout_seconds: int = 600,
                       poll_interval_seconds: int = 5) -> RunDetail:
    """Poll a run until it reaches a terminal status (passed/failed/error) or
    the timeout elapses. Returns the final run either way — check `status`."""
    client = new_client()
    interval = max(1, poll_interval_seconds)
    elapsed = 0
    data = await client.get_run(run_id)
    while (data.get("status") or "").lower() not in TERMINAL_STATUSES:
        if elapsed >= timeout_seconds:
            break
        await asyncio.sleep(interval)
        elapsed += interval
        data = await client.get_run(run_id)
    return RunDetail.model_validate(data)


@mcp.tool()
async def get_run_results(run_id: int) -> RunResults:
    """Fetch only the per-test-case results for a run (name, status, error,
    trace URL, test_case_id)."""
    data = await new_client().get_run(run_id)
    return RunResults(
        run_id=run_id,
        status=data.get("status") or "unknown",
        results=data.get("results") or [],
    )


@mcp.tool()
async def get_failure_analysis(run_id: int) -> FailureAnalysis:
    """Fetch the structured AI failure analysis for a finalized run plus the
    failed results it covers. Empty analysis = not generated yet; trigger one
    with analyze_run."""
    data = await new_client().get_run(run_id)
    failed = [
        r for r in (data.get("results") or [])
        if (r.get("status") or "").lower() in ("failed", "error")
    ]
    return FailureAnalysis(
        run_id=run_id,
        status=data.get("status") or "unknown",
        analysis=data.get("ai_analysis") or {},
        failed_results=[CaseResult.model_validate(r) for r in failed],
    )


@mcp.tool()
async def analyze_run(run_id: int,
                      provider_id: Optional[int] = None) -> AcceptedResult:
    """(Re-)run AI failure analysis for a finished run, optionally with a
    specific saved LLM provider (provider_id). Async: returns 202-style
    acknowledgement — poll get_failure_analysis for the result."""
    data = await new_client().analyze_run(run_id, provider_id)
    return AcceptedResult.model_validate(data)


@mcp.tool()
async def get_artifact_url(object_path: str) -> ArtifactUrl:
    """Resolve a presigned URL for a run artifact (trace.zip, video,
    screenshot) given its object path, e.g. 'runs/123/trace.zip'."""
    data = await new_client().get_artifact_url(object_path)
    return ArtifactUrl.model_validate(data)
