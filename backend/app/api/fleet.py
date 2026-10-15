"""Fleet & infrastructure endpoints (WP8).

    GET  /api/admin/workers              instance admin: heartbeats + stream
                                         backlog + version skew
    GET  /api/internal/worker-config     workers (X-Worker-Secret, optional
                                         mTLS): effective runtime config
    POST /api/jobs/heartbeat             local workers (API key): proof of life

The fleet view is instance-admin only: it names every worker host, what each
is running, and every region — infrastructure intelligence, not tenant data,
but nothing a workspace member needs.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.config import settings
from app.core.database import get_session
from app.core.worker_auth import require_worker
from app.models import User
from app.services import worker_fleet
from app.services.worker_config import effective_worker_config

logger = logging.getLogger(__name__)

router = APIRouter()


async def _redis():
    import redis.asyncio as redis
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


async def _close(client) -> None:
    try:
        await client.aclose() if hasattr(client, "aclose") else await client.close()
    except Exception:
        pass


@router.get("/admin/workers")
async def list_workers(_: User = Depends(get_current_instance_admin)) -> Dict[str, Any]:
    client = await _redis()
    try:
        return await worker_fleet.fleet_snapshot(client)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")
    finally:
        await _close(client)


@router.get("/internal/worker-config", dependencies=[Depends(require_worker)])
async def worker_config() -> Dict[str, Any]:
    """Never logged: the response may carry the worker LLM API key."""
    return effective_worker_config()


class LocalHeartbeat(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    hostname: Optional[str] = Field(default=None, max_length=255)
    version: Optional[str] = Field(default=None, max_length=64)
    git_sha: Optional[str] = Field(default=None, max_length=64)
    step_types: List[str] = Field(default_factory=list, max_length=500)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    current_job: Optional[Dict[str, Any]] = None
    jobs_processed: int = 0
    jobs_failed: int = 0
    started_at: Optional[str] = None


@router.post("/jobs/heartbeat", status_code=204)
async def local_worker_heartbeat(
    body: LocalHeartbeat,
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Local (developer) workers cannot reach Redis, so they report here.
    Namespaced by the key's workspace exactly like their job queue, so one
    tenant cannot overwrite another's entry by guessing a worker id."""
    if not principal.api_key:
        raise HTTPException(status_code=403,
                            detail="Local workers must authenticate with a workspace API key")
    workspace_id = principal.api_key.workspace_id
    current = body.current_job or None
    if current:
        current = {k: current.get(k) for k in ("job_id", "run_id", "started_at")}
    payload = {
        "worker_id": f"local:{workspace_id}:{body.worker_id}",
        "kind": "local",
        "workspace_id": workspace_id,
        "hostname": body.hostname,
        "version": body.version,
        "git_sha": body.git_sha,
        "step_types": [str(t)[:64] for t in body.step_types],
        "capabilities": body.capabilities,
        "current_job": current,
        "jobs_processed": max(0, body.jobs_processed),
        "jobs_failed": max(0, body.jobs_failed),
        "started_at": body.started_at,
        "region": None,
        "streams": [],
    }
    client = await _redis()
    try:
        await worker_fleet.record_heartbeat(client, payload)
    finally:
        await _close(client)


@router.get("/runs/{run_id}/results/{result_id}/server-metrics")
async def load_result_server_metrics(
    run_id: int,
    result_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Prometheus series over a load-test result's window (see
    app/services/server_metrics.py). Viewer access to the run's project."""
    from app.api.jobs import api_key_allows_project
    from app.models import TestCase, TestCaseResult, TestRun
    from app.services import server_metrics
    from app.services.access_service import access_service

    run = await session.get(TestRun, run_id)
    result = await session.get(TestCaseResult, result_id)
    if not run or not result or result.test_run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    if principal.api_key and not api_key_allows_project(principal.api_key, run.project_id):
        raise HTTPException(status_code=403, detail="This API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    if result.result_kind != "load":
        raise HTTPException(status_code=400,
                            detail="Server metrics are only available for load-test results")
    case_id = result.test_case_id or run.test_case_id
    case = await session.get(TestCase, case_id) if case_id else None
    if case is not None and case.project_id != run.project_id:
        case = None
    queries = server_metrics.queries_from_steps(case.steps if case else [])
    start, end = server_metrics.window_for(run.created_at, run.finalized_at, result.duration_ms)
    out = await server_metrics.evaluate(queries, start, end)
    out["queries_defined"] = len(queries)
    return out
