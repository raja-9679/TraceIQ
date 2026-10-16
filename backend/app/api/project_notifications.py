"""Project notification settings and each person's choice per project.

    GET  /api/projects/{id}/notification-settings      members (webhooks masked)
    PUT  /api/projects/{id}/notification-settings      project admin, audited
    GET  /api/projects/{id}/notification-recipients    people who may be picked
    GET  /api/projects/{id}/my-notifications           the caller's mode here
    PUT  /api/projects/{id}/my-notifications           {"mode": default|all|failures|off}
    GET  /api/me/project-notifications                 the caller's mode, per project

Rules and storage: app/services/project_notifications.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project, ProjectNotificationPref, User, UserWorkspace
from app.services import project_notifications as pn
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


async def _project(project_id: int, session: AsyncSession, user_id: int, min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(user_id, project_id, session, min_role=min_role):
        raise HTTPException(
            status_code=403,
            detail="Admin role required to change notification settings" if min_role == "admin" else "Access denied")
    return project


async def _eligible(project: Project, session: AsyncSession) -> List[Dict[str, Any]]:
    """Everyone with access to the project: candidates are the workspace's
    members (project access always runs through workspace membership), each
    checked with the same rule that gates the project itself."""
    rows = await session.exec(
        select(User).join(UserWorkspace, UserWorkspace.user_id == User.id)
        .where(UserWorkspace.workspace_id == project.workspace_id))
    out: List[Dict[str, Any]] = []
    for user in rows.all():
        if not getattr(user, "is_active", True):
            continue
        if await access_service.has_project_access(user.id, project.id, session):
            out.append({"id": user.id, "email": user.email, "full_name": user.full_name})
    out.sort(key=lambda u: (u["full_name"] or u["email"] or "").lower())
    return out


@router.get("/projects/{project_id}/notification-settings")
async def get_settings(project_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    project = await _project(project_id, session, principal.user.id)
    return pn.describe(project)


@router.get("/projects/{project_id}/notification-recipients")
async def get_recipients(project_id: int, session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)) -> List[Dict[str, Any]]:
    project = await _project(project_id, session, principal.user.id)
    return await _eligible(project, session)


@router.put("/projects/{project_id}/notification-settings")
async def set_settings(project_id: int, request: Request, payload: Dict[str, Any] = Body(...),
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    """Partial update. Admin-only: it decides who outside the run starter
    hears about the project and which external endpoints the server posts to."""
    project = await _project(project_id, session, principal.user.id, min_role="admin")
    try:
        patch = pn.validate_patch(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if "recipient_user_ids" in patch:
        allowed = {u["id"] for u in await _eligible(project, session)}
        outsiders = [i for i in patch["recipient_user_ids"] if i not in allowed]
        if outsiders:
            raise HTTPException(status_code=422,
                                detail=f"not members of this project: {outsiders}")
    for key in ("slack_webhook", "teams_webhook"):
        if patch.get(key):
            from app.core.net_guard import validate_outbound_url
            try:
                await validate_outbound_url(patch[key])
            except Exception as exc:  # noqa: BLE001 - refused destination, report why
                raise HTTPException(status_code=422, detail=f"{key}: {exc}")

    before = pn.describe(project)
    project.notification_settings = pn.apply_patch(pn.stored(project), patch)
    session.add(project)
    after = pn.describe(project)
    # Webhook URLs are secrets: the audit trail records that one was set or
    # cleared, never the URL.
    changed = {k: {"from": before.get(k), "to": after.get(k)}
               for k in after if before.get(k) != after.get(k) and not k.endswith("_hint")}
    if changed:
        await audit_record(session, entity_type="project", entity_id=project_id,
                           action="update_notification_settings", workspace_id=project.workspace_id,
                           principal=principal, request=request, changes=changed)
    await session.commit()
    await session.refresh(project)
    return pn.describe(project)


async def _pref(session: AsyncSession, user_id: int, project_id: int):
    return (await session.exec(select(ProjectNotificationPref).where(
        ProjectNotificationPref.user_id == user_id,
        ProjectNotificationPref.project_id == project_id))).first()


@router.get("/projects/{project_id}/my-notifications")
async def get_mine(project_id: int, session: AsyncSession = Depends(get_session),
                   principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    await _project(project_id, session, principal.user.id)
    row = await _pref(session, principal.user.id, project_id)
    return {"project_id": project_id, "mode": row.mode if row else "default"}


@router.put("/projects/{project_id}/my-notifications")
async def set_mine(project_id: int, payload: Dict[str, Any] = Body(...),
                   session: AsyncSession = Depends(get_session),
                   principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    await _project(project_id, session, principal.user.id)
    mode = payload.get("mode") if isinstance(payload, dict) else None
    if mode not in pn.PREF_MODES + ("default",):
        raise HTTPException(status_code=422, detail="mode must be default, all, failures or off")
    row = await _pref(session, principal.user.id, project_id)
    if mode == "default":
        if row:
            await session.delete(row)
    elif row:
        row.mode, row.updated_at = mode, datetime.utcnow()
        session.add(row)
    else:
        session.add(ProjectNotificationPref(user_id=principal.user.id, project_id=project_id, mode=mode))
    await session.commit()
    return {"project_id": project_id, "mode": mode}


@router.get("/me/project-notifications")
async def list_mine(session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)) -> List[Dict[str, Any]]:
    """Every project the caller can see, with their choice there."""
    from app.api.projects import list_projects
    projects = await list_projects(workspace_id=None, session=session, current_user=principal.user)
    rows = (await session.exec(select(ProjectNotificationPref).where(
        ProjectNotificationPref.user_id == principal.user.id))).all()
    modes = {r.project_id: r.mode for r in rows}
    return [{"project_id": p.id, "project_name": p.name, "workspace_id": p.workspace_id,
             "mode": modes.get(p.id, "default")} for p in projects]
