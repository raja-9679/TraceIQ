"""Shareable read-only report links (decision R2).

    POST   /api/projects/{id}/share-links       run_report | project_summary (editor)
    GET    /api/projects/{id}/share-links       the project's links (viewer; no tokens)
    POST   /api/workspaces/{id}/share-links     workspace_summary | poc_report (member;
                                                covers the projects where the creator
                                                is an editor)
    GET    /api/workspaces/{id}/share-links     the workspace-level links (member;
                                                trust_document links are listed on the
                                                trust center, for admins only)
    DELETE /api/share-links/{id}                revoke (editor on the project; for a
                                                workspace link, its creator or a workspace admin)
    POST   /api/share/view  {"token": ...}     PUBLIC, rate-limited, every view audited

The token never travels in a URL a server sees: links are /share#<token> (a
fragment stays in the browser) and the page POSTs it in the body, so access
logs (uvicorn, nginx, proxies) cannot capture it.

Rules (tokens hashed, expiry, what the public payload may contain):
app/services/share_links.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.reports import _project, _workspace, accessible_projects
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import Project, ShareLink, TestCaseResult, TestRun, User, UserWorkspace, Workspace
from app.services import poc_report
from app.services import reporting
from app.services import share_links as sl
from app.services.access_service import _ROLE_MAP, _workspace_role_level, access_service
from app.services.audit import record as audit_record

router = APIRouter()

NOT_FOUND = "Share link not found or no longer available"


class ShareLinkCreate(BaseModel):
    kind: str
    run_id: Optional[int] = None
    expires_in_days: int = Field(default=sl.DEFAULT_DAYS)
    window_days: int = Field(default=30, ge=1, le=reporting.MAX_WINDOW_DAYS)
    label: Optional[str] = Field(default=None, max_length=120)
    # poc_report only: {start, end, project_ids, minutes_per_execution,
    # hourly_rate, currency}.
    params: Optional[Dict[str, Any]] = None


async def _creators(session: AsyncSession, links: List[ShareLink]) -> Dict[int, User]:
    ids = {l.created_by_id for l in links if l.created_by_id}
    if not ids:
        return {}
    return {u.id: u for u in (await session.exec(select(User).where(User.id.in_(ids)))).all()}


async def _issue(session: AsyncSession, request: Request, principal: AuthPrincipal,
                 workspace_id: int, project_id: Optional[int], body: ShareLinkCreate,
                 target_id: Optional[int], *, path: str = "/share",
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = datetime.utcnow()
    try:
        expires_at = sl.expiry(body.expires_in_days, now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    token = sl.new_token()
    link = ShareLink(workspace_id=workspace_id, project_id=project_id, kind=body.kind,
                     target_id=target_id, window_days=body.window_days,
                     label=(body.label or None), token_hash=sl.hash_token(token), params=params,
                     created_by_id=principal.user.id, created_at=now, expires_at=expires_at)
    session.add(link)
    await session.flush()
    await audit_record(session, entity_type="share_link", entity_id=link.id, action="create",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes={"kind": link.kind, "project_id": project_id,
                                "target_id": target_id, "expires_at": expires_at.isoformat()})
    await session.commit()
    await session.refresh(link)
    out = sl.describe(link, principal.user)
    # The only time the token leaves the server.
    out["token"] = token
    out["path"] = f"{path}#{token}"
    return out


@router.post("/projects/{project_id}/share-links")
async def create_project_share_link(project_id: int, request: Request,
                                    body: ShareLinkCreate = Body(...),
                                    session: AsyncSession = Depends(get_session),
                                    principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(session, principal, project_id, min_role="editor")
    target_id = None
    if body.kind == "run_report":
        run = await session.get(TestRun, body.run_id) if body.run_id else None
        # 404 for a run in another project: no confirming that the id exists.
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Run not found in this project")
        target_id = run.id
    elif body.kind != "project_summary":
        raise HTTPException(status_code=422, detail="kind must be run_report or project_summary")
    return await _issue(session, request, principal, project.workspace_id, project_id, body, target_id)


@router.get("/projects/{project_id}/share-links")
async def list_project_share_links(project_id: int, session: AsyncSession = Depends(get_session),
                                   principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(session, principal, project_id)
    links = (await session.exec(select(ShareLink).where(ShareLink.project_id == project_id)
                                .order_by(ShareLink.created_at.desc()))).all()
    who = await _creators(session, links)
    return [sl.describe(l, who.get(l.created_by_id)) for l in links]


@router.post("/workspaces/{workspace_id}/share-links")
async def create_workspace_share_link(workspace_id: int, request: Request,
                                      body: ShareLinkCreate = Body(...),
                                      session: AsyncSession = Depends(get_session),
                                      principal: AuthPrincipal = Depends(get_current_principal)):
    await _workspace(session, principal, workspace_id)
    if body.kind not in ("workspace_summary", "poc_report"):
        raise HTTPException(status_code=422, detail="kind must be workspace_summary or poc_report")
    if principal.api_key is not None and principal.api_key.project_id is not None:
        raise HTTPException(status_code=403, detail="A project-scoped API key cannot share a workspace")
    editable = await accessible_projects(session, principal, workspace_id, min_role="editor")
    if not editable:
        raise HTTPException(status_code=403, detail="Editor role on at least one project required")
    params = None
    if body.kind == "poc_report":
        params = poc_share_params(body.params or {}, {p.id for p in editable})
    return await _issue(session, request, principal, workspace_id, None, body, None, params=params)


def poc_share_params(raw: Dict[str, Any], editable_ids: set) -> Dict[str, Any]:
    """Validated, frozen inputs of a poc_report link. A selection may name
    only projects the creator can share (editor); none = all of those."""
    try:
        start, end = poc_report.parse_range(raw.get("start"), raw.get("end"))
        assumptions = poc_report.parse_assumptions(raw.get("minutes_per_execution"),
                                                   raw.get("hourly_rate"), raw.get("currency"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ids = raw.get("project_ids") or []
    if not isinstance(ids, list) or len(ids) > 500 or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise HTTPException(status_code=422, detail="project_ids must be a list of project ids")
    if any(i not in editable_ids for i in ids):
        raise HTTPException(status_code=403, detail="You can share only projects where you are an editor")
    return {"start": start.isoformat(), "end": end.isoformat(), "project_ids": sorted(set(ids)),
            **assumptions}


@router.get("/workspaces/{workspace_id}/share-links")
async def list_workspace_share_links(workspace_id: int, kind: Optional[str] = None,
                                     session: AsyncSession = Depends(get_session),
                                     principal: AuthPrincipal = Depends(get_current_principal)):
    await _workspace(session, principal, workspace_id)
    q = select(ShareLink).where(ShareLink.workspace_id == workspace_id, ShareLink.project_id.is_(None),
                                # Recipients of gated documents are an admin matter.
                                ShareLink.kind != "trust_document")
    if kind:
        q = q.where(ShareLink.kind == kind)
    links = (await session.exec(q.order_by(ShareLink.created_at.desc()))).all()
    who = await _creators(session, links)
    return [sl.describe(l, who.get(l.created_by_id)) for l in links]


async def _is_workspace_admin(session: AsyncSession, user_id: int, workspace_id: int) -> bool:
    for uw in (await session.exec(select(UserWorkspace).where(
            UserWorkspace.user_id == user_id, UserWorkspace.workspace_id == workspace_id))).all():
        if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
            return True
    return False


@router.delete("/share-links/{link_id}")
async def revoke_share_link(link_id: int, request: Request,
                            session: AsyncSession = Depends(get_session),
                            principal: AuthPrincipal = Depends(get_current_principal)):
    link = await session.get(ShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found")
    if link.project_id is not None:
        await _project(session, principal, link.project_id, min_role="editor")
    elif link.kind == "trust_document":
        await _workspace(session, principal, link.workspace_id)
        if not await _is_workspace_admin(session, principal.user.id, link.workspace_id):
            raise HTTPException(status_code=403, detail="Workspace admin access required")
    else:
        await _workspace(session, principal, link.workspace_id)
        if link.created_by_id != principal.user.id and not await _is_workspace_admin(
                session, principal.user.id, link.workspace_id):
            raise HTTPException(status_code=403, detail="Only its creator or a workspace admin can revoke this link")
    if link.revoked_at is None:
        link.revoked_at = datetime.utcnow()
        session.add(link)
        await audit_record(session, entity_type="share_link", entity_id=link.id, action="revoke",
                           workspace_id=link.workspace_id, principal=principal, request=request,
                           changes={"kind": link.kind, "project_id": link.project_id})
        await session.commit()
        await session.refresh(link)
    return sl.describe(link, None)


# ---------------------------------------------------------------------------
# Public view
# ---------------------------------------------------------------------------


async def build_public_payload(session: AsyncSession, link: ShareLink) -> Optional[Dict[str, Any]]:
    """The reduced view, or None when the link must not resolve (creator
    gone or lost access, target deleted or moved)."""
    creator = await session.get(User, link.created_by_id) if link.created_by_id else None
    if not creator or not getattr(creator, "is_active", True):
        return None
    if link.kind in ("workspace_summary", "poc_report"):
        ws = await session.get(Workspace, link.workspace_id)
        if not ws:
            return None
        if not (await session.exec(select(UserWorkspace).where(
                UserWorkspace.workspace_id == ws.id, UserWorkspace.user_id == creator.id))).first():
            return None
        projects = await accessible_projects(session, AuthPrincipal(user=creator), ws.id,
                                             min_role="editor")
        if link.kind == "poc_report":
            p = link.params or {}
            picked = set(p.get("project_ids") or [])
            if picked:
                projects = [x for x in projects if x.id in picked]
            if not projects:
                return None
            try:
                start, end = poc_report.parse_range(p.get("start"), p.get("end"))
                assumptions = poc_report.parse_assumptions(
                    p.get("minutes_per_execution"), p.get("hourly_rate"), p.get("currency"))
            except ValueError:
                return None
            report = await poc_report.build(session, ws, projects, start, end, assumptions)
            return poc_report.public_payload(report)
        snap = await reporting.workspace_snapshot(session, ws, projects, link.window_days)
        return sl.public_summary(snap)

    project = await session.get(Project, link.project_id) if link.project_id else None
    if not project or project.workspace_id != link.workspace_id:
        return None
    if not await access_service.has_project_access(creator.id, project.id, session, min_role="editor"):
        return None
    if link.kind == "project_summary":
        snap = await reporting.project_snapshot(session, project, link.window_days)
        return sl.public_summary(snap)
    if link.kind == "run_report":
        run = await session.get(TestRun, link.target_id) if link.target_id else None
        if not run or run.project_id != project.id:
            return None
        results = (await session.exec(select(TestCaseResult).where(
            TestCaseResult.test_run_id == run.id).order_by(TestCaseResult.id))).all()
        return sl.public_run_report(run, results, project.name)
    return None


@router.post("/share/view")
@limiter.limit("30/minute")
async def public_share(request: Request, body: Dict[str, Any] = Body(...),
                       session: AsyncSession = Depends(get_session)):
    """PUBLIC - no auth. The token is the credential: it arrives in the body
    (never a URL, see module docstring), is hashed before the lookup and is
    never logged or audited."""
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token or len(token) > 128:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    link = (await session.exec(select(ShareLink).where(
        ShareLink.token_hash == sl.hash_token(token)))).first()
    now = datetime.utcnow()
    if not link or sl.state(link, now) != "active":
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    payload = await build_public_payload(session, link)
    if payload is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = now
    session.add(link)
    await audit_record(session, entity_type="share_link", entity_id=link.id, action="view",
                       workspace_id=link.workspace_id, request=request,
                       actor_type="public", actor_label="share link viewer",
                       changes={"kind": link.kind, "project_id": link.project_id,
                                "view_count": link.view_count})
    await session.commit()
    payload["expires_at"] = link.expires_at.isoformat()
    payload["label"] = link.label
    return payload
