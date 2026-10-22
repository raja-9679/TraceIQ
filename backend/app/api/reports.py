"""Reporting API (WP12a).

    POST/GET     /api/projects/{id}/report-schedules      weekly digest config (editor / viewer)
    PATCH/DELETE /api/report-schedules/{id}               editor
    POST         /api/report-schedules/{id}/send-now      editor
    GET          /api/projects/{id}/digest-preview        the digest as it would be sent (viewer)
    GET          /api/projects/{id}/executive-summary     one-page summary (viewer)
    GET          /api/workspaces/{id}/executive-summary   roll-up over the caller's projects
    GET          /api/workspaces/{id}/portfolio           one row per project the caller can access

Digest recipients follow the per-project notification rules (decision R1):
picked project members, re-checked for access when the digest is sent, plus
validated extra addresses; chat goes only to the project's own webhooks.
Numbers come from app/services/reporting.py.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    Project, ReportSchedule, ReportScheduleCreate, ReportScheduleRead, ReportScheduleUpdate,
    UserWorkspace, Workspace,
)
from app.services import reporting
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

CHANNELS = ("email", "slack", "teams")
MAX_EXTRA = 20
MAX_PEOPLE = 50


async def _project(session: AsyncSession, principal: AuthPrincipal, project_id: int,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403,
                            detail="Editor role required" if min_role == "editor" else "Access denied")
    return project


async def accessible_projects(session: AsyncSession, principal: AuthPrincipal,
                              workspace_id: int, min_role: str = "viewer") -> List[Project]:
    """Projects in the workspace this caller may see - checked one by one
    with the same rule that gates each project page."""
    projects = (await session.exec(select(Project).where(
        Project.workspace_id == workspace_id).order_by(Project.name))).all()
    out = []
    for p in projects:
        if principal.api_key is not None and not api_key_allows_project(principal.api_key, p.id):
            continue
        if await access_service.has_project_access(principal.user.id, p.id, session, min_role=min_role):
            out.append(p)
    return out


async def _workspace(session: AsyncSession, principal: AuthPrincipal, workspace_id: int) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # Everything behind this gate is narrowed to accessible_projects(), which
    # applies a project-scoped key's limit itself.
    if not await access_service.workspace_membership(principal.user.id, workspace_id, session,
                                                     per_project=True):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return ws


# ---------------------------------------------------------------------------
# Digest schedules
# ---------------------------------------------------------------------------


def _clean_channels(channels: Optional[List[str]]) -> List[str]:
    out = []
    for c in channels or ["email"]:
        if c not in CHANNELS:
            raise HTTPException(status_code=422, detail=f"unknown channel: {c}")
        if c not in out:
            out.append(c)
    return out


def _clean_extra(addresses: Optional[List[str]]) -> List[str]:
    from app.settings_models import normalize_notification_email
    out: List[str] = []
    for a in addresses or []:
        if not isinstance(a, str):
            raise HTTPException(status_code=422, detail="recipients must be email addresses")
        try:
            v = normalize_notification_email(a)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"not a single plain email address: {a[:80]}")
        if v and v.lower() not in {x.lower() for x in out}:
            out.append(v)
    if len(out) > MAX_EXTRA:
        raise HTTPException(status_code=422, detail=f"recipients: at most {MAX_EXTRA} addresses")
    return out


async def _clean_people(session: AsyncSession, project: Project, ids: Optional[List[int]]) -> List[int]:
    from app.api.project_notifications import _eligible
    ids = list(dict.fromkeys(ids or []))
    if len(ids) > MAX_PEOPLE:
        raise HTTPException(status_code=422, detail=f"recipient_user_ids: at most {MAX_PEOPLE}")
    if ids:
        allowed = {u["id"] for u in await _eligible(project, session)}
        outsiders = [i for i in ids if i not in allowed]
        if outsiders:
            raise HTTPException(status_code=422, detail=f"not members of this project: {outsiders}")
    return ids


def _clean_window(days: int) -> int:
    if not 1 <= int(days) <= reporting.MAX_WINDOW_DAYS:
        raise HTTPException(status_code=422,
                            detail=f"window_days must be between 1 and {reporting.MAX_WINDOW_DAYS}")
    return int(days)


@router.post("/projects/{project_id}/report-schedules", response_model=ReportScheduleRead)
async def create_report_schedule(project_id: int, body: ReportScheduleCreate, request: Request,
                                 session: AsyncSession = Depends(get_session),
                                 principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(session, principal, project_id, min_role="editor")
    if not croniter.is_valid(body.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")
    sched = ReportSchedule(
        project_id=project_id, name=(body.name or "Weekly digest")[:120],
        cron_expression=body.cron_expression, window_days=_clean_window(body.window_days),
        channels=_clean_channels(body.channels), recipients=_clean_extra(body.recipients),
        recipient_user_ids=await _clean_people(session, project, body.recipient_user_ids),
        is_active=body.is_active, created_by_id=principal.user.id,
        next_run_at=croniter(body.cron_expression, datetime.utcnow()).get_next(datetime))
    session.add(sched)
    await session.flush()
    await audit_record(session, entity_type="report_schedule", entity_id=sched.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"project_id": project_id, "channels": sched.channels,
                                "people": len(sched.recipient_user_ids or []),
                                "extra_addresses": len(sched.recipients or [])})
    await session.commit()
    await session.refresh(sched)
    return sched


@router.get("/projects/{project_id}/report-schedules", response_model=List[ReportScheduleRead])
async def list_report_schedules(project_id: int, session: AsyncSession = Depends(get_session),
                                principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(session, principal, project_id)
    return (await session.exec(
        select(ReportSchedule).where(ReportSchedule.project_id == project_id)
        .order_by(ReportSchedule.id))).all()


async def _schedule(session, principal, schedule_id: int, min_role: str = "editor"):
    sched = await session.get(ReportSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Report schedule not found")
    project = await _project(session, principal, sched.project_id, min_role=min_role)
    return sched, project


@router.patch("/report-schedules/{schedule_id}", response_model=ReportScheduleRead)
async def update_report_schedule(schedule_id: int, body: ReportScheduleUpdate, request: Request,
                                 session: AsyncSession = Depends(get_session),
                                 principal: AuthPrincipal = Depends(get_current_principal)):
    sched, project = await _schedule(session, principal, schedule_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("cron_expression") is not None:
        if not croniter.is_valid(data["cron_expression"]):
            raise HTTPException(status_code=400, detail="Invalid cron expression")
        sched.next_run_at = croniter(data["cron_expression"], datetime.utcnow()).get_next(datetime)
    if "channels" in data:
        data["channels"] = _clean_channels(data["channels"])
    if "recipients" in data:
        data["recipients"] = _clean_extra(data["recipients"])
    if "recipient_user_ids" in data:
        data["recipient_user_ids"] = await _clean_people(session, project, data["recipient_user_ids"])
    if data.get("window_days") is not None:
        data["window_days"] = _clean_window(data["window_days"])
    if data.get("name") is not None:
        data["name"] = data["name"][:120]
    for k, v in data.items():
        if v is not None:
            setattr(sched, k, v)
    session.add(sched)
    await audit_record(session, entity_type="report_schedule", entity_id=sched.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"fields": sorted(data)})
    await session.commit()
    await session.refresh(sched)
    return sched


@router.delete("/report-schedules/{schedule_id}")
async def delete_report_schedule(schedule_id: int, request: Request,
                                 session: AsyncSession = Depends(get_session),
                                 principal: AuthPrincipal = Depends(get_current_principal)):
    sched, project = await _schedule(session, principal, schedule_id)
    await audit_record(session, entity_type="report_schedule", entity_id=sched.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": sched.name})
    await session.delete(sched)
    await session.commit()
    return {"status": "deleted"}


@router.post("/report-schedules/{schedule_id}/send-now")
async def send_report_now_endpoint(schedule_id: int, session: AsyncSession = Depends(get_session),
                                   principal: AuthPrincipal = Depends(get_current_principal)):
    # Editor, not viewer: sending email and posting to the project's channels
    # on demand is an action on the project, not a read of it.
    await _schedule(session, principal, schedule_id)
    from app.tasks.report_tasks import send_report_now
    send_report_now.delay(schedule_id)
    return {"status": "queued"}


@router.get("/projects/{project_id}/digest-preview")
async def digest_preview(project_id: int, days: int = Query(7, ge=1, le=90),
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    project = await _project(session, principal, project_id)
    snap = await reporting.project_snapshot(session, project, days)
    return {"summary": snap, "text": reporting.render_digest_text(snap),
            "html": reporting.render_digest_html(snap)}


# ---------------------------------------------------------------------------
# Executive summary + portfolio
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/executive-summary")
async def project_executive_summary(project_id: int, days: int = Query(30, ge=1, le=90),
                                    session: AsyncSession = Depends(get_session),
                                    principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(session, principal, project_id)
    return await reporting.project_snapshot(session, project, days)


@router.get("/workspaces/{workspace_id}/executive-summary")
async def workspace_executive_summary(workspace_id: int, days: int = Query(30, ge=1, le=90),
                                      session: AsyncSession = Depends(get_session),
                                      principal: AuthPrincipal = Depends(get_current_principal)):
    ws = await _workspace(session, principal, workspace_id)
    projects = await accessible_projects(session, principal, workspace_id)
    return await reporting.workspace_snapshot(session, ws, projects, days)


@router.get("/workspaces/{workspace_id}/portfolio")
async def workspace_portfolio(workspace_id: int, session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    ws = await _workspace(session, principal, workspace_id)
    projects = await accessible_projects(session, principal, workspace_id)
    return {"workspace_id": ws.id, "workspace_name": ws.name,
            "generated_at": datetime.utcnow().isoformat(),
            "projects": await reporting.portfolio_rows(session, projects)}
