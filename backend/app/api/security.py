"""Active/authenticated security-scan API (PLATFORM_VISION.md P-4, item 6).

Creating a scan is gated hard:
- the project must have security scanning enabled,
- the target host must be on the project's authorized-domain allowlist,
- the caller must attest authorization (`authorized: true`),
- active (attacking) scans additionally require the global
  SECURITY_ACTIVE_SCAN_ENABLED flag AND per-project allow_active_scan.

This keeps scanning defensive and authorized — never an offensive tool pointed
at arbitrary targets.
"""
import json
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user, get_current_principal, AuthPrincipal
from app.core.config import settings as cfg
from app.services import audit, sarif
from app.services import finding_lifecycle as lifecycle
from app.services.access_service import access_service
from app.api.jobs import api_key_allows_project
from app.models import (
    User, Project, Workspace, UserWorkspace, SecurityScan, SecurityScanRead, SecurityScanRequest,
    SecuritySettings, DEFAULT_SECURITY_SETTINGS, SecurityFinding, SecurityFindingRead,
)

router = APIRouter()


def _scan_read(scan: SecurityScan, findings=None) -> SecurityScanRead:
    """Serialize a scan without leaking the stored auth-header secret."""
    return SecurityScanRead(
        **scan.model_dump(exclude={"auth_header_value"}),
        findings=findings or [])


def _security_settings(project: Project) -> SecuritySettings:
    if project.security_settings:
        merged = {**DEFAULT_SECURITY_SETTINGS.model_dump(), **project.security_settings}
        return SecuritySettings(**merged)
    return SecuritySettings()


def _host_allowed(target_url: str, allowed_domains: List[str]) -> bool:
    """True if the target's host matches an allowed domain (exact or subdomain)."""
    try:
        host = (urlsplit(target_url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for d in allowed_domains or []:
        d = (d or "").lower().strip()
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


async def _scan_gate(session: AsyncSession, project: Project, target_url: str,
                     scan_type: str, principal: Optional[AuthPrincipal] = None,
                     user_id: Optional[int] = None) -> Optional[str]:
    """Why a scan of this target may not run right now, or None. Shared by the
    scan endpoint and retest-on-fix, so a retest can never get past a gate a
    person could not. Asks the policy engine (security.scan), whose rule wraps
    `_scan_gate_reason` — so a refusal is logged with the gate that fired."""
    from app.services.policy import PolicyContext, evaluate
    ctx = (PolicyContext.from_principal(principal) if principal is not None
           else PolicyContext(user_id=user_id))
    ctx.workspace_id, ctx.project_id, ctx.session = project.workspace_id, project.id, session
    ctx.facts = {"project": project, "target_url": target_url, "scan_type": scan_type}
    decision = await evaluate("security.scan", ctx)
    return None if decision.allowed else decision.reason


async def _scan_gate_reason(session: AsyncSession, project: Project, target_url: str,
                            scan_type: str) -> Optional[str]:
    """The gate itself (the security.scan policy rule)."""
    sec = _security_settings(project)
    if not sec.enabled:
        return "Security scanning is disabled for this project"
    if not _host_allowed(target_url, sec.allowed_domains):
        return "Target host is not on this project's authorized-domain allowlist"
    if scan_type == "active":
        workspace = await session.get(Workspace, project.workspace_id) if project.workspace_id else None
        deployment_ok = cfg.SECURITY_ACTIVE_SCAN_ENABLED or bool(workspace and workspace.active_scan_enabled)
        if not deployment_ok:
            return ("Active scanning is disabled for this workspace — a workspace admin "
                    "can enable it in Security settings")
        if not sec.allow_active_scan:
            return "Active scanning is not enabled for this project"
    return None


def _check_key_scope(principal: AuthPrincipal, project_id: Optional[int]) -> None:
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")


@router.get("/projects/{project_id}/security-settings", response_model=SecuritySettings)
async def get_security_settings(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return _security_settings(project)


@router.put("/projects/{project_id}/security-settings", response_model=SecuritySettings)
async def set_security_settings(
    project_id: int,
    sec: SecuritySettings = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="admin"):
        raise HTTPException(status_code=403, detail="Admin role required to change scan authorization")
    project.security_settings = sec.model_dump()
    session.add(project)
    await session.commit()
    return sec


@router.post("/projects/{project_id}/security-scan", response_model=SecurityScanRead, status_code=202)
async def create_security_scan(
    project_id: int,
    req: SecurityScanRequest,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(principal.user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")

    if not req.authorized:
        raise HTTPException(status_code=400, detail="You must attest authorization to scan this target (authorized=true)")
    scan_type = (req.scan_type or "baseline").lower()
    if scan_type not in ("baseline", "active"):
        raise HTTPException(status_code=400, detail="scan_type must be 'baseline' or 'active'")
    gate = await _scan_gate(session, project, req.target_url, scan_type, principal=principal)
    if gate:
        raise HTTPException(status_code=403, detail=gate)
    sec = _security_settings(project)

    # API import: the spec URL's host must be allowlisted too, so a scan can't
    # be used to pull and probe an arbitrary third-party spec.
    openapi_url = (req.openapi_url or "").strip() or None
    if openapi_url:
        if not openapi_url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="openapi_url must be an http(s) URL")
        if not _host_allowed(openapi_url, sec.allowed_domains):
            raise HTTPException(status_code=403, detail="OpenAPI spec host is not on this project's authorized-domain allowlist")

    # Header auth: a value implies a header name (default Authorization).
    auth_value = (req.auth_header_value or "").strip() or None
    auth_name = (req.auth_header_name or "").strip() or ("Authorization" if auth_value else None)

    scan = SecurityScan(
        project_id=project_id, target_url=req.target_url, scan_type=scan_type,
        authenticated=bool(req.authenticated), status="pending",
        requested_by_id=principal.user.id,
        openapi_url=openapi_url,
        auth_header_name=auth_name, auth_header_value=auth_value)
    session.add(scan)
    await session.commit()
    await session.refresh(scan)

    try:
        from app.tasks.zap_tasks import run_zap_scan
        run_zap_scan.delay(scan.id)
    except Exception as e:  # noqa: BLE001
        scan.status = "error"
        scan.error = f"Could not queue scan: {e}"
        session.add(scan)
        await session.commit()

    return _scan_read(scan)


@router.get("/projects/{project_id}/security-scans", response_model=List[SecurityScanRead])
async def list_security_scans(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    scans = (await session.exec(
        select(SecurityScan).where(SecurityScan.project_id == project_id)
        .order_by(SecurityScan.created_at.desc()))).all()
    return [_scan_read(s) for s in scans]


@router.get("/security-scans/{scan_id}", response_model=SecurityScanRead)
async def get_security_scan(
    scan_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    scan = await session.get(SecurityScan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not await access_service.has_project_access(current_user.id, scan.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    severity_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rows = (await session.exec(
        select(SecurityFinding).where(SecurityFinding.scan_id == scan_id))).all()
    rows.sort(key=lambda r: severity_rank.get(r.severity, 9))
    return _scan_read(
        scan,
        findings=[SecurityFindingRead.model_validate(r, from_attributes=True) for r in rows])


# --- Findings workflow (WP7) -------------------------------------------------


class FindingUpdate(BaseModel):
    status: Optional[str] = None
    # The owner. `assignee_id` is the stored name; `owner_id` is accepted too.
    assignee_id: Optional[int] = None
    owner_id: Optional[int] = None
    due_at: Optional[datetime] = None
    risk_justification: Optional[str] = Field(default=None, max_length=4000)
    risk_expires_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=2000)


def _finding_out(f: SecurityFinding, now: Optional[datetime] = None) -> dict:
    out = SecurityFindingRead.model_validate(f, from_attributes=True).model_dump()
    out["overdue"] = lifecycle.is_overdue(f, now)
    return out


async def _can_own(user_id: int, project_id: int, session: AsyncSession) -> bool:
    user = await session.get(User, user_id)
    return bool(user and user.is_active and await access_service.has_project_access(
        user_id, project_id, session, min_role="editor"))


def _auto_verify_reason(scan_type: Optional[str]) -> str:
    """How a finding that gets no queued retest is verified — it depends on
    what produced it (only run-sourced findings come from a suite)."""
    st = scan_type or ""
    if st == "access_matrix":
        return "verified automatically by the next run of the access matrix that found it"
    if st.startswith("estate-"):
        return "verified automatically by the next scan of the estate that found it"
    if st.startswith("page-"):
        return "verified automatically by the next check of the monitored page that found it"
    return "verified automatically by the next run of the suite that found it"


async def _queue_retest(session: AsyncSession, finding: SecurityFinding,
                        requested_by: User) -> dict:
    """Retest-on-fix. ZAP findings get a targeted rescan of the same target and
    scan type when every gate still allows it; run-sourced findings (passive,
    leak) are verified by the next run of the suite that produced them —
    re-running a whole suite on someone's behalf is not ours to decide."""
    if finding.scan_type != "zap" or not finding.scan_id:
        return {"queued": False, "reason": _auto_verify_reason(finding.scan_type)}
    source = await session.get(SecurityScan, finding.scan_id)
    project = await session.get(Project, finding.project_id) if finding.project_id else None
    if not source or not project:
        return {"queued": False, "reason": "the originating scan no longer exists"}
    if source.auth_header_name:
        # The header token is discarded after each scan, so a retest would run
        # unauthenticated and "verify" anything behind the login.
        return {"queued": False,
                "reason": "the original scan used header auth, whose token is not kept — "
                          "run a new scan with the token to verify the fix"}
    in_flight = (await session.exec(select(SecurityScan).where(
        SecurityScan.project_id == source.project_id,
        SecurityScan.target_url == source.target_url,
        SecurityScan.scan_type == source.scan_type,
        SecurityScan.status.in_(("pending", "running")),
        SecurityScan.id > source.id))).first()
    if in_flight:
        finding.retest_scan_id = in_flight.id
        return {"queued": True, "scan_id": in_flight.id, "reused": True}
    gate = await _scan_gate(session, project, source.target_url, source.scan_type,
                            user_id=getattr(requested_by, "id", None))
    if gate:
        return {"queued": False, "reason": f"{gate}; the next scan of this target will verify it"}
    retest = SecurityScan(
        project_id=source.project_id, target_url=source.target_url,
        scan_type=source.scan_type, authenticated=source.authenticated,
        openapi_url=source.openapi_url, status="pending", requested_by_id=requested_by.id)
    session.add(retest)
    await session.flush()
    finding.retest_scan_id = retest.id
    return {"queued": True, "scan_id": retest.id, "reused": False}


@router.patch("/security-findings/{finding_id}")
async def update_security_finding(
    finding_id: int,
    body: FindingUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Owner, due date and status transitions for one finding. Every change is
    audited. Accepting a risk needs a justification, an expiry and a project
    admin (who is recorded as the approver); so does moving a due date, since
    an editor quietly extending their own deadline defeats the SLA."""
    finding = await session.get(SecurityFinding, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    user = principal.user
    if not finding.project_id or not await access_service.has_project_access(
            user.id, finding.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, finding.project_id)
    if finding.superseded_by_id:
        raise HTTPException(status_code=409, detail={
            "message": "This occurrence has been superseded by a newer scan; update the latest one",
            "latest_finding_id": finding.superseded_by_id})

    fields = body.model_fields_set
    project = await session.get(Project, finding.project_id)
    is_admin = await access_service.has_project_access(
        user.id, finding.project_id, session, min_role="admin")
    now = datetime.utcnow()
    changes: dict = {}
    retest = None

    owner_field = "owner_id" if "owner_id" in fields else ("assignee_id" if "assignee_id" in fields else None)
    if owner_field:
        new_owner = getattr(body, owner_field) or None
        if new_owner and not await _can_own(new_owner, finding.project_id, session):
            raise HTTPException(status_code=422,
                                detail="Owner must be an active user with editor access to this project")
        if new_owner != finding.assignee_id:
            changes["owner"] = {"from": finding.assignee_id, "to": new_owner}
            finding.assignee_id = new_owner

    if "due_at" in fields and body.due_at != finding.due_at:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Project admin role required to change a due date")
        changes["due_at"] = {"from": finding.due_at.isoformat() if finding.due_at else None,
                             "to": body.due_at.isoformat() if body.due_at else None}
        finding.due_at = body.due_at

    if body.status is not None:
        target = lifecycle.normalize_status(body.status)
        current = lifecycle.normalize_status(finding.status)
        try:
            lifecycle.check_transition(current, target, justification=body.risk_justification,
                                       expires_at=body.risk_expires_at, now=now)
        except lifecycle.TransitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if target == "accepted_risk":
            if not is_admin:
                raise HTTPException(status_code=403,
                                    detail="Accepting a risk needs a project admin, who is recorded as the approver")
            finding.risk_justification = body.risk_justification.strip()
            finding.risk_expires_at = body.risk_expires_at
            finding.risk_approved_by_id = user.id
            finding.risk_approved_at = now
        elif current == "accepted_risk":
            finding.risk_justification = finding.risk_expires_at = None
            finding.risk_approved_by_id = finding.risk_approved_at = None
        if target == "open":
            finding.resolved_at = finding.verified_at = None
        if target == "fixed_pending_verification":
            finding.resolved_at = now
        finding.status = target
        changes["status"] = {"from": current, "to": target}
        if target == "accepted_risk":
            changes["status"].update({"justification": finding.risk_justification,
                                      "expires_at": finding.risk_expires_at.isoformat()})
        if target == "fixed_pending_verification":
            retest = await _queue_retest(session, finding, user)
            changes["retest"] = retest

    if not changes:
        return _finding_out(finding, now)
    if body.note:
        changes["note"] = body.note
    changes["fingerprint"] = finding.fingerprint
    session.add(finding)
    await audit.record(
        session, entity_type="security_finding", entity_id=finding.id,
        action="status_change" if "status" in changes else "update",
        workspace_id=project.workspace_id if project else None,
        changes=changes, principal=principal, request=request)
    await session.commit()
    await session.refresh(finding)

    if retest and retest.get("queued") and not retest.get("reused"):
        try:
            from app.tasks.zap_tasks import run_zap_scan
            run_zap_scan.delay(retest["scan_id"])
        except Exception as exc:  # noqa: BLE001 — the next scan still verifies
            retest = {**retest, "queued": False, "reason": f"could not queue the rescan: {exc}"}
    out = _finding_out(finding, now)
    if retest is not None:
        out["retest"] = retest
    return out


@router.get("/projects/{project_id}/security-findings")
async def list_project_findings(
    project_id: int,
    status: Optional[str] = Query(None, description="comma-separated statuses"),
    severity: Optional[str] = Query(None, description="comma-separated severities"),
    scan_type: Optional[str] = None,
    overdue: bool = False,
    owner_id: Optional[int] = None,
    include_superseded: bool = False,
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """The project's findings register: the latest occurrence of each logical
    finding across ZAP scans, passive passes and leak detection."""
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, project_id)
    rows = await _project_findings(session, project_id, status=status, severity=severity,
                                   scan_type=scan_type, owner_id=owner_id,
                                   include_superseded=include_superseded, limit=limit)
    now = datetime.utcnow()
    if overdue:
        rows = [r for r in rows if lifecycle.is_overdue(r, now)]
    counts: dict = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    actionable, overdue_n = lifecycle.partition_by_status(rows)
    return {"project_id": project_id, "total": len(rows), "by_status": counts,
            "actionable": actionable, "overdue": overdue_n,
            "findings": [_finding_out(r, now) for r in rows]}


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


async def _project_findings(session: AsyncSession, project_id: int, *, status=None,
                            severity=None, scan_type=None, owner_id=None,
                            include_superseded=False, limit=2000):
    q = select(SecurityFinding).where(SecurityFinding.project_id == project_id)
    if not include_superseded:
        q = q.where(SecurityFinding.superseded_by_id == None)  # noqa: E711
    if status:
        wanted = [lifecycle.normalize_status(x) for x in status.split(",") if x.strip()]
        q = q.where(SecurityFinding.status.in_(wanted))
    if severity:
        q = q.where(SecurityFinding.severity.in_([x.strip().lower() for x in severity.split(",") if x.strip()]))
    if scan_type:
        q = q.where(SecurityFinding.scan_type == scan_type)
    if owner_id:
        q = q.where(SecurityFinding.assignee_id == owner_id)
    rows = (await session.exec(q.order_by(SecurityFinding.id.desc()).limit(limit))).all()
    rows.sort(key=lambda r: (_SEVERITY_RANK.get(r.severity, 9), r.due_at or datetime.max))
    return rows


def _sarif_response(doc: dict, filename: str) -> Response:
    return Response(
        content=json.dumps(doc, indent=2, default=str),
        media_type="application/sarif+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{project_id}/security-findings/sarif")
async def project_findings_sarif(
    project_id: int,
    status: Optional[str] = Query(None, description="comma-separated statuses; default all"),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """SARIF 2.1.0 of the project's current findings (latest occurrence each)."""
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, project_id)
    rows = await _project_findings(session, project_id, status=status)
    doc = sarif.build_sarif(rows, automation_id=f"traceiq/project/{project_id}")
    return _sarif_response(doc, f"traceiq-project-{project_id}.sarif")


@router.get("/security-scans/{scan_id}/sarif")
async def scan_sarif(
    scan_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """SARIF 2.1.0 of one scan's findings."""
    scan = await session.get(SecurityScan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not await access_service.has_project_access(principal.user.id, scan.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, scan.project_id)
    rows = (await session.exec(
        select(SecurityFinding).where(SecurityFinding.scan_id == scan_id))).all()
    doc = sarif.build_sarif(rows, automation_id=f"traceiq/scan/{scan_id}")
    return _sarif_response(doc, f"traceiq-scan-{scan_id}.sarif")


@router.get("/projects/{project_id}/security-findings/owner-candidates")
async def finding_owner_candidates(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Workspace members with editor access to the project — who may own a finding."""
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, project_id)
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    members = (await session.exec(
        select(User).join(UserWorkspace, UserWorkspace.user_id == User.id)
        .where(UserWorkspace.workspace_id == project.workspace_id,
               User.is_active == True)  # noqa: E712
        .order_by(User.full_name))).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name}
            for u in members if await access_service.has_project_access(
                u.id, project_id, session, min_role="editor")]


@router.get("/workspaces/{workspace_id}/finding-sla")
async def get_finding_sla(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    role = await _workspace_role(session, workspace_id, current_user.id)
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"workspace_id": workspace_id, "sla_days": lifecycle.resolve_sla(ws.finding_sla_days),
            "default_sla_days": lifecycle.DEFAULT_FINDING_SLA,
            "can_edit": role in ("admin", "owner")}


@router.put("/workspaces/{workspace_id}/finding-sla")
async def set_finding_sla(
    workspace_id: int,
    request: Request,
    body: dict = Body(...),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Severity → days. Applies to findings first detected from now on; existing
    due dates are not rewritten (an SLA change is not a reason to move a
    deadline someone already committed to)."""
    role = await _workspace_role(session, workspace_id, principal.user.id)
    if role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Workspace admin required to change the finding SLA")
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    try:
        sla = lifecycle.validate_sla(body.get("sla_days", body))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    before = lifecycle.resolve_sla(ws.finding_sla_days)
    ws.finding_sla_days = {**(ws.finding_sla_days or {}), **sla}
    session.add(ws)
    await audit.record(session, entity_type="workspace", entity_id=workspace_id,
                       action="finding_sla_update", workspace_id=workspace_id,
                       changes={"from": before, "to": lifecycle.resolve_sla(ws.finding_sla_days)},
                       principal=principal, request=request)
    await session.commit()
    return await get_finding_sla(workspace_id, session, principal.user)


@router.get("/security-scans/{scan_id}/diff")
async def security_scan_diff(
    scan_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Scan-over-scan comparison against the previous completed scan of the
    same project+target: which findings are new, fixed, or persisting."""
    scan = await session.get(SecurityScan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not await access_service.has_project_access(current_user.id, scan.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    prev = (await session.exec(
        select(SecurityScan).where(
            SecurityScan.project_id == scan.project_id,
            SecurityScan.target_url == scan.target_url,
            SecurityScan.status == "completed",
            SecurityScan.id != scan.id,
            SecurityScan.created_at < scan.created_at,
        ).order_by(SecurityScan.created_at.desc()))).first()

    current_rows = (await session.exec(
        select(SecurityFinding).where(SecurityFinding.scan_id == scan_id))).all()
    prev_rows = (await session.exec(
        select(SecurityFinding).where(SecurityFinding.scan_id == prev.id))).all() if prev else []

    cur_fp = {r.fingerprint: r for r in current_rows if r.fingerprint}
    prev_fp = {r.fingerprint: r for r in prev_rows if r.fingerprint}

    def _brief(r: SecurityFinding) -> dict:
        return {"id": r.id, "severity": r.severity, "title": r.title,
                "target_url": r.target_url, "status": r.status}

    new = [_brief(r) for fp, r in cur_fp.items() if fp not in prev_fp]
    fixed = [_brief(r) for fp, r in prev_fp.items() if fp not in cur_fp]
    persisting = [_brief(r) for fp, r in cur_fp.items() if fp in prev_fp]

    return {
        "scan_id": scan_id,
        "previous_scan_id": prev.id if prev else None,
        "baseline_available": prev is not None,
        "new": new,
        "fixed": fixed,
        "persisting_count": len(persisting),
    }


async def _workspace_role(session: AsyncSession, workspace_id: int, user_id: int) -> str:
    row = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.workspace_id == workspace_id,
        UserWorkspace.user_id == user_id))).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return (row.role or "").lower()


@router.get("/workspaces/{workspace_id}/security")
async def get_workspace_security(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Workspace-level security settings + whether the caller may change them."""
    role = await _workspace_role(session, workspace_id, current_user.id)
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {
        "workspace_id": workspace_id,
        # Effective master switch: the global env flag OR the workspace toggle.
        "active_scan_enabled": bool(cfg.SECURITY_ACTIVE_SCAN_ENABLED or ws.active_scan_enabled),
        "workspace_toggle": ws.active_scan_enabled,
        "forced_by_deployment": bool(cfg.SECURITY_ACTIVE_SCAN_ENABLED),
        "can_edit": role in ("admin", "owner"),
    }


@router.put("/workspaces/{workspace_id}/security")
async def set_workspace_security(
    workspace_id: int,
    body: dict = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Toggle workspace-wide active scanning (workspace admin/owner only)."""
    role = await _workspace_role(session, workspace_id, current_user.id)
    if role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Workspace admin required to change active-scan policy")
    ws = await session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if "active_scan_enabled" in body:
        ws.active_scan_enabled = bool(body["active_scan_enabled"])
        session.add(ws)
        await session.commit()
        await session.refresh(ws)
    return await get_workspace_security(workspace_id, session, current_user)
