"""Accessibility register (WP15d, H6) — trend, per-run violations, waivers.

    GET    /api/projects/{id}/accessibility?days=30          trend + open list (viewer)
    GET    /api/projects/{id}/accessibility/runs/{run_id}    one run, new/waived flags (viewer)
    GET    /api/projects/{id}/accessibility/waivers          (viewer)
    POST   /api/projects/{id}/accessibility/waivers          {rule_id, selector_pattern?, reason, days?, owner_user_id?} (editor)
    PUT    /api/accessibility-waivers/{id}                   {reason?, owner_user_id?, days?} — days RENEWS from now (editor)
    DELETE /api/accessibility-waivers/{id}                   (editor)

Rules in app/services/accessibility.py. Every waiver write is audited
(entity_type `accessibility_waiver`).
"""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.flake_records import _can_own, _check_key_scope, _person, _users_by_id
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import AccessibilityWaiver, Project, TestRun, User
from app.services import accessibility as a11y
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
            principal.user.id, project_id, session, min_role=min_role):
        raise HTTPException(status_code=403,
                            detail="Editor access required" if min_role == "editor" else "Access denied")
    _check_key_scope(principal, project_id)
    return project


def _serialize(w: AccessibilityWaiver, users: Dict[int, User]) -> dict:
    return {
        "id": w.id, "project_id": w.project_id, "rule_id": w.rule_id,
        "selector_pattern": w.selector_pattern, "reason": w.reason,
        "owner": _person(users.get(w.owner_user_id)),
        "created_by": _person(users.get(w.created_by_id)),
        "created_at": w.created_at.isoformat() + "Z",
        "updated_at": w.updated_at.isoformat() + "Z",
        "expires_at": w.expires_at.isoformat() + "Z",
        "state": a11y.waiver_state(w),
    }


async def _check_owner(owner_id: int, project_id: int, session: AsyncSession) -> None:
    if not await _can_own(owner_id, project_id, session):
        raise HTTPException(status_code=422,
                            detail="Owner must be an active user with editor access to this project")


@router.get("/projects/{project_id}/accessibility")
async def accessibility_trend(
    project_id: int,
    days: int = Query(30, ge=1, le=180),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    await _project(project_id, principal, session)
    return await a11y.trend(session, project_id, days)


@router.get("/projects/{project_id}/accessibility/runs/{run_id}")
async def accessibility_run(
    project_id: int, run_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    await _project(project_id, principal, session)
    run = await session.get(TestRun, run_id)
    if not run or run.project_id != project_id:
        # Another project's run must look exactly like a missing one.
        raise HTTPException(status_code=404, detail="Run not found in this project")
    return await a11y.annotated_run(session, project_id, run_id)


@router.get("/projects/{project_id}/accessibility/waivers")
async def list_waivers(
    project_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:
    await _project(project_id, principal, session)
    rows = (await session.exec(select(AccessibilityWaiver).where(
        AccessibilityWaiver.project_id == project_id)
        .order_by(AccessibilityWaiver.expires_at))).all()
    users = await _users_by_id([r.owner_user_id for r in rows] + [r.created_by_id for r in rows], session)
    return [_serialize(r, users) for r in rows]


class WaiverCreate(BaseModel):
    rule_id: str = PydField(min_length=1, max_length=80)
    selector_pattern: str = PydField(default="*", max_length=300)
    reason: str = PydField(min_length=1, max_length=500)
    days: int = PydField(default=a11y.DEFAULT_WAIVER_DAYS, ge=1, le=a11y.MAX_WAIVER_DAYS)
    owner_user_id: Optional[int] = None


class WaiverUpdate(BaseModel):
    reason: Optional[str] = PydField(default=None, min_length=1, max_length=500)
    owner_user_id: Optional[int] = None
    # Explicit renewal: a new expiry `days` from now. Omitted = expiry unchanged.
    days: Optional[int] = PydField(default=None, ge=1, le=a11y.MAX_WAIVER_DAYS)


@router.post("/projects/{project_id}/accessibility/waivers")
async def create_waiver(
    project_id: int, body: WaiverCreate, request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await _project(project_id, principal, session, "editor")
    try:
        rule_id = a11y.validate_rule_id(body.rule_id)
        pattern = a11y.validate_pattern(body.selector_pattern)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    owner_id = body.owner_user_id or principal.user.id
    await _check_owner(owner_id, project_id, session)
    now = datetime.utcnow()
    waiver = AccessibilityWaiver(project_id=project_id, rule_id=rule_id, selector_pattern=pattern,
                                 reason=body.reason.strip(), owner_user_id=owner_id,
                                 created_by_id=principal.user.id, created_at=now,
                                 updated_at=now, expires_at=now)
    a11y.set_expiry(waiver, body.days, now)
    session.add(waiver)
    await session.flush()
    await audit_record(
        session, entity_type="accessibility_waiver", entity_id=waiver.id, action="create",
        workspace_id=project.workspace_id, principal=principal, request=request,
        changes={"project_id": project_id, "rule_id": rule_id, "selector_pattern": pattern,
                 "reason": waiver.reason, "owner_user_id": owner_id,
                 "expires_at": waiver.expires_at.isoformat()})
    await session.commit()
    await session.refresh(waiver)
    users = await _users_by_id([waiver.owner_user_id, waiver.created_by_id], session)
    return _serialize(waiver, users)


async def _load_waiver(waiver_id: int, principal: AuthPrincipal, session: AsyncSession):
    waiver = await session.get(AccessibilityWaiver, waiver_id)
    if not waiver:
        raise HTTPException(status_code=404, detail="Waiver not found")
    if not await access_service.has_project_access(principal.user.id, waiver.project_id, session):
        raise HTTPException(status_code=404, detail="Waiver not found")
    project = await _project(waiver.project_id, principal, session, "editor")
    return waiver, project


@router.put("/accessibility-waivers/{waiver_id}")
async def update_waiver(
    waiver_id: int, body: WaiverUpdate, request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    waiver, project = await _load_waiver(waiver_id, principal, session)
    changes: dict = {}
    if body.reason is not None and body.reason.strip() != waiver.reason:
        changes["reason"] = {"from": waiver.reason, "to": body.reason.strip()}
        waiver.reason = body.reason.strip()
    if body.owner_user_id is not None and body.owner_user_id != waiver.owner_user_id:
        await _check_owner(body.owner_user_id, waiver.project_id, session)
        changes["owner_user_id"] = {"from": waiver.owner_user_id, "to": body.owner_user_id}
        waiver.owner_user_id = body.owner_user_id
    if body.days is not None:
        before = waiver.expires_at.isoformat()
        a11y.set_expiry(waiver, body.days)
        changes["expires_at"] = {"from": before, "to": waiver.expires_at.isoformat()}
    else:
        waiver.updated_at = datetime.utcnow()
    if changes:
        session.add(waiver)
        await audit_record(
            session, entity_type="accessibility_waiver", entity_id=waiver.id,
            action="renew" if "expires_at" in changes else "update",
            workspace_id=project.workspace_id, principal=principal, request=request,
            changes={"project_id": waiver.project_id, "rule_id": waiver.rule_id, **changes})
        await session.commit()
        await session.refresh(waiver)
    users = await _users_by_id([waiver.owner_user_id, waiver.created_by_id], session)
    return _serialize(waiver, users)


@router.delete("/accessibility-waivers/{waiver_id}")
async def delete_waiver(
    waiver_id: int, request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    waiver, project = await _load_waiver(waiver_id, principal, session)
    await audit_record(
        session, entity_type="accessibility_waiver", entity_id=waiver.id, action="delete",
        workspace_id=project.workspace_id, principal=principal, request=request,
        changes={"project_id": waiver.project_id, "rule_id": waiver.rule_id,
                 "selector_pattern": waiver.selector_pattern, "reason": waiver.reason,
                 "state": a11y.waiver_state(waiver)})
    await session.delete(waiver)
    await session.commit()
    return {"deleted": waiver_id}
