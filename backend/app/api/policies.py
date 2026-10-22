"""Policies page API (WP14b, E22 / decisions W2, W4).

    GET  /api/policy/rules                          every registered rule (static catalog)
    GET  /api/workspaces/{id}/ai-policy             the workspace AI opt-out (members)
    PUT  /api/workspaces/{id}/ai-policy             switch AI on/off (workspace admin, humans)
    GET  /api/workspaces/{id}/policy-decisions      recent denials in the workspace (workspace admin)
    GET  /api/admin/policy-decisions                every denial, incl. instance-level (instance admin)

Denials are workspace-scoped evidence of who was refused what, so they are an
admin read like the audit history; a project-scoped API key is refused.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.api.share_links import _is_workspace_admin
from app.services.access_service import access_service
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import PolicyDecisionLog, Project, User, UserWorkspace, Workspace
from app.services import policy
from app.services.audit import record as audit_record

router = APIRouter()

MAX_LIMIT = 500


async def _workspace(session: AsyncSession, workspace_id: int) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


async def _member(session: AsyncSession, principal: AuthPrincipal, workspace_id: int) -> Workspace:
    ws = await _workspace(session, workspace_id)
    if principal.api_key is not None and principal.api_key.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="API key is not scoped to this workspace")
    # per_project: the AI switch is one workspace flag; a project-scoped key of
    # this workspace may read it (require_workspace_admin refuses it below).
    if not await access_service.workspace_membership(principal.user.id, workspace_id, session,
                                                     per_project=True):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return ws


async def require_workspace_admin(session: AsyncSession, principal: AuthPrincipal,
                                  workspace_id: int) -> Workspace:
    ws = await _member(session, principal, workspace_id)
    if principal.api_key is not None and principal.api_key.project_id is not None:
        raise HTTPException(status_code=403, detail="A project-scoped API key cannot read this")
    if not await _is_workspace_admin(session, principal.user.id, workspace_id):
        raise HTTPException(status_code=403, detail="Workspace admin access required")
    return ws


@router.get("/policy/rules")
async def list_rules(principal: AuthPrincipal = Depends(get_current_principal)) -> List[Dict[str, Any]]:
    """The rule catalog: static descriptions only, no tenant data."""
    return policy.catalog()


class AiPolicyBody(BaseModel):
    ai_enabled: bool


def _ai_policy(ws: Workspace) -> Dict[str, Any]:
    return {"workspace_id": ws.id, "ai_enabled": bool(ws.ai_enabled)}


@router.get("/workspaces/{workspace_id}/ai-policy")
async def get_ai_policy(workspace_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    ws = await _member(session, principal, workspace_id)
    out = _ai_policy(ws)
    out["can_edit"] = (principal.api_key is None
                       and await _is_workspace_admin(session, principal.user.id, workspace_id))
    return out


@router.put("/workspaces/{workspace_id}/ai-policy")
async def set_ai_policy(workspace_id: int, body: AiPolicyBody = Body(...),
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    # Humans only: an agent must not be able to switch its own AI back on.
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="API keys cannot change the AI policy")
    ws = await require_workspace_admin(session, principal, workspace_id)
    before = bool(ws.ai_enabled)
    ws.ai_enabled = bool(body.ai_enabled)
    session.add(ws)
    if before != ws.ai_enabled:
        await audit_record(session, entity_type="workspace", entity_id=ws.id,
                           action="ai_enabled" if ws.ai_enabled else "ai_disabled",
                           workspace_id=ws.id, principal=principal,
                           changes={"ai_enabled": {"old": before, "new": ws.ai_enabled}})
    await session.commit()
    out = _ai_policy(ws)
    out["can_edit"] = True
    return out


def _filtered(query, *, action: Optional[str], rule: Optional[str], agent_id: Optional[str],
              project_id: Optional[int], days: int):
    if action:
        query = query.where(PolicyDecisionLog.action == action)
    if rule:
        query = query.where(PolicyDecisionLog.rule == rule)
    if agent_id:
        query = query.where(PolicyDecisionLog.agent_id == agent_id)
    if project_id:
        query = query.where(PolicyDecisionLog.project_id == project_id)
    if days:
        query = query.where(PolicyDecisionLog.created_at >= datetime.utcnow() - timedelta(days=days))
    return query


async def _page(session: AsyncSession, base, limit: int, offset: int) -> Dict[str, Any]:
    total = (await session.exec(select(func.count()).select_from(base.subquery()))).one()
    rows = (await session.exec(base.order_by(PolicyDecisionLog.created_at.desc(),
                                             PolicyDecisionLog.id.desc())
                               .offset(offset).limit(limit))).all()
    project_ids = {r.project_id for r in rows if r.project_id}
    user_ids = {r.user_id for r in rows if r.user_id}
    names = {p.id: p.name for p in (await session.exec(
        select(Project).where(Project.id.in_(project_ids)))).all()} if project_ids else {}
    emails = {u.id: u.email for u in (await session.exec(
        select(User).where(User.id.in_(user_ids)))).all()} if user_ids else {}
    return {
        "total": total,
        "items": [{
            "id": r.id, "created_at": r.created_at, "action": r.action, "rule": r.rule,
            "reason": r.reason, "workspace_id": r.workspace_id, "project_id": r.project_id,
            "project_name": names.get(r.project_id), "user_id": r.user_id,
            "user_email": emails.get(r.user_id), "api_key_id": r.api_key_id,
            "agent_id": r.agent_id, "agent_session_id": r.agent_session_id, "data": r.data or {},
        } for r in rows],
    }


@router.get("/workspaces/{workspace_id}/policy-decisions")
async def workspace_denials(
    workspace_id: int,
    action: Optional[str] = None, rule: Optional[str] = None, agent_id: Optional[str] = None,
    project_id: Optional[int] = None, days: int = Query(30, ge=0, le=3650),
    limit: int = Query(100, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    await require_workspace_admin(session, principal, workspace_id)
    base = _filtered(select(PolicyDecisionLog).where(PolicyDecisionLog.workspace_id == workspace_id),
                     action=action, rule=rule, agent_id=agent_id, project_id=project_id, days=days)
    out = await _page(session, base, limit, offset)
    out["workspace_id"] = workspace_id
    return out


@router.get("/admin/policy-decisions")
async def instance_denials(
    workspace_id: Optional[int] = None,
    action: Optional[str] = None, rule: Optional[str] = None, agent_id: Optional[str] = None,
    project_id: Optional[int] = None, days: int = Query(30, ge=0, le=3650),
    limit: int = Query(100, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    base = select(PolicyDecisionLog)
    if workspace_id:
        base = base.where(PolicyDecisionLog.workspace_id == workspace_id)
    base = _filtered(base, action=action, rule=rule, agent_id=agent_id,
                     project_id=project_id, days=days)
    return await _page(session, base, limit, offset)
