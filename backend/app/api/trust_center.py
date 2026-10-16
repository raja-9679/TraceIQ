"""Trust center (WP13b, roadmap E24, decision C3).

Admin (workspace admin; a project-scoped API key is refused):

    GET/PUT  /api/workspaces/{id}/trust-center                 config + posture preview
    POST     /api/workspaces/{id}/trust-center/verify-audit    walk the audit chain, keep the result
    GET/POST /api/workspaces/{id}/trust-center/incidents       incident posts
    PATCH/DELETE /api/trust-center/incidents/{id}
    POST     /api/trust-center/incidents/{id}/updates          append a timestamped update
    GET/POST /api/workspaces/{id}/trust-center/documents       gated documents (multipart upload)
    DELETE   /api/trust-center/documents/{id}                  also revokes its links
    GET/POST /api/trust-center/documents/{id}/links            one expiring link per recipient

PUBLIC (no auth, rate-limited, reduced payloads; everything 404s while the
trust center is off):

    GET  /api/trust/{slug}                      the page
    POST /api/trust/{slug}/document {token}     -> a 60-second presigned download URL
    POST /api/trust/{slug}/request-access       emails the workspace admins

The slug is an unguessable IDENTIFIER (like status pages), safe in a path
because the page only shows what an admin published. Document links are
CREDENTIALS and follow the share-link rules: ShareLink kind trust_document,
token hashed at rest, link /trust/<slug>#<token> (the fragment never reaches a
server), the page POSTs the token in the body. A link resolves only while its
issuer is still a workspace admin and the document still belongs to the
workspace. Downloads are audited; page views are counted on the row instead
(an anonymous page must not be able to grow the append-only audit table).
"""
from __future__ import annotations

import hashlib
import html
import io
import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.share_links import ShareLinkCreate, _creators, _is_workspace_admin, _issue
from app.api.status_pages import monitor_entries
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import (
    AuditLog, Project, ShareLink, StatusPage, TrustCenter, TrustDocument, TrustIncidentPost, User,
    UserWorkspace, Workspace,
)
from app.services import share_links as sl
from app.services import trust_center as tc
from app.services.access_service import _ROLE_MAP, _workspace_role_level
from app.services.audit import record as audit_record

router = APIRouter()

NOT_FOUND = "Not found"
_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


async def _admin(session: AsyncSession, principal: AuthPrincipal, workspace_id: int) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if principal.api_key is not None and principal.api_key.project_id is not None:
        raise HTTPException(status_code=403, detail="A project-scoped API key cannot manage the trust center")
    if not await _is_workspace_admin(session, principal.user.id, workspace_id):
        raise HTTPException(status_code=403, detail="Workspace admin access required")
    return ws


async def _center(session: AsyncSession, workspace_id: int) -> Optional[TrustCenter]:
    return (await session.exec(select(TrustCenter).where(
        TrustCenter.workspace_id == workspace_id))).first()


async def _workspace_status_pages(session: AsyncSession, workspace_id: int) -> List[tuple]:
    return (await session.exec(
        select(StatusPage, Project.name).join(Project, Project.id == StatusPage.project_id)
        .where(Project.workspace_id == workspace_id).order_by(Project.name))).all()


def _config_out(center: Optional[TrustCenter], workspace_id: int, pages: List[tuple],
                facts: Dict[str, Any]) -> Dict[str, Any]:
    c = center
    return {
        "configured": c is not None,
        "workspace_id": workspace_id,
        "enabled": bool(c and c.enabled),
        "slug": c.slug if c else None,
        "path": f"/trust/{c.slug}" if c else None,
        "title": c.title if c else "Trust center",
        "intro": (c.intro if c else "") or "",
        "contact_email": c.contact_email if c else None,
        "status_page_ids": list(c.status_page_ids or []) if c else [],
        "posture_items": list(c.posture_items or []) if c else [],
        "allow_access_requests": bool(c and c.allow_access_requests),
        "audit_verified_at": c.audit_verified_at if c else None,
        "audit_verified_ok": c.audit_verified_ok if c else None,
        "view_count": c.view_count if c else 0,
        "last_viewed_at": c.last_viewed_at if c else None,
        "available_status_pages": [{"id": p.id, "title": p.title, "project_name": name,
                                    "enabled": p.enabled} for p, name in pages],
        "posture_preview": tc.posture_preview(facts, c.posture_items if c else []),
    }


@router.get("/workspaces/{workspace_id}/trust-center")
async def get_trust_center(workspace_id: int, session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    center = await _center(session, workspace_id)
    facts = await tc.collect_facts(session, workspace_id, center)
    return _config_out(center, workspace_id, await _workspace_status_pages(session, workspace_id), facts)


class TrustCenterUpdate(BaseModel):
    enabled: Optional[bool] = None
    title: Optional[str] = Field(default=None, max_length=120)
    intro: Optional[str] = Field(default=None, max_length=tc.MAX_INTRO)
    contact_email: Optional[str] = Field(default=None, max_length=254)
    status_page_ids: Optional[List[int]] = None
    posture_items: Optional[List[str]] = None
    allow_access_requests: Optional[bool] = None
    regenerate_slug: bool = False


@router.put("/workspaces/{workspace_id}/trust-center")
async def update_trust_center(workspace_id: int, request: Request, body: TrustCenterUpdate = Body(...),
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    center = await _center(session, workspace_id)
    if center is None:
        center = TrustCenter(workspace_id=workspace_id, slug=secrets.token_urlsafe(12))
    changes: Dict[str, Any] = {}
    if body.regenerate_slug:
        center.slug = secrets.token_urlsafe(12)
        changes["slug"] = "regenerated"
    if body.title is not None:
        center.title = body.title.strip()[:120] or "Trust center"
        changes["title"] = center.title
    if body.intro is not None:
        center.intro = body.intro.strip() or None
        changes["intro"] = "updated"
    if body.contact_email is not None:
        email = body.contact_email.strip()
        if email and not _EMAIL.match(email):
            raise HTTPException(status_code=422, detail="contact_email is not a valid address")
        center.contact_email = email or None
        changes["contact_email"] = "updated"
    if body.status_page_ids is not None:
        own = {p.id for p, _n in await _workspace_status_pages(session, workspace_id)}
        if any(i not in own for i in body.status_page_ids):
            raise HTTPException(status_code=404, detail="Status page not found in this workspace")
        center.status_page_ids = sorted(set(body.status_page_ids))
        changes["status_page_ids"] = center.status_page_ids
    if body.posture_items is not None:
        try:
            center.posture_items = tc.clean_posture_keys(body.posture_items)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        changes["posture_items"] = center.posture_items
    if body.allow_access_requests is not None:
        center.allow_access_requests = body.allow_access_requests
        changes["allow_access_requests"] = body.allow_access_requests
    if body.enabled is not None:
        center.enabled = body.enabled
        changes["enabled"] = body.enabled
    center.updated_by_id = principal.user.id
    center.updated_at = datetime.utcnow()
    session.add(center)
    await session.flush()
    await audit_record(session, entity_type="trust_center", entity_id=center.id, action="update",
                       workspace_id=workspace_id, principal=principal, request=request, changes=changes)
    await session.commit()
    await session.refresh(center)
    facts = await tc.collect_facts(session, workspace_id, center)
    return _config_out(center, workspace_id, await _workspace_status_pages(session, workspace_id), facts)


@router.post("/workspaces/{workspace_id}/trust-center/verify-audit")
async def verify_audit_for_trust_center(workspace_id: int, request: Request,
                                        session: AsyncSession = Depends(get_session),
                                        principal: AuthPrincipal = Depends(get_current_principal)):
    """Same full-chain walk as GET /workspaces/{id}/audit/verify; the outcome
    and date are what the "tamper-evident audit log" posture item publishes."""
    from app.services.audit import verify_chain
    await _admin(session, principal, workspace_id)
    center = await _center(session, workspace_id)
    if center is None:
        raise HTTPException(status_code=404, detail="Save the trust center settings first")
    rows = (await session.exec(select(AuditLog).order_by(AuditLog.id))).all()
    ok, _idx = verify_chain(rows)
    center.audit_verified_at = datetime.utcnow()
    center.audit_verified_ok = bool(ok)
    session.add(center)
    await audit_record(session, entity_type="trust_center", entity_id=center.id, action="verify_audit",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes={"verified": bool(ok), "rows_checked": len(rows)})
    await session.commit()
    return {"verified": bool(ok), "rows_checked": len(rows), "verified_at": center.audit_verified_at}


# ---------------------------------------------------------------------------
# Incident posts
# ---------------------------------------------------------------------------


class IncidentPostCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    status: str = "investigating"
    components: Optional[List[str]] = None
    body: Optional[str] = Field(default=None, max_length=tc.MAX_BODY)
    published: bool = False


class IncidentPostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    components: Optional[List[str]] = None
    published: Optional[bool] = None


class IncidentUpdateCreate(BaseModel):
    status: str
    body: str = Field(min_length=1, max_length=tc.MAX_BODY)


def _post_out(p: TrustIncidentPost) -> Dict[str, Any]:
    return {"id": p.id, "workspace_id": p.workspace_id, "title": p.title, "status": p.status,
            "components": p.components or [], "updates": p.updates or [], "published": p.published,
            "published_at": p.published_at, "resolved_at": p.resolved_at,
            "created_at": p.created_at, "updated_at": p.updated_at}


def _check_status(status: str) -> str:
    if status not in tc.INCIDENT_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(tc.INCIDENT_STATUSES)}")
    return status


def _set_status(post: TrustIncidentPost, status: str, now: datetime) -> None:
    post.status = status
    post.resolved_at = now if status == "resolved" else None


@router.get("/workspaces/{workspace_id}/trust-center/incidents")
async def list_incident_posts(workspace_id: int, session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    posts = (await session.exec(select(TrustIncidentPost).where(
        TrustIncidentPost.workspace_id == workspace_id)
        .order_by(TrustIncidentPost.created_at.desc()))).all()
    return [_post_out(p) for p in posts]


@router.post("/workspaces/{workspace_id}/trust-center/incidents")
async def create_incident_post(workspace_id: int, request: Request, body: IncidentPostCreate = Body(...),
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    now = datetime.utcnow()
    try:
        components = tc.clean_components(body.components)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    post = TrustIncidentPost(workspace_id=workspace_id, title=body.title.strip(), components=components,
                             created_by_id=principal.user.id, created_at=now, updated_at=now,
                             published=body.published, published_at=now if body.published else None)
    _set_status(post, _check_status(body.status), now)
    if body.body and body.body.strip():
        post.updates = [{"at": now.isoformat(), "status": post.status, "body": body.body.strip()}]
    session.add(post)
    await session.flush()
    await audit_record(session, entity_type="trust_incident", entity_id=post.id, action="create",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes={"title": post.title, "status": post.status, "published": post.published})
    await session.commit()
    await session.refresh(post)
    return _post_out(post)


async def _post(session: AsyncSession, principal: AuthPrincipal, post_id: int) -> TrustIncidentPost:
    post = await session.get(TrustIncidentPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Incident post not found")
    try:
        await _admin(session, principal, post.workspace_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Incident post not found")
    return post


@router.patch("/trust-center/incidents/{post_id}")
async def update_incident_post(post_id: int, request: Request, body: IncidentPostUpdate = Body(...),
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    post = await _post(session, principal, post_id)
    now = datetime.utcnow()
    changes: Dict[str, Any] = {}
    if body.title is not None:
        post.title = body.title.strip()
        changes["title"] = post.title
    if body.components is not None:
        try:
            post.components = tc.clean_components(body.components)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        changes["components"] = post.components
    if body.published is not None:
        if body.published and not post.published:
            post.published_at = post.published_at or now
        post.published = body.published
        changes["published"] = body.published
    post.updated_at = now
    session.add(post)
    await audit_record(session, entity_type="trust_incident", entity_id=post.id, action="update",
                       workspace_id=post.workspace_id, principal=principal, request=request, changes=changes)
    await session.commit()
    await session.refresh(post)
    return _post_out(post)


@router.post("/trust-center/incidents/{post_id}/updates")
async def add_incident_update(post_id: int, request: Request, body: IncidentUpdateCreate = Body(...),
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    post = await _post(session, principal, post_id)
    now = datetime.utcnow()
    updates = list(post.updates or [])
    if len(updates) >= tc.MAX_UPDATES:
        raise HTTPException(status_code=422, detail=f"at most {tc.MAX_UPDATES} updates per post")
    _set_status(post, _check_status(body.status), now)
    updates.append({"at": now.isoformat(), "status": post.status, "body": body.body.strip()})
    post.updates = updates      # a new list, so the JSON column is marked dirty
    post.updated_at = now
    session.add(post)
    await audit_record(session, entity_type="trust_incident", entity_id=post.id, action="add_update",
                       workspace_id=post.workspace_id, principal=principal, request=request,
                       changes={"status": post.status})
    await session.commit()
    await session.refresh(post)
    return _post_out(post)


@router.delete("/trust-center/incidents/{post_id}")
async def delete_incident_post(post_id: int, request: Request, session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    post = await _post(session, principal, post_id)
    await audit_record(session, entity_type="trust_incident", entity_id=post.id, action="delete",
                       workspace_id=post.workspace_id, principal=principal, request=request,
                       changes={"title": post.title})
    await session.delete(post)
    await session.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Gated documents
# ---------------------------------------------------------------------------


def _doc_out(d: TrustDocument) -> Dict[str, Any]:
    return {"id": d.id, "workspace_id": d.workspace_id, "title": d.title, "description": d.description,
            "filename": d.filename, "content_type": d.content_type, "size_bytes": d.size_bytes,
            "sha256": d.sha256, "listed": d.listed, "created_at": d.created_at}


@router.get("/workspaces/{workspace_id}/trust-center/documents")
async def list_documents(workspace_id: int, session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    docs = (await session.exec(select(TrustDocument).where(TrustDocument.workspace_id == workspace_id)
                               .order_by(TrustDocument.created_at.desc()))).all()
    return [_doc_out(d) for d in docs]


@router.post("/workspaces/{workspace_id}/trust-center/documents")
async def upload_document(workspace_id: int, request: Request, file: UploadFile = File(...),
                          title: str = Form(...), description: Optional[str] = Form(None),
                          listed: bool = Form(True),
                          session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    await _admin(session, principal, workspace_id)
    title = (title or "").strip()[:200]
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    data = await file.read(tc.MAX_DOCUMENT_BYTES + 1)
    if len(data) > tc.MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"Documents can be at most {tc.MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    filename = tc.safe_filename(file.filename)
    content_type = tc.sniff_document(data[:4096], filename)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Accepted: PDF, PNG, JPEG, DOCX, XLSX, PPTX, TXT or MD "
                                                    "(checked from the file's contents)")
    key = f"trust/{workspace_id}/{uuid.uuid4().hex}"
    from app.core.storage import object_store
    try:
        object_store.upload_fileobj(io.BytesIO(data), key, content_type=content_type)
    except Exception:  # noqa: BLE001 - the store's error text can carry its endpoint
        raise HTTPException(status_code=502, detail="Upload to object storage failed")
    doc = TrustDocument(workspace_id=workspace_id, title=title,
                        description=(description or "").strip()[:500] or None, filename=filename,
                        content_type=content_type, size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), object_key=key, listed=listed,
                        uploaded_by_id=principal.user.id)
    session.add(doc)
    await session.flush()
    await audit_record(session, entity_type="trust_document", entity_id=doc.id, action="upload",
                       workspace_id=workspace_id, principal=principal, request=request,
                       changes={"title": title, "content_type": content_type, "size_bytes": len(data),
                                "sha256": doc.sha256})
    await session.commit()
    await session.refresh(doc)
    return _doc_out(doc)


async def _document(session: AsyncSession, principal: AuthPrincipal, doc_id: int) -> TrustDocument:
    doc = await session.get(TrustDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        await _admin(session, principal, doc.workspace_id)
    except HTTPException:
        # Another workspace's document: do not confirm that the id exists.
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/trust-center/documents/{doc_id}")
async def delete_document(doc_id: int, request: Request, session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    doc = await _document(session, principal, doc_id)
    now = datetime.utcnow()
    revoked = await session.exec(text(
        "UPDATE sharelink SET revoked_at = :now WHERE kind = 'trust_document' AND target_id = :doc "
        "AND workspace_id = :ws AND revoked_at IS NULL"),
        params={"now": now, "doc": doc.id, "ws": doc.workspace_id})
    await audit_record(session, entity_type="trust_document", entity_id=doc.id, action="delete",
                       workspace_id=doc.workspace_id, principal=principal, request=request,
                       changes={"title": doc.title, "links_revoked": getattr(revoked, "rowcount", 0) or 0})
    key = doc.object_key
    await session.delete(doc)
    await session.commit()
    from app.core.storage import object_store
    try:
        object_store.delete_object(key)
    except Exception:  # noqa: BLE001 - the row is gone; purge sweeps the prefix later
        pass
    return {"deleted": True}


class DocumentLinkCreate(BaseModel):
    recipient: str = Field(min_length=1, max_length=120)
    expires_in_days: int = Field(default=sl.DEFAULT_DAYS)


@router.get("/trust-center/documents/{doc_id}/links")
async def list_document_links(doc_id: int, session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    doc = await _document(session, principal, doc_id)
    links = (await session.exec(select(ShareLink).where(
        ShareLink.kind == "trust_document", ShareLink.workspace_id == doc.workspace_id,
        ShareLink.target_id == doc.id).order_by(ShareLink.created_at.desc()))).all()
    who = await _creators(session, links)
    return [sl.describe(l, who.get(l.created_by_id)) for l in links]


@router.post("/trust-center/documents/{doc_id}/links")
async def create_document_link(doc_id: int, request: Request, body: DocumentLinkCreate = Body(...),
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    doc = await _document(session, principal, doc_id)
    center = await _center(session, doc.workspace_id)
    if center is None:
        raise HTTPException(status_code=409, detail="Save the trust center settings first")
    req = ShareLinkCreate(kind="trust_document", expires_in_days=body.expires_in_days,
                          label=body.recipient.strip())
    return await _issue(session, request, principal, doc.workspace_id, None, req, doc.id,
                        path=f"/trust/{center.slug}")


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


async def _public_center(session: AsyncSession, slug: str) -> TrustCenter:
    if not slug or len(slug) > 64:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    center = (await session.exec(select(TrustCenter).where(TrustCenter.slug == slug))).first()
    if not center or not center.enabled:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return center


async def build_public_page(session: AsyncSession, center: TrustCenter) -> Dict[str, Any]:
    monitors: List[Dict[str, Any]] = []
    picked = set(center.status_page_ids or [])
    if picked:
        # Re-filtered at every view: a page moved out of the workspace, or
        # switched off, drops out without anyone editing the trust center.
        for page, _name in await _workspace_status_pages(session, center.workspace_id):
            if page.id in picked and page.enabled:
                monitors.extend(await monitor_entries(session, page.project_id))
    posts = (await session.exec(select(TrustIncidentPost).where(
        TrustIncidentPost.workspace_id == center.workspace_id,
        TrustIncidentPost.published == True)  # noqa: E712
        .order_by(TrustIncidentPost.created_at.desc()).limit(20))).all()
    docs = (await session.exec(select(TrustDocument).where(
        TrustDocument.workspace_id == center.workspace_id,
        TrustDocument.listed == True)  # noqa: E712
        .order_by(TrustDocument.title))).all()
    posture: List[Dict[str, str]] = []
    if center.posture_items:
        posture = tc.derive_posture(await tc.collect_facts(session, center.workspace_id, center),
                                    center.posture_items)
    return tc.public_page(center, monitors, posts, posture, docs)


@router.get("/trust/{slug}")
@limiter.limit("60/minute")
async def public_trust_page(request: Request, slug: str, session: AsyncSession = Depends(get_session)):
    """PUBLIC - no auth."""
    center = await _public_center(session, slug)
    payload = await build_public_page(session, center)
    center.view_count = (center.view_count or 0) + 1
    center.last_viewed_at = datetime.utcnow()
    session.add(center)
    await session.commit()
    return payload


async def resolve_document_link(session: AsyncSession, center: TrustCenter,
                                token: Any) -> Optional[tuple]:
    """(link, document) for a live link to a document of THIS trust center's
    workspace, issued by someone who is still its admin; else None."""
    if not isinstance(token, str) or not token or len(token) > 128:
        return None
    link = (await session.exec(select(ShareLink).where(
        ShareLink.token_hash == sl.hash_token(token)))).first()
    if (not link or link.kind != "trust_document" or link.workspace_id != center.workspace_id
            or sl.state(link) != "active"):
        return None
    doc = await session.get(TrustDocument, link.target_id) if link.target_id else None
    if not doc or doc.workspace_id != link.workspace_id:
        return None
    creator = await session.get(User, link.created_by_id) if link.created_by_id else None
    if not creator or not getattr(creator, "is_active", True):
        return None
    if not await _is_workspace_admin(session, creator.id, link.workspace_id):
        return None
    return link, doc


@router.post("/trust/{slug}/document")
@limiter.limit("20/minute")
async def public_document_download(request: Request, slug: str, body: Dict[str, Any] = Body(...),
                                   session: AsyncSession = Depends(get_session)):
    """PUBLIC - the token arrives in the body (never a URL), is hashed before
    the lookup and is never logged or audited."""
    center = await _public_center(session, slug)
    found = await resolve_document_link(session, center, body.get("token") if isinstance(body, dict) else None)
    if not found:
        raise HTTPException(status_code=404, detail="This document link is not available")
    link, doc = found
    from app.core.storage import object_store
    try:
        url = object_store.get_presigned_download_url(doc.object_key, doc.filename, doc.content_type, 60)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="The document store is unavailable; try again shortly")
    now = datetime.utcnow()
    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = now
    session.add(link)
    await audit_record(session, entity_type="share_link", entity_id=link.id, action="download",
                       workspace_id=link.workspace_id, request=request,
                       actor_type="public", actor_label="trust document recipient",
                       changes={"kind": link.kind, "document_id": doc.id, "view_count": link.view_count})
    await session.commit()
    return {"title": doc.title, "filename": doc.filename, "content_type": doc.content_type,
            "size_bytes": doc.size_bytes, "sha256": doc.sha256, "url": url, "url_expires_in": 60,
            "link_expires_at": link.expires_at.isoformat()}


class AccessRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    company: Optional[str] = Field(default=None, max_length=120)
    message: Optional[str] = Field(default=None, max_length=2000)
    document_ids: Optional[List[int]] = None


def _one_line(v: Optional[str]) -> str:
    return re.sub(r"[\r\n\t]+", " ", v or "").strip()


async def workspace_admin_emails(session: AsyncSession, workspace_id: int) -> List[str]:
    out = []
    for uw in (await session.exec(select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id))).all():
        if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
            u = await session.get(User, uw.user_id)
            if u and getattr(u, "is_active", True) and u.email and u.email not in out:
                out.append(u.email)
    return out


@router.post("/trust/{slug}/request-access")
@limiter.limit("5/hour")
async def public_request_access(request: Request, slug: str, body: AccessRequest = Body(...),
                                session: AsyncSession = Depends(get_session)):
    """PUBLIC - emails this workspace's admins (nobody else) so one of them
    can issue a document link. Says nothing about who the admins are."""
    center = await _public_center(session, slug)
    if not center.allow_access_requests:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    email = _one_line(body.email)
    if not _EMAIL.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    docs = []
    if body.document_ids:
        docs = (await session.exec(select(TrustDocument).where(
            TrustDocument.workspace_id == center.workspace_id, TrustDocument.listed == True,  # noqa: E712
            TrustDocument.id.in_(body.document_ids[:20])))).all()
    name, company, message = _one_line(body.name), _one_line(body.company), (body.message or "").strip()
    titles = [d.title for d in docs] or ["(not specified)"]
    subject = f"Trust center access request from {name}"[:150]
    text_body = (f"{name} <{email}>{' (' + company + ')' if company else ''} asked for access on your "
                 f"trust center \"{center.title}\".\n\nDocuments: {', '.join(titles)}\n\n"
                 f"Message:\n{message or '(none)'}\n\nIssue a link from Trust center settings if you agree. "
                 f"The address was typed by the requester and is not verified.")
    e = html.escape
    html_body = (f"<p><strong>{e(name)}</strong> &lt;{e(email)}&gt;{' (' + e(company) + ')' if company else ''} "
                 f"asked for access on your trust center &ldquo;{e(center.title)}&rdquo;.</p>"
                 f"<p>Documents: {e(', '.join(titles))}</p>"
                 f"<p>Message:</p><pre style=\"white-space:pre-wrap\">{e(message or '(none)')}</pre>"
                 f"<p>Issue a link from Trust center settings if you agree. The address was typed by "
                 f"the requester and is not verified.</p>")
    recipients = await workspace_admin_emails(session, center.workspace_id)
    from app.tasks.notification_tasks import send_account_email
    for to in recipients:
        send_account_email.delay(to, subject, html_body, text_body)
    # The requester's address is personal data; the permanent audit row
    # records that a request happened, not who made it.
    await audit_record(session, entity_type="trust_center", entity_id=center.id, action="access_request",
                       workspace_id=center.workspace_id, request=request,
                       actor_type="public", actor_label="trust center visitor",
                       changes={"document_ids": [d.id for d in docs], "admins_notified": len(recipients)})
    await session.commit()
    return {"received": True}
