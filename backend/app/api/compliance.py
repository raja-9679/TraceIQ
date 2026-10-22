"""Compliance: controls, evidence, exceptions, evidence packs, restore drills (WP13a).

Controls (workspace members read; workspace admin — or tenant admin — writes):
    GET    /compliance/evidence-sources
    GET    /workspaces/{id}/compliance/controls?framework&status&owner_id
    POST   /workspaces/{id}/compliance/controls
    POST   /workspaces/{id}/compliance/controls/load-soc2      idempotent starter set
    GET    /workspaces/{id}/compliance/members                 owner picker
    GET    /compliance/controls/{cid}                          detail + evidence + exceptions + history
    PATCH  /compliance/controls/{cid}
    DELETE /compliance/controls/{cid}
    POST   /compliance/controls/{cid}/evidence                 multipart: note, outcome, file (admin or owner)
    GET    /compliance/evidence/{eid}/file                     attachment download (audited)
    DELETE /compliance/evidence/{eid}
    POST   /compliance/controls/{cid}/exceptions               request (admin or owner)
    POST   /compliance/exceptions/{xid}/approve|reject|revoke  admin; approve is four-eyes

Evidence packs (workspace admin):
    POST   /workspaces/{id}/evidence-packs                     {period_start, period_end} → queued
    GET    /workspaces/{id}/evidence-packs
    GET    /evidence-packs/{pid}
    POST   /evidence-packs/{pid}/download                      → short-lived presigned URL (audited)
    POST   /evidence-packs/verify                              multipart: file (zip) [+ manifest]

Restore drills:
    POST   /admin/restore-drills          instance admin (see report_restore_drill for why)
    GET    /admin/restore-drills          instance admin, full detail
    GET    /workspaces/{id}/restore-drills  members, verdicts only

Every write is audited; changes to a control, its evidence and its exceptions
are all recorded against entity_type "compliance_control" so the control's
history is one query. Writes need a person (API keys are refused) except the
drill report, which a cron job sends.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.storage import object_store
from app.models import (
    AuditLog, ComplianceControl, ControlEvidence, ControlException, DrillResult, EvidencePack,
    User, UserWorkspace, Workspace,
)
from app.services import audit
from app.services import compliance as svc
from app.services.rbac_service import rbac_service
from app.services import key_scope
from app.services.access_service import access_service

router = APIRouter()

#: A pack that has been queued/building this long is treated as failed (the
#: worker died) so the workspace can start another.
PACK_TIMEOUT = timedelta(hours=2)
PACK_MAX_SPAN_DAYS = 1100
DOWNLOAD_URL_SECONDS = 300


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

async def workspace_role(session: AsyncSession, workspace_id: int,
                         principal: AuthPrincipal) -> str:
    """'admin' or 'member'; 403 for anyone else.

    Admin = the workspace:manage_users permission, which a Workspace Admin
    holds and a Tenant Admin of the workspace's tenant holds without being a
    member. An API key only ever sees its own workspace, and a project-scoped
    key sees none of this (controls are workspace-level).
    """
    if not await session.get(Workspace, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    key = principal.api_key
    if key is not None and (key.workspace_id != workspace_id or key.project_id is not None):
        raise HTTPException(status_code=403, detail="This API key cannot read this workspace's compliance data")
    uid = principal.user.id
    if await rbac_service.has_permission(session, uid, "workspace:manage_users", workspace_id=workspace_id):
        return "admin"
    member = await access_service.workspace_membership(uid, workspace_id, session)
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    # Pre-RBAC memberships carry only the legacy role string (still capped
    # by an API key's role).
    if member.role_id is None and (member.role or "").lower() in ("admin", "owner") \
            and key_scope.allows_workspace(session, uid, workspace_id, min_level=3):
        return "admin"
    return "member"


def _human(principal: AuthPrincipal) -> User:
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="Compliance changes must be made by a person, not an API key")
    return principal.user


async def _require_admin(session, workspace_id: int, principal: AuthPrincipal) -> User:
    user = _human(principal)
    if await workspace_role(session, workspace_id, principal) != "admin":
        raise HTTPException(status_code=403, detail="Workspace admin access required")
    return user


async def _load_control(session, control_id: int, principal: AuthPrincipal):
    control = await session.get(ComplianceControl, control_id)
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    role = await workspace_role(session, control.workspace_id, principal)
    return control, role


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

@router.get("/compliance/evidence-sources")
async def evidence_sources(principal: AuthPrincipal = Depends(get_current_principal)):
    return [{"type": s.type, "label": s.label, "description": s.description, "defaults": s.defaults}
            for s in svc.SOURCES.values()]


class ControlIn(BaseModel):
    framework: str = Field(min_length=1, max_length=60)
    reference: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    owner_id: Optional[int] = None
    evidence_sources: List[Dict[str, Any]] = Field(default_factory=list)
    freshness_days: int = svc.DEFAULT_FRESHNESS_DAYS


class ControlPatch(BaseModel):
    framework: Optional[str] = Field(default=None, min_length=1, max_length=60)
    reference: Optional[str] = Field(default=None, min_length=1, max_length=40)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    owner_id: Optional[int] = None
    evidence_sources: Optional[List[Dict[str, Any]]] = None
    freshness_days: Optional[int] = None


async def _check_owner(session, workspace_id: int, owner_id: Optional[int]) -> None:
    if owner_id is None:
        return
    member = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.user_id == owner_id, UserWorkspace.workspace_id == workspace_id))).first()
    if not member:
        raise HTTPException(status_code=422, detail="The owner must be a member of this workspace")


def _snapshot(c: ComplianceControl) -> Dict[str, Any]:
    return {"framework": c.framework, "reference": c.reference, "title": c.title,
            "owner_id": c.owner_id, "evidence_sources": c.evidence_sources,
            "freshness_days": c.freshness_days}


@router.get("/workspaces/{workspace_id}/compliance/controls")
async def list_controls(
    workspace_id: int,
    framework: Optional[str] = None,
    status: Optional[str] = None,
    owner_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    role = await workspace_role(session, workspace_id, principal)
    rows = await svc.controls_overview(session, workspace_id)
    frameworks = sorted({r["framework"] for r in rows})
    counts = svc.status_counts(rows)
    if framework:
        rows = [r for r in rows if r["framework"] == framework]
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        rows = [r for r in rows if r["status"] in wanted]
    if owner_id is not None:
        rows = [r for r in rows if r["owner_id"] == owner_id]
    return {"workspace_id": workspace_id, "can_edit": role == "admin", "frameworks": frameworks,
            "counts": counts, "controls": rows}


@router.post("/workspaces/{workspace_id}/compliance/controls", status_code=201)
async def create_control(
    workspace_id: int, body: ControlIn, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    user = await _require_admin(session, workspace_id, principal)
    try:
        sources = svc.validate_sources(body.evidence_sources)
        freshness = svc.validate_freshness(body.freshness_days)
    except svc.ComplianceError as exc:
        raise _bad(exc)
    await _check_owner(session, workspace_id, body.owner_id)
    control = ComplianceControl(
        workspace_id=workspace_id, framework=body.framework.strip(), reference=body.reference.strip(),
        title=body.title.strip(), description=(body.description or "").strip() or None,
        owner_id=body.owner_id, evidence_sources=sources, freshness_days=freshness,
        created_by_id=user.id)
    session.add(control)
    await session.flush()
    await audit.record(session, entity_type="compliance_control", entity_id=control.id, action="create",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes=_snapshot(control))
    await session.commit()
    return await get_control(control.id, session=session, principal=principal)


@router.post("/workspaces/{workspace_id}/compliance/controls/load-soc2")
async def load_soc2_starter(
    workspace_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Create the SOC 2 starter controls this workspace does not have yet.

    Idempotent by `seed_key`: a control loaded before — even if an admin has
    since edited it — is left alone, and one an admin deleted comes back only
    because they asked for the starter set again.
    """
    user = await _require_admin(session, workspace_id, principal)
    existing = set((await session.exec(select(ComplianceControl.seed_key).where(
        ComplianceControl.workspace_id == workspace_id,
        ComplianceControl.seed_key != None))).all())  # noqa: E711
    created = []
    for item in svc.SOC2_STARTER:
        seed_key = f"soc2:{item['key']}"
        if seed_key in existing:
            continue
        session.add(ComplianceControl(
            workspace_id=workspace_id, framework="SOC 2", reference=item["reference"],
            title=item["title"], description=item["description"],
            evidence_sources=svc.validate_sources(item["sources"]),
            freshness_days=svc.DEFAULT_FRESHNESS_DAYS, seed_key=seed_key, created_by_id=user.id))
        created.append(item["reference"] + " " + item["title"])
    if created:
        await audit.record(session, entity_type="workspace", entity_id=workspace_id,
                           action="compliance_load_soc2", workspace_id=workspace_id,
                           principal=principal, request=request,
                           changes={"created": len(created), "controls": created})
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent click loaded them first; the unique key did its job.
            await session.rollback()
            created = []
    return {"created": len(created), "already_present": len(existing & {
        f"soc2:{i['key']}" for i in svc.SOC2_STARTER}), "total": len(svc.SOC2_STARTER)}


@router.get("/workspaces/{workspace_id}/compliance/members")
async def control_owner_candidates(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    await workspace_role(session, workspace_id, principal)
    users = (await session.exec(select(User).join(UserWorkspace, UserWorkspace.user_id == User.id)
                                .where(UserWorkspace.workspace_id == workspace_id,
                                       User.is_active == True)  # noqa: E712
                                .order_by(User.email))).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name} for u in users]


@router.get("/compliance/controls/{control_id}")
async def get_control(
    control_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    control, role = await _load_control(session, control_id, principal)
    [row] = await svc.controls_overview(session, control.workspace_id, control_ids=[control.id],
                                        with_detail=True)
    history = (await session.exec(select(AuditLog).where(
        AuditLog.workspace_id == control.workspace_id,
        AuditLog.entity_type == "compliance_control", AuditLog.entity_id == control.id)
        .order_by(AuditLog.id.desc()).limit(200))).all()
    people = await svc.people_by_id(session, [h.user_id for h in history])
    row["history"] = [{"id": h.id, "at": h.timestamp, "action": h.action,
                       "by": people.get(h.user_id) or h.actor_label, "changes": h.changes}
                      for h in history]
    me = principal.user.id
    row["can_edit"] = role == "admin"
    row["can_contribute"] = role == "admin" or control.owner_id == me
    row["me"] = me
    return row


@router.patch("/compliance/controls/{control_id}")
async def update_control(
    control_id: int, body: ControlPatch, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    control = await session.get(ComplianceControl, control_id)
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    await _require_admin(session, control.workspace_id, principal)
    before = _snapshot(control)
    fields = body.model_fields_set
    try:
        if "evidence_sources" in fields:
            control.evidence_sources = svc.validate_sources(body.evidence_sources or [])
        if "freshness_days" in fields and body.freshness_days is not None:
            control.freshness_days = svc.validate_freshness(body.freshness_days)
    except svc.ComplianceError as exc:
        raise _bad(exc)
    for name in ("framework", "reference", "title"):
        if name in fields and getattr(body, name):
            setattr(control, name, getattr(body, name).strip())
    if "description" in fields:
        control.description = (body.description or "").strip() or None
    if "owner_id" in fields:
        await _check_owner(session, control.workspace_id, body.owner_id)
        control.owner_id = body.owner_id
    after = _snapshot(control)
    changed = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if "description" in fields:
        changed["description"] = "updated"
    if changed:
        control.updated_at = datetime.utcnow()
        session.add(control)
        await audit.record(session, entity_type="compliance_control", entity_id=control.id,
                           action="update", workspace_id=control.workspace_id,
                           principal=principal, request=request, changes=changed)
        await session.commit()
    return await get_control(control.id, session=session, principal=principal)


@router.delete("/compliance/controls/{control_id}")
async def delete_control(
    control_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    control = await session.get(ComplianceControl, control_id)
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    await _require_admin(session, control.workspace_id, principal)
    evidence = (await session.exec(select(ControlEvidence).where(
        ControlEvidence.control_id == control.id))).all()
    exceptions = (await session.exec(select(ControlException).where(
        ControlException.control_id == control.id))).all()
    keys = [e.file_key for e in evidence if e.file_key]
    for row in [*evidence, *exceptions]:
        await session.delete(row)
    await session.flush()
    await session.delete(control)
    await audit.record(session, entity_type="compliance_control", entity_id=control.id,
                       action="delete", workspace_id=control.workspace_id, principal=principal,
                       request=request, changes={**_snapshot(control), "evidence_removed": len(evidence),
                                                 "exceptions_removed": len(exceptions)})
    await session.commit()
    for key in keys:
        try:
            await asyncio.to_thread(object_store.delete_object, key)
        except Exception:  # noqa: BLE001 — the orphan sweep / purge prefix covers it
            pass
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Manual evidence
# ---------------------------------------------------------------------------

@router.post("/compliance/controls/{control_id}/evidence", status_code=201)
async def add_evidence(
    control_id: int, request: Request,
    note: Optional[str] = Form(default=None),
    outcome: Literal["pass", "fail"] = Form(default="pass"),
    file: Optional[UploadFile] = File(default=None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    control, role = await _load_control(session, control_id, principal)
    user = _human(principal)
    if role != "admin" and control.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only a workspace admin or the control's owner can add evidence")
    note = (note or "").strip() or None
    if note and len(note) > 4000:
        raise HTTPException(status_code=422, detail="The note is too long (4000 characters)")
    if not note and file is None:
        raise HTTPException(status_code=422, detail="Add a note, a file, or both")

    ev = ControlEvidence(control_id=control.id, workspace_id=control.workspace_id,
                         outcome=outcome, note=note, created_by_id=user.id)
    if file is not None:
        try:
            ctype = svc.evidence_file_type(file.filename or "")
        except svc.ComplianceError as exc:
            raise _bad(exc)
        data = await file.read(svc.MAX_EVIDENCE_BYTES + 1)
        if len(data) > svc.MAX_EVIDENCE_BYTES:
            raise HTTPException(status_code=413, detail=f"Files are limited to {svc.MAX_EVIDENCE_BYTES // (1024 * 1024)} MB")
        if not data:
            raise HTTPException(status_code=422, detail="The file is empty")
        name = svc.safe_filename(file.filename or "evidence")
        key = (f"{svc.workspace_prefix(control.workspace_id)}controls/{control.id}/"
               f"{uuid.uuid4().hex[:16]}-{name}")
        try:
            await asyncio.to_thread(object_store.upload_fileobj, io.BytesIO(data), key, ctype)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=502, detail="Could not store the file")
        ev.file_key, ev.file_name, ev.content_type = key, name, ctype
        ev.size_bytes, ev.sha256 = len(data), hashlib.sha256(data).hexdigest()
    session.add(ev)
    await session.flush()
    await audit.record(session, entity_type="compliance_control", entity_id=control.id,
                       action="evidence_add", workspace_id=control.workspace_id,
                       principal=principal, request=request,
                       changes={"evidence_id": ev.id, "outcome": outcome, "file_name": ev.file_name,
                                "sha256": ev.sha256, "size_bytes": ev.size_bytes,
                                "note": (note or "")[:200] or None})
    await session.commit()
    return svc.evidence_view(ev, {user.id: user.email})


async def _load_evidence(session, evidence_id: int, principal: AuthPrincipal):
    ev = await session.get(ControlEvidence, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evidence not found")
    role = await workspace_role(session, ev.workspace_id, principal)
    return ev, role


@router.get("/compliance/evidence/{evidence_id}/file")
async def download_evidence(
    evidence_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    ev, _ = await _load_evidence(session, evidence_id, principal)
    if not ev.file_key:
        raise HTTPException(status_code=404, detail="This evidence has no file")
    try:
        data = await asyncio.to_thread(object_store.get_object_bytes, ev.file_key,
                                       svc.MAX_EVIDENCE_BYTES)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Could not read the file")
    await audit.record(session, entity_type="compliance_control", entity_id=ev.control_id,
                       action="evidence_download", workspace_id=ev.workspace_id,
                       principal=principal, request=request,
                       changes={"evidence_id": ev.id, "file_name": ev.file_name})
    await session.commit()
    return Response(data, media_type=ev.content_type or "application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{ev.file_name or "evidence"}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "no-store",
    })


@router.delete("/compliance/evidence/{evidence_id}")
async def delete_evidence(
    evidence_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    ev, _ = await _load_evidence(session, evidence_id, principal)
    await _require_admin(session, ev.workspace_id, principal)
    key = ev.file_key
    await session.delete(ev)
    await audit.record(session, entity_type="compliance_control", entity_id=ev.control_id,
                       action="evidence_delete", workspace_id=ev.workspace_id,
                       principal=principal, request=request,
                       changes={"evidence_id": ev.id, "file_name": ev.file_name, "sha256": ev.sha256})
    await session.commit()
    if key:
        try:
            await asyncio.to_thread(object_store.delete_object, key)
        except Exception:  # noqa: BLE001
            pass
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Exceptions (four-eyes)
# ---------------------------------------------------------------------------

class ExceptionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)
    compensating_control: Optional[str] = Field(default=None, max_length=4000)
    expires_at: datetime


class ExceptionDecision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


def _naive(dt: datetime) -> datetime:
    return (dt - dt.utcoffset()).replace(tzinfo=None) if dt.tzinfo else dt


@router.post("/compliance/controls/{control_id}/exceptions", status_code=201)
async def request_exception(
    control_id: int, body: ExceptionIn, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    control, role = await _load_control(session, control_id, principal)
    user = _human(principal)
    if role != "admin" and control.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only a workspace admin or the control's owner can request an exception")
    now = datetime.utcnow()
    expires = _naive(body.expires_at)
    try:
        svc.validate_exception_request(body.reason, expires, now)
    except svc.ComplianceError as exc:
        raise _bad(exc)
    exc_row = ControlException(
        control_id=control.id, workspace_id=control.workspace_id, reason=body.reason.strip(),
        compensating_control=(body.compensating_control or "").strip() or None,
        requested_by_id=user.id, requested_at=now, expires_at=expires)
    session.add(exc_row)
    await session.flush()
    await audit.record(session, entity_type="compliance_control", entity_id=control.id,
                       action="exception_request", workspace_id=control.workspace_id,
                       principal=principal, request=request,
                       changes={"exception_id": exc_row.id, "reason": exc_row.reason,
                                "compensating_control": exc_row.compensating_control,
                                "expires_at": expires.isoformat() + "Z"})
    await session.commit()
    return svc.exception_view(exc_row, {user.id: user.email}, now)


async def _decide(session, exception_id: int, principal: AuthPrincipal, request,
                  action: str, note: Optional[str]):
    exc_row = await session.get(ControlException, exception_id)
    if not exc_row:
        raise HTTPException(status_code=404, detail="Exception not found")
    user = await _require_admin(session, exc_row.workspace_id, principal)
    now = datetime.utcnow()
    if action == "approve":
        try:
            svc.check_approval(exc_row, user.id, now)
        except svc.ComplianceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        exc_row.status, exc_row.approved_by_id, exc_row.approved_at = "approved", user.id, now
    elif action == "reject":
        if exc_row.status != "pending":
            raise HTTPException(status_code=409, detail=f"This exception is {exc_row.status}, not pending")
        exc_row.status, exc_row.approved_by_id, exc_row.approved_at = "rejected", user.id, now
    else:  # revoke
        if svc.exception_state(exc_row, now) != "approved":
            raise HTTPException(status_code=409, detail="Only an approved, unexpired exception can be revoked")
        exc_row.status = "revoked"
    exc_row.decided_note = (note or "").strip() or exc_row.decided_note
    session.add(exc_row)
    await audit.record(session, entity_type="compliance_control", entity_id=exc_row.control_id,
                       action=f"exception_{action}", workspace_id=exc_row.workspace_id,
                       principal=principal, request=request,
                       changes={"exception_id": exc_row.id, "requested_by_id": exc_row.requested_by_id,
                                "expires_at": exc_row.expires_at.isoformat() + "Z",
                                "note": exc_row.decided_note})
    await session.commit()
    people = await svc.people_by_id(session, [exc_row.requested_by_id, exc_row.approved_by_id])
    return svc.exception_view(exc_row, people, now)


@router.post("/compliance/exceptions/{exception_id}/approve")
async def approve_exception(exception_id: int, request: Request,
                            body: Optional[ExceptionDecision] = None,
                           session: AsyncSession = Depends(get_session),
                            principal: AuthPrincipal = Depends(get_current_principal)):
    return await _decide(session, exception_id, principal, request, "approve", body.note if body else None)


@router.post("/compliance/exceptions/{exception_id}/reject")
async def reject_exception(exception_id: int, request: Request,
                           body: Optional[ExceptionDecision] = None,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    return await _decide(session, exception_id, principal, request, "reject", body.note if body else None)


@router.post("/compliance/exceptions/{exception_id}/revoke")
async def revoke_exception(exception_id: int, request: Request,
                           body: Optional[ExceptionDecision] = None,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    return await _decide(session, exception_id, principal, request, "revoke", body.note if body else None)


# ---------------------------------------------------------------------------
# Evidence packs
# ---------------------------------------------------------------------------

class PackIn(BaseModel):
    period_start: datetime
    period_end: datetime


def _pack_status(p: EvidencePack, now: datetime) -> str:
    if p.status in ("queued", "building") and p.created_at < now - PACK_TIMEOUT:
        return "failed"
    return p.status


def _pack_view(p: EvidencePack, people: Dict[int, str], now: datetime) -> Dict[str, Any]:
    status = _pack_status(p, now)
    return {"id": p.id, "workspace_id": p.workspace_id, "status": status,
            "period_start": p.period_start, "period_end": p.period_end,
            "requested_by": people.get(p.requested_by_id), "created_at": p.created_at,
            "completed_at": p.completed_at, "size_bytes": p.size_bytes, "sha256": p.sha256,
            "file_count": p.file_count,
            "error": p.error or ("timed out — the build did not finish" if status != p.status else None),
            "manifest_signature": ((p.manifest or {}).get("signature") or {}).get("value"),
            "files": (p.manifest or {}).get("files") or []}


def dispatch_pack(pack_id: int) -> None:
    from app.tasks.compliance_tasks import build_evidence_pack
    build_evidence_pack.delay(pack_id)


@router.post("/workspaces/{workspace_id}/evidence-packs", status_code=202)
async def create_evidence_pack(
    workspace_id: int, body: PackIn, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    user = await _require_admin(session, workspace_id, principal)
    now = datetime.utcnow()
    start, end = _naive(body.period_start), _naive(body.period_end)
    if start >= end:
        raise HTTPException(status_code=422, detail="period_start must be before period_end")
    if end > now + timedelta(days=1):
        raise HTTPException(status_code=422, detail="period_end cannot be in the future")
    if (end - start).days > PACK_MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail=f"A pack covers at most {PACK_MAX_SPAN_DAYS} days")
    busy = (await session.exec(select(EvidencePack).where(
        EvidencePack.workspace_id == workspace_id, EvidencePack.status.in_(["queued", "building"]),
        EvidencePack.created_at > now - PACK_TIMEOUT))).first()
    if busy:
        raise HTTPException(status_code=409, detail="A pack for this workspace is already being built")
    pack = EvidencePack(workspace_id=workspace_id, requested_by_id=user.id,
                        period_start=start, period_end=end)
    session.add(pack)
    await session.flush()
    await audit.record(session, entity_type="evidence_pack", entity_id=pack.id, action="generate",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes={"period_start": start.isoformat() + "Z",
                                "period_end": end.isoformat() + "Z"})
    await session.commit()
    try:
        dispatch_pack(pack.id)
    except Exception:  # noqa: BLE001 — broker down: say so instead of queueing forever
        pack.status, pack.error, pack.completed_at = "failed", "could not queue the build", now
        session.add(pack)
        await session.commit()
    return _pack_view(pack, {user.id: user.email}, now)


@router.get("/workspaces/{workspace_id}/evidence-packs")
async def list_evidence_packs(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    if await workspace_role(session, workspace_id, principal) != "admin":
        raise HTTPException(status_code=403, detail="Workspace admin access required")
    packs = (await session.exec(select(EvidencePack).where(EvidencePack.workspace_id == workspace_id)
                                .order_by(EvidencePack.created_at.desc()).limit(100))).all()
    people = await svc.people_by_id(session, [p.requested_by_id for p in packs])
    now = datetime.utcnow()
    return [_pack_view(p, people, now) for p in packs]


async def _load_pack(session, pack_id: int, principal: AuthPrincipal) -> EvidencePack:
    pack = await session.get(EvidencePack, pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Evidence pack not found")
    if await workspace_role(session, pack.workspace_id, principal) != "admin":
        raise HTTPException(status_code=403, detail="Workspace admin access required")
    return pack


@router.get("/evidence-packs/{pack_id}")
async def get_evidence_pack(pack_id: int, session: AsyncSession = Depends(get_session),
                            principal: AuthPrincipal = Depends(get_current_principal)):
    pack = await _load_pack(session, pack_id, principal)
    people = await svc.people_by_id(session, [pack.requested_by_id])
    return _pack_view(pack, people, datetime.utcnow())


@router.post("/evidence-packs/{pack_id}/download")
async def download_evidence_pack(pack_id: int, request: Request,
                                 session: AsyncSession = Depends(get_session),
                                 principal: AuthPrincipal = Depends(get_current_principal)):
    """A presigned object-store URL valid for a few minutes. POST because it
    is audited — a GET would be fetched by prefetchers and link previews."""
    pack = await _load_pack(session, pack_id, principal)
    _human(principal)
    if pack.status != "ready" or not pack.object_key:
        raise HTTPException(status_code=409, detail="This pack is not ready")
    url = object_store.get_presigned_url(pack.object_key, expiration=DOWNLOAD_URL_SECONDS)
    await audit.record(session, entity_type="evidence_pack", entity_id=pack.id, action="download",
                       workspace_id=pack.workspace_id, principal=principal, request=request,
                       changes={"sha256": pack.sha256})
    await session.commit()
    return {"url": url, "expires_in": DOWNLOAD_URL_SECONDS,
            "filename": f"traceiq-evidence-ws{pack.workspace_id}-{pack.id}.zip"}


async def _spool_upload(upload: UploadFile, limit: int):
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            spool.close()
            raise HTTPException(status_code=413, detail=f"Upload is larger than {limit // (1024 * 1024)} MB")
        spool.write(chunk)
    spool.seek(0)
    return spool


@router.post("/evidence-packs/verify")
async def verify_evidence_pack(
    request: Request,
    file: UploadFile = File(...),
    manifest: Optional[UploadFile] = File(default=None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Recompute every file's hash and check the manifest signature.

    The result is only returned to members of the workspace the manifest
    names — a pack's metadata (who generated it, for which period) is that
    workspace's business. Verification itself reveals nothing the uploader
    did not already hold.
    """
    from app.core.secrets import purpose_keys
    from app.services.evidence_pack import (
        KEY_PURPOSE, MAX_MANIFEST_BYTES, MAX_VERIFY_UPLOAD_BYTES, verify_pack)

    override = None
    if manifest is not None:
        raw = await manifest.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise HTTPException(status_code=413, detail="manifest is too large")
        try:
            override = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="manifest is not valid JSON")
    spool = await _spool_upload(file, MAX_VERIFY_UPLOAD_BYTES)
    try:
        report = await asyncio.to_thread(verify_pack, spool, purpose_keys(KEY_PURPOSE), override)
    finally:
        spool.close()
    ws_id = report.get("workspace_id")
    if not isinstance(ws_id, int):
        return {k: report[k] for k in ("verified", "signature_valid", "errors")}
    try:
        await workspace_role(session, ws_id, principal)
    except HTTPException:
        raise HTTPException(status_code=403, detail="This pack belongs to a workspace you are not a member of")
    await audit.record(session, entity_type="evidence_pack", entity_id=report.get("pack_id") or 0,
                       action="verify", workspace_id=ws_id, principal=principal, request=request,
                       changes={"verified": report["verified"],
                                "signature_valid": report["signature_valid"],
                                "mismatched": report["mismatched"][:20],
                                "missing": report["missing"][:20],
                                "unexpected": report["unexpected"][:20]})
    await session.commit()
    return report


# ---------------------------------------------------------------------------
# Restore drills
# ---------------------------------------------------------------------------

async def _require_drill_reporter(session, principal: AuthPrincipal) -> User:
    """Instance admin only — as a person, or through an API key minted by one.

    A drill restores the WHOLE database, so its result is instance-wide
    evidence that shows up in every workspace's controls. Accepting a
    workspace API key on its own would let any tenant's key forge "restore
    drill passed" for every other tenant. A key qualifies only when the
    person behind it is an instance admin right now (revoking the grant
    stops the key) and it is not narrowed to a project.
    """
    from app.api.instance_settings import get_current_instance_admin
    key = principal.api_key
    if key is not None and key.project_id is not None:
        raise HTTPException(status_code=403, detail="Use a workspace-wide key minted by an instance admin")
    await get_current_instance_admin(session=session, current_user=principal.user)
    return principal.user


@router.post("/admin/restore-drills", status_code=201)
async def report_restore_drill(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    from app.services.restore_drills import DrillReport
    user = await _require_drill_reporter(session, principal)
    try:
        report = DrillReport.model_validate(payload)
    except ValidationError as exc:
        # Messages only — FastAPI's default 422 echoes the input back, and the
        # input is exactly what might hold the secret we refused.
        raise HTTPException(status_code=422, detail=[
            {"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()])
    label = (f"api_key:{principal.api_key.prefix}" if principal.api_key is not None else user.email)
    drill = DrillResult(
        ran_at=report.ran_at, status=report.status, dump_name=report.dump_name,
        target=report.target, duration_seconds=report.duration_seconds,
        checks=[c.model_dump() for c in report.checks], reported_by_id=user.id,
        reporter_label=label)
    session.add(drill)
    await session.flush()
    await audit.record(session, entity_type="restore_drill", entity_id=drill.id, action="report",
                       principal=principal, request=request,
                       changes={"status": drill.status, "ran_at": drill.ran_at.isoformat() + "Z",
                                "dump_name": drill.dump_name, "checks": len(drill.checks or []),
                                "failed": [c["check"] for c in drill.checks or [] if c["status"] == "FAIL"]})
    await session.commit()
    return svc.drill_view(drill, full=True)


@router.get("/admin/restore-drills")
async def list_restore_drills_admin(session: AsyncSession = Depends(get_session),
                                    principal: AuthPrincipal = Depends(get_current_principal)):
    from app.api.instance_settings import get_current_instance_admin
    await get_current_instance_admin(session=session, current_user=principal.user)
    drills = (await session.exec(select(DrillResult).order_by(DrillResult.ran_at.desc()).limit(100))).all()
    return [svc.drill_view(d, full=True) for d in drills]


@router.get("/workspaces/{workspace_id}/restore-drills")
async def list_restore_drills(workspace_id: int, session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    await workspace_role(session, workspace_id, principal)
    drills = (await session.exec(select(DrillResult).order_by(DrillResult.ran_at.desc()).limit(20))).all()
    return [svc.drill_view(d, full=False) for d in drills]
