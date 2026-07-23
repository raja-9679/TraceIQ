"""Local-worker job bridge (SCOPE_NOTES 'Local development bridge').

A developer (or their coding agent) runs a thin worker next to their dev
server (`npm run worker:local` in execution-engine). It authenticates with a
workspace API key and:

1. polls `GET /api/jobs/poll?worker_id=…` for jobs of runs created with
   `local_worker_id` — queued on a per-workspace Redis list at dispatch;
2. runs them with Playwright against localhost;
3. posts each result to `POST /api/jobs/result`, which feeds the normal
   `jobs:results` stream so aggregation/finalize/notifications are identical
   to server-side execution.

The TraceIQ server never needs to reach the developer's machine — only the
public REST API is used, so nothing about the deployment changes.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project, TestRun

router = APIRouter()

RESULTS_STREAM = "jobs:results"


def _require_api_key_workspace(principal: AuthPrincipal) -> int:
    """Local workers are service accounts: API-key auth only. The key's
    workspace namespaces the job queue, so a worker can never poll another
    tenant's jobs even with a guessed worker id."""
    if not principal.api_key:
        raise HTTPException(
            status_code=403,
            detail="Local workers must authenticate with a workspace API key (X-API-Key)")
    return principal.api_key.workspace_id


@router.get("/jobs/poll")
async def poll_local_job(
    worker_id: str = Query(..., min_length=1, max_length=64),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Pop the next pending job for this local worker (204 when idle)."""
    workspace_id = _require_api_key_workspace(principal)

    from app.core.redis import RedisClient
    redis = RedisClient.get_instance()
    raw = await redis.lpop(f"jobs:local:{workspace_id}:{worker_id}")
    if not raw:
        return Response(status_code=204)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=500, detail="Corrupt job payload in queue")


@router.post("/jobs/result", status_code=202)
async def submit_local_job_result(
    result: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Accept one job result from a local worker (same shape the server
    workers push) and feed it into the normal results stream."""
    workspace_id = _require_api_key_workspace(principal)

    run_id: Optional[int] = result.get("run_id")
    job_id: Optional[str] = result.get("job_id")
    if not run_id or not job_id:
        raise HTTPException(status_code=422, detail="result must include run_id and job_id")

    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    project = await session.get(Project, run.project_id) if run.project_id else None
    if not project or project.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="Run does not belong to this API key's workspace")
    if not run.local_worker_id:
        raise HTTPException(status_code=403, detail="Run is not a local-worker run")

    from app.core.redis import RedisClient
    redis = RedisClient.get_instance()

    # Server-side workers increment the run's progress hash themselves
    # (job-queue.ts); local workers can't touch Redis, so do it here. The
    # aggregator's update_run_from_progress finalizes the run when
    # completed >= total.
    status = str(result.get("status", "error"))
    sub_results = result.get("test_results") or [result]
    passed = sum(1 for r in sub_results if r.get("status") == "passed")
    failed = len(sub_results) - passed
    progress_key = f"runs:{run_id}:progress"
    pipe = redis.pipeline()
    pipe.hincrby(progress_key, "completed", len(sub_results))
    pipe.hincrby(progress_key, "passed", passed)
    pipe.hincrby(progress_key, "failed", failed)
    pipe.xadd(RESULTS_STREAM, {
        "job_id": str(job_id),
        "run_id": str(run_id),
        "result": json.dumps(result),
    })
    await pipe.execute()
    return {"status": "queued", "reported": status}
