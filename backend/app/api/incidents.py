"""Incidents → regression tests, recurrence and escaped-defect tracking (WP6).

Surface:
    POST   /api/projects/{id}/incidents/webhook          inbound, HMAC (no user auth)
    GET    /api/projects/{id}/incidents                  ?status=&escaped=
    POST   /api/projects/{id}/incidents                  manual create (editor)
    GET    /api/projects/{id}/incidents/escaped-report   ?days=&period=month|week
    GET    /api/projects/{id}/incidents/webhook-config   (editor; never the secret)
    PUT    /api/projects/{id}/incidents/webhook-config   {default_suite_id?, enabled?} (admin)
    POST   /api/projects/{id}/incidents/webhook-config/secret   create/rotate, secret shown ONCE (admin)
    DELETE /api/projects/{id}/incidents/webhook-config   (admin)
    GET    /api/incidents/{id}
    PATCH  /api/incidents/{id}                           status/severity/resolved_at/...
    POST   /api/incidents/{id}/escaped                   {escaped, case_id? | no_test_existed?, note?}
    POST   /api/incidents/{id}/propose-regression        {test_suite_id?}
    DELETE /api/incidents/{id}

On create an incident is fingerprinted (recurrence), matched against failure
clusters and cases, and a regression-test CaseProposal is queued (never a
direct case). The webhook path does the proposal after responding — paging
tools time out quickly and an LLM call can take seconds.

The webhook answers 404 both for "no such project" and "no webhook configured"
and 401 for a bad signature *only once a webhook exists*, so it can't be used
to enumerate projects.
"""
from __future__ import annotations

import json
import secrets as pysecrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models import (
    CaseProposal, FailureCluster, Incident, IncidentWebhook, Project, TestCase, TestSuite,
)
from app.services import generation_proposals, incidents as incident_svc
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

MAX_WEBHOOK_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        detail = {"viewer": "Access denied", "editor": "Editor access required",
                  "admin": "Project admin required"}[min_role]
        raise HTTPException(status_code=403, detail=detail)
    return project


async def _incident(incident_id: int, principal: AuthPrincipal, session: AsyncSession,
                    min_role: str = "viewer") -> Tuple[Incident, Project]:
    inc = await session.get(Incident, incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    project = await _project(inc.project_id, principal, session, min_role)
    return inc, project


async def _case_in_project(case_id: Optional[int], project_id: int, session: AsyncSession) -> Optional[TestCase]:
    if case_id is None:
        return None
    case = await session.get(TestCase, case_id)
    if not case or case.project_id != project_id:
        raise HTTPException(status_code=404, detail="Test case not found in this project")
    return case


async def _suite_in_project(suite_id: Optional[int], project_id: int, session: AsyncSession) -> Optional[TestSuite]:
    if suite_id is None:
        return None
    suite = await session.get(TestSuite, suite_id)
    if not suite or suite.project_id != project_id:
        raise HTTPException(status_code=404, detail="Test suite not found in this project")
    return suite


# ---------------------------------------------------------------------------
# Ingestion (shared by the webhook and the manual form)
# ---------------------------------------------------------------------------

async def ingest(session: AsyncSession, project: Project, data: Dict[str, Any], *,
                 user_id: Optional[int]) -> Tuple[Incident, bool]:
    """Create the incident, or update the one with the same external id (a
    paging tool sends triggered → acknowledged → resolved for one incident).
    Returns (incident, created). Flushes; caller commits."""
    source = data.get("source") or "manual"
    ext = data.get("external_id")
    if ext:
        existing = (await session.exec(select(Incident).where(
            Incident.project_id == project.id, Incident.source == source,
            Incident.external_id == ext))).first()
        if existing:
            _apply_update(existing, data)
            session.add(existing)
            await session.flush()
            return existing, False

    title = data["title"]
    inc = Incident(
        project_id=project.id, source=source, external_id=ext, title=title,
        description=data.get("description"),
        severity=data.get("severity") or "unknown",
        status=data.get("status") or "open",
        service=data.get("service"), url=data.get("url"), affected_url=data.get("affected_url"),
        started_at=data.get("started_at") or datetime.utcnow(),
        resolved_at=data.get("resolved_at"),
        git_commit=data.get("git_commit"), git_branch=data.get("git_branch"),
        release=data.get("release"),
        fingerprint=incident_svc.fingerprint(data.get("service"), title),
        created_by_id=user_id,
    )
    if inc.status == "resolved" and not inc.resolved_at:
        inc.resolved_at = datetime.utcnow()

    previous = (await session.exec(select(Incident).where(
        Incident.project_id == project.id, Incident.fingerprint == inc.fingerprint)
        .order_by(Incident.started_at.desc(), Incident.id.desc()))).first()
    if previous:
        inc.previous_incident_id = previous.id
        inc.recurrence_count = (previous.recurrence_count or 0) + 1

    clusters = (await session.exec(
        select(FailureCluster.id, FailureCluster.title, FailureCluster.sample_error)
        .where(FailureCluster.project_id == project.id)
        .order_by(FailureCluster.last_seen_at.desc()).limit(300))).all()
    cases = (await session.exec(
        select(TestCase.id, TestCase.name, TestCase.steps)
        .where(TestCase.project_id == project.id).order_by(TestCase.id.desc()).limit(3000))).all()
    match = incident_svc.match_existing(title, inc.affected_url, clusters, cases)
    inc.matched_cluster_id = match["cluster_id"]
    inc.matched_case_id = match["case_id"]
    inc.match_reason = match["reason"]

    session.add(inc)
    await session.flush()
    return inc, True


def _apply_update(inc: Incident, data: Dict[str, Any]) -> None:
    new_status = data.get("status")
    if new_status in incident_svc.STATUSES:
        inc.status = new_status
        if new_status == "resolved":
            inc.resolved_at = data.get("resolved_at") or inc.resolved_at or datetime.utcnow()
    if data.get("severity") and data["severity"] != "unknown":
        inc.severity = data["severity"]
    for key in ("description", "service", "url", "affected_url", "git_commit", "git_branch", "release"):
        if data.get(key) and not getattr(inc, key):
            setattr(inc, key, data[key])
    inc.updated_at = datetime.utcnow()


async def propose_regression(session: AsyncSession, inc: Incident, project: Project, *,
                             suite_id: Optional[int], user_id: Optional[int],
                             principal: Optional[AuthPrincipal] = None) -> Dict[str, Any]:
    """Queue a regression-test proposal for `inc`. Suite: the one asked for,
    else the matched case's suite, else the project's first suite."""
    suite = await _suite_in_project(suite_id, project.id, session) if suite_id else None
    if suite is None and inc.matched_case_id:
        case = await session.get(TestCase, inc.matched_case_id)
        if case and case.project_id == project.id:
            suite = await session.get(TestSuite, case.test_suite_id)
    if suite is None:
        suite = (await session.exec(select(TestSuite).where(TestSuite.project_id == project.id)
                                    .order_by(TestSuite.id))).first()
    if suite is None:
        inc.regression_status = "no_suite"
        session.add(inc)
        return {"status": "no_suite"}

    proposal, why_not = await generation_proposals.propose(
        session, project=project, suite_id=suite.id,
        name=generation_proposals.case_name(f"Incident #{inc.id}:", inc.title),
        description=incident_svc.regression_description(inc),
        skeleton_text=f"Verify the incident does not recur: {inc.title}",
        target_url=inc.affected_url, origin={"incident_id": inc.id},
        tags=["incident", f"incident:{inc.id}"],
        rationale=f"Regression test for incident #{inc.id}: {inc.title}",
        user_id=user_id, feature="incident_regression",
        agent_id=principal.agent_id if principal else None,
        agent_session_id=principal.agent_session_id if principal else None)
    inc.regression_proposal_id = proposal.id
    if proposal.status != "accepted":   # auto-apply already set "accepted" via the link hook
        inc.regression_status = ("needs_authoring" if (proposal.payload or {}).get("needs_authoring")
                                 else "proposed")
    inc.updated_at = datetime.utcnow()
    session.add(inc)
    return {"status": inc.regression_status, "proposal_id": proposal.id,
            "needs_authoring_reason": why_not}


async def _propose_in_background(incident_id: int, suite_id: Optional[int]) -> None:
    from app.core.database import async_session_factory
    try:
        async with async_session_factory() as session:
            inc = await session.get(Incident, incident_id)
            project = await session.get(Project, inc.project_id) if inc else None
            if not inc or not project or inc.regression_proposal_id:
                return
            await propose_regression(session, inc, project, suite_id=suite_id, user_id=None)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — the incident is recorded; the proposal can be retried
        print(f"[incidents] regression proposal for incident {incident_id} failed: {exc}")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

async def _serialize_many(session: AsyncSession, rows: List[Incident]) -> List[Dict[str, Any]]:
    case_ids = {i for r in rows for i in (r.matched_case_id, r.regression_case_id, r.escaped_case_id) if i}
    names = {}
    if case_ids:
        names = dict((await session.exec(select(TestCase.id, TestCase.name)
                                          .where(TestCase.id.in_(list(case_ids))))).all())
    pids = [r.regression_proposal_id for r in rows if r.regression_proposal_id]
    pstatus = {}
    if pids:
        pstatus = dict((await session.exec(select(CaseProposal.id, CaseProposal.status)
                                           .where(CaseProposal.id.in_(pids)))).all())
    cids = [r.matched_cluster_id for r in rows if r.matched_cluster_id]
    ctitles = {}
    if cids:
        ctitles = dict((await session.exec(select(FailureCluster.id, FailureCluster.title)
                                           .where(FailureCluster.id.in_(cids)))).all())
    out = []
    for r in rows:
        d = r.model_dump()
        d["matched_case_name"] = names.get(r.matched_case_id)
        d["regression_case_name"] = names.get(r.regression_case_id)
        d["escaped_case_name"] = names.get(r.escaped_case_id)
        d["regression_proposal_status"] = pstatus.get(r.regression_proposal_id)
        d["matched_cluster_title"] = ctitles.get(r.matched_cluster_id)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Inbound webhook
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/incidents/webhook", status_code=202)
async def receive_incident_webhook(project_id: int, request: Request, background: BackgroundTasks,
                                   source: Optional[str] = Query(None, pattern="^(generic|pagerduty|opsgenie)$"),
                                   session: AsyncSession = Depends(get_session)):
    hook = (await session.exec(select(IncidentWebhook).where(
        IncidentWebhook.project_id == project_id))).first()
    if not hook or not hook.enabled:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    try:
        secret = decrypt_secret(hook.secret_encrypted)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Webhook secret unreadable; rotate it")
    if not incident_svc.verify_request(secret, body, dict(request.headers)):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    # PagerDuty sends a ping on subscription create, and other non-incident
    # event types: acknowledge them so the sender doesn't retry.
    event = payload.get("event") if isinstance(payload, dict) else None
    if isinstance(event, dict) and not str(event.get("event_type") or "").startswith("incident.") \
            and (source in (None, "pagerduty")):
        return {"status": "ignored", "reason": f"event_type {event.get('event_type')!r}"}

    try:
        data = incident_svc.normalise_payload(payload, source)
    except incident_svc.IncidentPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    project = await session.get(Project, project_id)
    try:
        inc, created = await ingest(session, project, data, user_id=None)
    except IntegrityError:
        # Two deliveries for the same new external incident raced; the other
        # one created it, so this one is an update.
        await session.rollback()
        project = await session.get(Project, project_id)
        inc, created = await ingest(session, project, data, user_id=None)
    hook = await session.get(IncidentWebhook, hook.id)
    hook.last_received_at = datetime.utcnow()
    session.add(hook)
    if created:
        await audit_record(session, entity_type="incident", entity_id=inc.id, action="create",
                           workspace_id=project.workspace_id, actor_type="webhook",
                           actor_label=data.get("source"),
                           changes={"title": inc.title, "severity": inc.severity,
                                    "recurrence_count": inc.recurrence_count})
    await session.commit()
    if created:
        background.add_task(_propose_in_background, inc.id, hook.default_suite_id)
    return {"incident_id": inc.id, "created": created, "status": inc.status,
            "recurrence_count": inc.recurrence_count,
            "previous_incident_id": inc.previous_incident_id}


# ---------------------------------------------------------------------------
# Webhook configuration
# ---------------------------------------------------------------------------

def _hook_view(project_id: int, hook: Optional[IncidentWebhook]) -> Dict[str, Any]:
    return {
        "configured": hook is not None,
        "enabled": bool(hook and hook.enabled),
        "default_suite_id": hook.default_suite_id if hook else None,
        "created_at": hook.created_at if hook else None,
        "rotated_at": hook.rotated_at if hook else None,
        "last_received_at": hook.last_received_at if hook else None,
        "path": f"/api/projects/{project_id}/incidents/webhook",
        "signature_headers": ["X-TraceIQ-Signature: sha256=<hex hmac of body>",
                              "X-PagerDuty-Signature: v1=<hex hmac of body>",
                              "X-TraceIQ-Token: <secret> (Opsgenie; static header)"],
    }


async def _hook(session: AsyncSession, project_id: int) -> Optional[IncidentWebhook]:
    return (await session.exec(select(IncidentWebhook).where(
        IncidentWebhook.project_id == project_id))).first()


@router.get("/projects/{project_id}/incidents/webhook-config")
async def get_webhook_config(project_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                             session: AsyncSession = Depends(get_session)):
    await _project(project_id, principal, session, "editor")
    return _hook_view(project_id, await _hook(session, project_id))


class WebhookConfigBody(BaseModel):
    default_suite_id: Optional[int] = None
    enabled: Optional[bool] = None


@router.put("/projects/{project_id}/incidents/webhook-config")
async def update_webhook_config(project_id: int, body: WebhookConfigBody,
                                principal: AuthPrincipal = Depends(get_current_principal),
                                session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "admin")
    hook = await _hook(session, project_id)
    if not hook:
        raise HTTPException(status_code=404, detail="Create the webhook secret first")
    data = body.model_dump(exclude_unset=True)
    if "default_suite_id" in data:
        await _suite_in_project(data["default_suite_id"], project_id, session)
        hook.default_suite_id = data["default_suite_id"]
    if data.get("enabled") is not None:
        hook.enabled = bool(data["enabled"])
    session.add(hook)
    await audit_record(session, entity_type="incident_webhook", entity_id=hook.id, action="update",
                       principal=principal, workspace_id=project.workspace_id, changes=data)
    await session.commit()
    return _hook_view(project_id, hook)


@router.post("/projects/{project_id}/incidents/webhook-config/secret")
async def rotate_webhook_secret(project_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                                session: AsyncSession = Depends(get_session)):
    """Create the webhook, or rotate its secret. The secret is in this response
    and nowhere else, ever — the old one stops working immediately."""
    if principal.is_api_caller:
        # A key that can mint the credential for the inbound channel could
        # then feed the proposal queue as "PagerDuty".
        raise HTTPException(status_code=403, detail="API keys cannot manage webhook secrets")
    project = await _project(project_id, principal, session, "admin")
    secret = "tiqwh_" + pysecrets.token_urlsafe(32)
    hook = await _hook(session, project_id)
    action = "rotate_secret"
    if hook is None:
        hook = IncidentWebhook(project_id=project_id, secret_encrypted=encrypt_secret(secret),
                               created_by_id=principal.user.id)
        action = "create"
    else:
        hook.secret_encrypted = encrypt_secret(secret)
        hook.rotated_at = datetime.utcnow()
    session.add(hook)
    await session.flush()
    await audit_record(session, entity_type="incident_webhook", entity_id=hook.id, action=action,
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"project_id": project_id})
    await session.commit()
    return {**_hook_view(project_id, hook), "secret": secret}


@router.delete("/projects/{project_id}/incidents/webhook-config")
async def delete_webhook_config(project_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                                session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "admin")
    hook = await _hook(session, project_id)
    if not hook:
        raise HTTPException(status_code=404, detail="No webhook configured")
    await audit_record(session, entity_type="incident_webhook", entity_id=hook.id, action="delete",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"project_id": project_id})
    await session.delete(hook)
    await session.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Incidents CRUD
# ---------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    title: str = PydField(min_length=1, max_length=500)
    description: Optional[str] = PydField(default=None, max_length=5000)
    severity: Optional[str] = None
    status: Optional[str] = None
    service: Optional[str] = PydField(default=None, max_length=200)
    url: Optional[str] = PydField(default=None, max_length=2000)
    affected_url: Optional[str] = PydField(default=None, max_length=2000)
    started_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    git_commit: Optional[str] = PydField(default=None, max_length=80)
    git_branch: Optional[str] = PydField(default=None, max_length=200)
    release: Optional[str] = PydField(default=None, max_length=120)
    test_suite_id: Optional[int] = None
    propose_regression: bool = True


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is not None:
        return (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


@router.post("/projects/{project_id}/incidents")
async def create_incident(project_id: int, body: IncidentCreate,
                          principal: AuthPrincipal = Depends(get_current_principal),
                          session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "editor")
    if body.test_suite_id is not None:
        await _suite_in_project(body.test_suite_id, project_id, session)
    from app.services.tracker_import import safe_link
    data = {
        "source": "manual", "title": body.title.strip(), "description": body.description,
        "severity": incident_svc.normalise_severity(body.severity),
        "status": incident_svc.normalise_status(body.status),
        "service": (body.service or "").strip() or None,
        "url": safe_link(body.url), "affected_url": safe_link(body.affected_url),
        "started_at": _naive(body.started_at), "resolved_at": _naive(body.resolved_at),
        "git_commit": body.git_commit, "git_branch": body.git_branch, "release": body.release,
    }
    inc, _ = await ingest(session, project, data, user_id=principal.user.id)
    await audit_record(session, entity_type="incident", entity_id=inc.id, action="create",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"title": inc.title, "severity": inc.severity,
                                "recurrence_count": inc.recurrence_count})
    regression = None
    if body.propose_regression:
        regression = await propose_regression(session, inc, project, suite_id=body.test_suite_id,
                                               user_id=principal.user.id, principal=principal)
    await session.commit()
    await session.refresh(inc)
    return {**(await _serialize_many(session, [inc]))[0], "regression": regression}


@router.get("/projects/{project_id}/incidents")
async def list_incidents(project_id: int, status: Optional[str] = None,
                         escaped: Optional[bool] = None, limit: int = Query(200, ge=1, le=1000),
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)):
    await _project(project_id, principal, session)
    q = select(Incident).where(Incident.project_id == project_id)
    if status:
        q = q.where(Incident.status == status)
    if escaped is not None:
        q = q.where(Incident.escaped_defect == escaped)
    rows = (await session.exec(q.order_by(Incident.started_at.desc(), Incident.id.desc())
                               .limit(limit))).all()
    return await _serialize_many(session, list(rows))


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                       session: AsyncSession = Depends(get_session)):
    inc, _ = await _incident(incident_id, principal, session)
    out = (await _serialize_many(session, [inc]))[0]
    history = (await session.exec(select(Incident).where(
        Incident.project_id == inc.project_id, Incident.fingerprint == inc.fingerprint,
        Incident.id != inc.id).order_by(Incident.started_at.desc()).limit(20))).all()
    out["recurrences"] = [{"id": h.id, "title": h.title, "started_at": h.started_at,
                           "status": h.status} for h in history]
    return out


class IncidentUpdate(BaseModel):
    title: Optional[str] = PydField(default=None, min_length=1, max_length=500)
    status: Optional[str] = None
    severity: Optional[str] = None
    service: Optional[str] = PydField(default=None, max_length=200)
    resolved_at: Optional[datetime] = None
    git_commit: Optional[str] = PydField(default=None, max_length=80)
    git_branch: Optional[str] = PydField(default=None, max_length=200)
    release: Optional[str] = PydField(default=None, max_length=120)


@router.patch("/incidents/{incident_id}")
async def update_incident(incident_id: int, body: IncidentUpdate,
                          principal: AuthPrincipal = Depends(get_current_principal),
                          session: AsyncSession = Depends(get_session)):
    inc, project = await _incident(incident_id, principal, session, "editor")
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        data["status"] = incident_svc.normalise_status(data["status"])
        if data["status"] == "resolved" and not inc.resolved_at and "resolved_at" not in data:
            data["resolved_at"] = datetime.utcnow()
    if "severity" in data:
        data["severity"] = incident_svc.normalise_severity(data["severity"])
    if "resolved_at" in data:
        data["resolved_at"] = _naive(data["resolved_at"])
    for k, v in data.items():
        setattr(inc, k, v)
    # The fingerprint deliberately does NOT follow edits: it identifies what the
    # incident looked like when it arrived, which is what a recurrence matches.
    inc.updated_at = datetime.utcnow()
    session.add(inc)
    await audit_record(session, entity_type="incident", entity_id=inc.id, action="update",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()})
    await session.commit()
    await session.refresh(inc)
    return (await _serialize_many(session, [inc]))[0]


class EscapedBody(BaseModel):
    escaped: bool = True
    case_id: Optional[int] = None
    no_test_existed: bool = False
    note: Optional[str] = PydField(default=None, max_length=1000)


@router.post("/incidents/{incident_id}/escaped")
async def mark_escaped(incident_id: int, body: EscapedBody,
                       principal: AuthPrincipal = Depends(get_current_principal),
                       session: AsyncSession = Depends(get_session)):
    """Record that this incident was a defect that escaped to production, and
    either the test that should have caught it or that no test existed."""
    inc, project = await _incident(incident_id, principal, session, "editor")
    if body.escaped:
        if body.case_id is None and not body.no_test_existed:
            raise HTTPException(status_code=400,
                                detail="Name the case that should have caught it, or set no_test_existed")
        if body.case_id is not None and body.no_test_existed:
            raise HTTPException(status_code=400, detail="case_id and no_test_existed are exclusive")
        await _case_in_project(body.case_id, inc.project_id, session)
        inc.escaped_defect = True
        inc.escaped_case_id = body.case_id
        inc.escaped_no_test = bool(body.no_test_existed)
        inc.escaped_note = body.note
        inc.escaped_marked_at = datetime.utcnow()
        inc.escaped_marked_by_id = principal.user.id
    else:
        inc.escaped_defect = False
        inc.escaped_case_id = None
        inc.escaped_no_test = False
        inc.escaped_note = None
        inc.escaped_marked_at = None
        inc.escaped_marked_by_id = None
    inc.updated_at = datetime.utcnow()
    session.add(inc)
    await audit_record(session, entity_type="incident", entity_id=inc.id,
                       action="mark_escaped" if body.escaped else "unmark_escaped",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"case_id": body.case_id, "no_test_existed": body.no_test_existed})
    await session.commit()
    await session.refresh(inc)
    return (await _serialize_many(session, [inc]))[0]


class ProposeBody(BaseModel):
    test_suite_id: Optional[int] = None


@router.post("/incidents/{incident_id}/propose-regression")
async def repropose(incident_id: int, body: ProposeBody,
                    principal: AuthPrincipal = Depends(get_current_principal),
                    session: AsyncSession = Depends(get_session)):
    inc, project = await _incident(incident_id, principal, session, "editor")
    if inc.regression_case_id:
        raise HTTPException(status_code=409, detail="A regression case already exists for this incident")
    if inc.regression_proposal_id:
        prior = await session.get(CaseProposal, inc.regression_proposal_id)
        if prior and prior.status == "pending":
            raise HTTPException(status_code=409,
                                detail=f"Proposal #{prior.id} is still pending review")
    result = await propose_regression(session, inc, project, suite_id=body.test_suite_id,
                                      user_id=principal.user.id, principal=principal)
    await session.commit()
    return result


@router.delete("/incidents/{incident_id}")
async def delete_incident(incident_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                          session: AsyncSession = Depends(get_session)):
    inc, project = await _incident(incident_id, principal, session, "editor")
    await audit_record(session, entity_type="incident", entity_id=inc.id, action="delete",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"title": inc.title, "escaped_defect": inc.escaped_defect})
    await session.delete(inc)
    await session.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Escaped-defect report
# ---------------------------------------------------------------------------

def _period_start(dt: datetime, period: str) -> str:
    if period == "week":
        monday = (dt - timedelta(days=dt.weekday())).date()
        return monday.isoformat()
    return dt.strftime("%Y-%m")


def escaped_rollup(rows: List[Tuple[Incident, Optional[datetime]]], period: str) -> Dict[str, Any]:
    """rows: (incident, regression case created_at). A test counts as "added
    afterwards" when the incident's regression case was created at or after
    the incident started — a case that already existed and was merely linked
    does not."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for inc, case_created in rows:
        key = _period_start(inc.started_at, period)
        b = buckets.setdefault(key, {"period": key, "escaped": 0, "missed_by_existing_test": 0,
                                     "no_test_existed": 0, "test_added_after": 0})
        b["escaped"] += 1
        if inc.escaped_case_id:
            b["missed_by_existing_test"] += 1
        if inc.escaped_no_test:
            b["no_test_existed"] += 1
        if inc.regression_case_id and case_created and case_created >= inc.started_at:
            b["test_added_after"] += 1
    periods = sorted(buckets.values(), key=lambda b: b["period"])
    for b in periods:
        b["pct_test_added_after"] = round(100.0 * b["test_added_after"] / b["escaped"], 1)
    total = sum(b["escaped"] for b in periods)
    added = sum(b["test_added_after"] for b in periods)
    return {
        "period": period, "periods": periods,
        "totals": {"escaped": total,
                   "missed_by_existing_test": sum(b["missed_by_existing_test"] for b in periods),
                   "no_test_existed": sum(b["no_test_existed"] for b in periods),
                   "test_added_after": added,
                   "pct_test_added_after": round(100.0 * added / total, 1) if total else None},
    }


@router.get("/projects/{project_id}/incidents/escaped-report")
async def escaped_report(project_id: int, days: int = Query(180, ge=1, le=3650),
                         period: str = Query("month", pattern="^(month|week)$"),
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)):
    await _project(project_id, principal, session)
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await session.exec(
        select(Incident, TestCase.created_at)
        .join(TestCase, TestCase.id == Incident.regression_case_id, isouter=True)
        .where(Incident.project_id == project_id, Incident.escaped_defect == True,  # noqa: E712
               Incident.started_at >= since))).all()
    return {"project_id": project_id, "days": days, **escaped_rollup(list(rows), period)}
