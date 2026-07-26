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
from typing import List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user, get_current_principal, AuthPrincipal
from app.core.config import settings as cfg
from app.services.access_service import access_service
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

    sec = _security_settings(project)
    if not sec.enabled:
        raise HTTPException(status_code=403, detail="Security scanning is disabled for this project")
    if not req.authorized:
        raise HTTPException(status_code=400, detail="You must attest authorization to scan this target (authorized=true)")
    if not _host_allowed(req.target_url, sec.allowed_domains):
        raise HTTPException(status_code=403, detail="Target host is not on this project's authorized-domain allowlist")

    scan_type = (req.scan_type or "baseline").lower()
    if scan_type not in ("baseline", "active"):
        raise HTTPException(status_code=400, detail="scan_type must be 'baseline' or 'active'")
    if scan_type == "active":
        workspace = await session.get(Workspace, project.workspace_id) if project.workspace_id else None
        deployment_ok = cfg.SECURITY_ACTIVE_SCAN_ENABLED or bool(workspace and workspace.active_scan_enabled)
        if not deployment_ok:
            raise HTTPException(status_code=403,
                detail="Active scanning is disabled for this workspace — a workspace admin can enable it in Security settings")
        if not sec.allow_active_scan:
            raise HTTPException(status_code=403, detail="Active scanning is not enabled for this project")

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


_FINDING_STATUSES = {"open", "acknowledged", "false_positive", "resolved"}


@router.patch("/security-findings/{finding_id}", response_model=SecurityFindingRead)
async def update_security_finding(
    finding_id: int,
    body: dict = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Triage a finding: set status (open/acknowledged/false_positive/resolved)
    and/or assignee. false_positive verdicts on ZAP findings carry forward to
    the same finding in future scans (fingerprint match)."""
    finding = await session.get(SecurityFinding, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    if not finding.project_id or not await access_service.has_project_access(
            current_user.id, finding.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")

    if "status" in body:
        status = str(body["status"])
        if status not in _FINDING_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {sorted(_FINDING_STATUSES)}")
        finding.status = status
        from datetime import datetime as _dt
        finding.resolved_at = _dt.utcnow() if status == "resolved" else None
    if "assignee_id" in body:
        finding.assignee_id = body["assignee_id"] or None

    session.add(finding)
    await session.commit()
    await session.refresh(finding)
    return finding


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
