"""Trace correlation with the application under test (WP16a).

    GET /api/projects/{id}/trace-settings          viewer
    PUT /api/projects/{id}/trace-settings          project admin, audited
    GET /api/runs/{run}/results/{result}/traces    viewer of the run's project

Rules live in app/services/trace_correlation.py (what the worker injects) and
app/services/trace_backends.py (reading spans back). Project-scoped API keys
are honoured on all three.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project, TestCaseResult, TestRun
from app.services import trace_backends
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.trace_correlation import TraceSettings, stored_settings

router = APIRouter()


async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="This API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} role required")
    return project


def _describe(project: Project) -> Dict[str, Any]:
    cfg = trace_backends.configured()
    return {**stored_settings(project).model_dump(),
            "backend_configured": cfg is not None,
            "backend": cfg.kind if cfg else None}


class TraceSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    cross_origin: Optional[bool] = None


@router.get("/projects/{project_id}/trace-settings")
async def get_trace_settings(project_id: int, session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)):
    return _describe(await _project(project_id, principal, session))


@router.put("/projects/{project_id}/trace-settings")
async def put_trace_settings(project_id: int, body: TraceSettingsIn, request: Request,
                             session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    before = stored_settings(project)
    after = TraceSettings(**{**before.model_dump(),
                             **body.model_dump(exclude_none=True)})
    project.trace_settings = after.model_dump()      # reassign: JSON column
    session.add(project)
    await audit_record(session, entity_type="project", entity_id=project_id,
                       action="update_trace_settings", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes={"from": before.model_dump(), "to": after.model_dump()})
    await session.commit()
    await session.refresh(project)
    return _describe(project)


@router.get("/runs/{run_id}/results/{result_id}/traces")
async def result_traces(run_id: int, result_id: int,
                        principal: AuthPrincipal = Depends(get_current_principal),
                        session: AsyncSession = Depends(get_session)):
    """Per step with a trace id: the spans the application recorded under the
    step's span, or `no_telemetry` with the reason. Never inferred."""
    run = await session.get(TestRun, run_id)
    result = await session.get(TestCaseResult, result_id)
    if not run or not result or result.test_run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    if principal.api_key and not api_key_allows_project(principal.api_key, run.project_id):
        raise HTTPException(status_code=403, detail="This API key is not scoped to this project")
    if not run.project_id or not await access_service.has_project_access(
            principal.user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    cached = trace_backends.cache_get(result.id)
    if cached is not None:
        return cached
    project = await session.get(Project, run.project_id)
    from app.services.data_policy import resolve_for_project
    from app.services.redaction import RedactionPolicy
    policy = RedactionPolicy.from_data_policy(resolve_for_project(project) if project else None)
    evidence = (result.result_payload or {}).get("step_evidence")
    cfg = trace_backends.configured()
    # The fetch is synchronous (shared with the Celery analysis task); keep it
    # off the event loop.
    payload = await asyncio.to_thread(
        trace_backends.build_result_traces, evidence,
        is_local_run=bool(run.local_worker_id), cfg=cfg, policy=policy)
    trace_backends.cache_put(result.id, payload)
    return payload
