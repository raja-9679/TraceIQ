"""PoC report (WP13b, decision C4).

    GET /api/workspaces/{id}/poc-report?start=&end=&project_ids=1,2
        &minutes_per_execution=5&hourly_rate=&currency=

Covers only the projects the caller can access (viewer), optionally narrowed
to a selection; ids the caller cannot see are ignored, and `project_ids` in
the response says what was covered. Sharing goes through the workspace
share-link endpoint with kind=poc_report (app/api/share_links.py), whose
public payload is app/services/poc_report.public_payload.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.reports import _workspace, accessible_projects
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.services import poc_report

router = APIRouter()


def _ids(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        return [int(x) for x in raw.split(",") if x.strip()][:500]
    except ValueError:
        raise HTTPException(status_code=422, detail="project_ids must be comma-separated ids")


@router.get("/workspaces/{workspace_id}/poc-report")
async def workspace_poc_report(workspace_id: int,
                               start: Optional[str] = Query(None), end: Optional[str] = Query(None),
                               project_ids: Optional[str] = Query(None),
                               minutes_per_execution: Optional[float] = Query(None),
                               hourly_rate: Optional[float] = Query(None),
                               currency: Optional[str] = Query(None, max_length=8),
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    ws = await _workspace(session, principal, workspace_id)
    try:
        s, e = poc_report.parse_range(start, end)
        assumptions = poc_report.parse_assumptions(minutes_per_execution, hourly_rate, currency)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    visible = await accessible_projects(session, principal, workspace_id)
    picked = set(_ids(project_ids))
    projects = [p for p in visible if p.id in picked] if picked else visible
    report = await poc_report.build(session, ws, projects, s, e, assumptions)
    report["available_projects"] = [{"id": p.id, "name": p.name} for p in visible]
    return report
