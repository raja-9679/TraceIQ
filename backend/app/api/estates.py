"""Estate scanning + monitored pages API (WP9). Passive checks only.

    GET    /api/projects/{project_id}/estates                 list (viewer)
    POST   /api/projects/{project_id}/estates                 create (editor)
    GET    /api/estates/{id}                                  estate + domains worst-first + trend (viewer)
    PUT    /api/estates/{id}                                  update (editor)
    DELETE /api/estates/{id}                                  delete + history (editor)
    POST   /api/estates/{id}/domains                          import textarea/CSV (editor)
    DELETE /api/estates/{id}/domains/{domain_id}              remove a domain (editor)
    POST   /api/estates/{id}/scan                             scan now (editor)
    GET    /api/estates/{id}/scans                            scan history (viewer)
    GET    /api/estate-domains/{domain_id}                    checks + deduction arithmetic + history (viewer)
    GET    /api/workspaces/{workspace_id}/estate-certificates certificate calendar (viewer, per project)

    GET    /api/projects/{project_id}/monitored-pages         list (viewer)
    POST   /api/projects/{project_id}/monitored-pages         create (editor)
    GET    /api/monitored-pages/{id}                          fetch (viewer)
    PUT    /api/monitored-pages/{id}                          update incl. defacement config (editor)
    DELETE /api/monitored-pages/{id}                          delete + inventory + history (editor)
    POST   /api/monitored-pages/{id}/check                    check now (editor)
    POST   /api/monitored-pages/{id}/baseline/reset           re-baseline content on next check (editor)
    GET    /api/monitored-pages/{id}/scripts                  script inventory (viewer)
    POST   /api/monitored-pages/{id}/scripts/{sid}/approve    approve with justification (editor, audited)
    POST   /api/monitored-pages/{id}/scripts/{sid}/revoke     withdraw approval (editor, audited)
    GET    /api/monitored-pages/{id}/changes                  change history (viewer)

Every host must be on the project's security allowlist with security testing
enabled — checked on import/create AND again when a scan or check runs, since
the allowlist can shrink in between.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField, field_validator
from sqlmodel import delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    Estate, EstateDomain, EstateDomainResult, EstateScan, MonitoredPage, PageChangeEvent,
    PageScript, Project,
)
from app.services import estate_checks as ec
from app.services.access_matrix import host_allowed
from app.services.access_matrix_runtime import security_settings_for
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

MAX_ESTATE_DOMAINS = 500
TREND_POINTS = 12
TREND_DAYS = 120
MIN_SCHEDULE_GAP_SECONDS = 3600


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

async def _project_for(session: AsyncSession, principal: AuthPrincipal, project_id: int,
                       min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} role required")
    return project


async def _estate_for(session, principal, estate_id: int, min_role: str = "viewer"):
    estate = await session.get(Estate, estate_id)
    if not estate:
        raise HTTPException(status_code=404, detail="Estate not found")
    return estate, await _project_for(session, principal, estate.project_id, min_role)


async def _page_for(session, principal, page_id: int, min_role: str = "viewer"):
    page = await session.get(MonitoredPage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Monitored page not found")
    return page, await _project_for(session, principal, page.project_id, min_role)


def _allowlist(project: Project) -> List[str]:
    sec = security_settings_for(project)
    return [d for d in (sec.get("allowed_domains") or []) if d] if sec.get("enabled") else []


def _require_allowlist(project: Project) -> List[str]:
    allowed = _allowlist(project)
    if not allowed:
        raise HTTPException(status_code=403, detail=(
            "Enable security testing for this project and allowlist the hosts to "
            "check in Security settings first"))
    return allowed


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _valid_cron(expr: str) -> str:
    from croniter import croniter
    expr = (expr or "").strip()
    if not croniter.is_valid(expr):
        raise ValueError("cron_expression is not a valid cron expression")
    it = croniter(expr, datetime(2026, 1, 1))
    a, b = it.get_next(datetime), it.get_next(datetime)
    if (b - a).total_seconds() < MIN_SCHEDULE_GAP_SECONDS:
        raise ValueError("schedules may run at most hourly")
    return expr


def _emails(v: Optional[List[str]]) -> Optional[List[str]]:
    if not v:
        return None
    out = []
    for e in v[:20]:
        e = (e or "").strip()
        if not e:
            continue
        if "@" not in e or len(e) > 254 or any(c.isspace() for c in e):
            raise ValueError(f"not an email address: {e[:60]}")
        out.append(e)
    return out or None


def parse_domain_import(text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """Hostnames from a textarea or CSV paste: one per line or comma/semicolon
    separated; in a CSV with extra columns, non-hostname cells after the first
    are ignored; a pasted URL is reduced to its host. Returns (hostnames in order, rejected [{value, reason}])."""
    names: List[str] = []
    rejected: List[Dict[str, str]] = []
    seen = set()
    for row in csv.reader(io.StringIO((text or "").replace(";", ","))):
        for col, cell in enumerate(row):
            v = cell.strip().strip('"').strip()
            if not v or v.startswith("#") or v.lower() in ("domain", "hostname", "host"):
                continue
            host = v
            if "://" in v:
                try:
                    host = urlsplit(v).hostname or ""
                except ValueError:
                    host = ""
            host = host.split("/")[0].split(":")[0].lower().rstrip(".")
            if host.startswith("*."):
                host = host[2:]
            if not ec.valid_hostname(host):
                # Later CSV columns may be metadata (owner, notes): ignore
                # those quietly; a bad first column is reported.
                if col == 0:
                    rejected.append({"value": v[:200], "reason": "not a valid hostname"})
                continue
            if host not in seen:
                seen.add(host)
                names.append(host)
    return names, rejected


class EstateIn(BaseModel):
    name: str = PydField(min_length=1, max_length=200)
    dkim_selectors: Optional[List[str]] = PydField(default=None, max_length=50)
    discover_subdomains: bool = True
    cron_expression: str = "0 3 * * *"
    schedule_enabled: bool = True
    cert_alert_days: Optional[List[int]] = PydField(default=None, max_length=10)
    alert_emails: Optional[List[str]] = PydField(default=None, max_length=20)

    @field_validator("cron_expression")
    @classmethod
    def _cron(cls, v):
        return _valid_cron(v)

    @field_validator("dkim_selectors")
    @classmethod
    def _selectors(cls, v):
        if not v:
            return None
        out = []
        for s in v:
            s = (s or "").strip().lower()
            if not s:
                continue
            if len(s) > 63 or not all(c.isalnum() or c in "-_." for c in s):
                raise ValueError(f"invalid DKIM selector: {s[:40]}")
            out.append(s)
        return out or None

    @field_validator("cert_alert_days")
    @classmethod
    def _days(cls, v):
        if not v:
            return None
        if any(d < 1 or d > 365 for d in v):
            raise ValueError("cert_alert_days must be between 1 and 365")
        return sorted(set(v), reverse=True)

    @field_validator("alert_emails")
    @classmethod
    def _alert_emails(cls, v):
        return _emails(v)


class DomainImport(BaseModel):
    text: str = PydField(min_length=1, max_length=200_000)


class ElementCheck(BaseModel):
    selector: str = PydField(min_length=1, max_length=500)
    must_contain: Optional[str] = PydField(default=None, max_length=500)
    must_not_contain: List[str] = PydField(default_factory=list, max_length=20)


class MonitoredPageIn(BaseModel):
    name: str = PydField(min_length=1, max_length=200)
    url: str = PydField(min_length=8, max_length=2000)
    track_scripts: bool = True
    track_content: bool = False
    # PCI DSS 11.6.1: at least weekly — the default.
    cron_expression: str = "0 6 * * 1"
    schedule_enabled: bool = True
    similarity_threshold: float = PydField(default=0.5, ge=0.0, le=1.0)
    forbidden_keywords: Optional[List[str]] = PydField(default=None, max_length=100)
    element_checks: Optional[List[ElementCheck]] = PydField(default=None, max_length=50)
    alert_emails: Optional[List[str]] = PydField(default=None, max_length=20)

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must be an http(s) URL")
        return v

    @field_validator("cron_expression")
    @classmethod
    def _cron(cls, v):
        return _valid_cron(v)

    @field_validator("forbidden_keywords")
    @classmethod
    def _keywords(cls, v):
        # Plain substrings, never regexes: the page is outsider-controlled.
        out = [k.strip()[:200] for k in (v or []) if k and k.strip()]
        return out or None

    @field_validator("alert_emails")
    @classmethod
    def _alert_emails(cls, v):
        return _emails(v)


class ApproveIn(BaseModel):
    # PCI DSS 6.4.3 asks for a written business/technical justification.
    justification: str = PydField(min_length=10, max_length=2000)


# ---------------------------------------------------------------------------
# Output shapes
# ---------------------------------------------------------------------------

def _estate_out(e: Estate, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {
        "id": e.id, "project_id": e.project_id, "name": e.name,
        "dkim_selectors": e.dkim_selectors or [], "discover_subdomains": e.discover_subdomains,
        "cron_expression": e.cron_expression, "schedule_enabled": e.schedule_enabled,
        "next_scan_at": e.next_scan_at, "last_scan_at": e.last_scan_at,
        "cert_alert_days": e.cert_alert_days or [30, 14, 7], "alert_emails": e.alert_emails or [],
        "created_at": e.created_at, "updated_at": e.updated_at,
    }
    if extra:
        out.update(extra)
    return out


def _domain_out(d: EstateDomain, trend: Optional[List[int]] = None) -> Dict[str, Any]:
    return {
        "id": d.id, "estate_id": d.estate_id, "hostname": d.hostname, "source": d.source,
        "is_apex": d.is_apex, "score": d.score, "cert_expires_at": d.cert_expires_at,
        "last_scanned_at": d.last_scanned_at, "created_at": d.created_at,
        "trend": trend or [],
    }


def _scan_out(s: EstateScan) -> Dict[str, Any]:
    return {"id": s.id, "estate_id": s.estate_id, "status": s.status, "trigger": s.trigger,
            "summary": s.summary, "error": s.error, "requested_by_id": s.requested_by_id,
            "created_at": s.created_at, "started_at": s.started_at, "finished_at": s.finished_at}


def _page_out(p: MonitoredPage, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {
        "id": p.id, "project_id": p.project_id, "name": p.name, "url": p.url,
        "track_scripts": p.track_scripts, "track_content": p.track_content,
        "cron_expression": p.cron_expression, "schedule_enabled": p.schedule_enabled,
        "next_check_at": p.next_check_at, "last_checked_at": p.last_checked_at,
        "last_status": p.last_status, "last_error": p.last_error,
        "headers_baseline": p.headers_baseline or {},
        "has_content_baseline": p.content_baseline is not None,
        "content_baseline_at": p.content_baseline_at,
        "similarity_threshold": p.similarity_threshold, "last_similarity": p.last_similarity,
        "forbidden_keywords": p.forbidden_keywords or [], "element_checks": p.element_checks or [],
        "alert_emails": p.alert_emails or [], "created_at": p.created_at,
    }
    if extra:
        out.update(extra)
    return out


def _script_out(s: PageScript) -> Dict[str, Any]:
    return {"id": s.id, "page_id": s.page_id, "script_key": s.script_key, "kind": s.kind,
            "src": s.src, "sha384": s.sha384, "integrity": s.integrity,
            "third_party": s.third_party, "present": s.present,
            "first_seen_at": s.first_seen_at, "last_seen_at": s.last_seen_at,
            "removed_at": s.removed_at, "authorized": s.authorized,
            "justification": s.justification, "authorized_by_id": s.authorized_by_id,
            "authorized_at": s.authorized_at}


def _next(expr: str) -> Optional[datetime]:
    from app.tasks.estate_tasks import next_run
    return next_run(expr, datetime.utcnow())


# ---------------------------------------------------------------------------
# Estates
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/estates")
async def list_estates(project_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_for(session, principal, project_id)
    estates = (await session.exec(select(Estate).where(
        Estate.project_id == project_id).order_by(Estate.name))).all()
    out = []
    for e in estates:
        stats = (await session.exec(select(
            func.count(EstateDomain.id), func.avg(EstateDomain.score), func.min(EstateDomain.score),
            func.min(EstateDomain.cert_expires_at)).where(EstateDomain.estate_id == e.id))).one()
        out.append(_estate_out(e, {
            "domain_count": stats[0], "average_score": round(stats[1]) if stats[1] is not None else None,
            "worst_score": stats[2], "next_cert_expiry": stats[3]}))
    return out


@router.post("/projects/{project_id}/estates", status_code=201)
async def create_estate(project_id: int, body: EstateIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_for(session, principal, project_id, "editor")
    e = Estate(project_id=project_id, created_by_id=principal.user.id,
               next_scan_at=_next(body.cron_expression), **body.model_dump())
    session.add(e)
    await session.flush()
    await audit_record(session, entity_type="estate", entity_id=e.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": e.name, "project_id": project_id})
    await session.commit()
    await session.refresh(e)
    return _estate_out(e, {"domains": []})


async def _trends(session, domain_ids: List[int]) -> Dict[int, List[int]]:
    if not domain_ids:
        return {}
    since = datetime.utcnow() - timedelta(days=TREND_DAYS)
    rows = (await session.exec(select(EstateDomainResult.domain_id, EstateDomainResult.score).where(
        EstateDomainResult.domain_id.in_(domain_ids), EstateDomainResult.created_at >= since)
        .order_by(EstateDomainResult.created_at))).all()
    out: Dict[int, List[int]] = {}
    for did, score in rows:
        out.setdefault(did, []).append(score)
    return {k: v[-TREND_POINTS:] for k, v in out.items()}


@router.get("/estates/{estate_id}")
async def get_estate(estate_id: int, session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id)
    domains = (await session.exec(select(EstateDomain).where(EstateDomain.estate_id == e.id))).all()
    trends = await _trends(session, [d.id for d in domains])
    # Worst first; never-scanned domains last (no score is not a good score).
    domains = sorted(domains, key=lambda d: (d.score is None, d.score if d.score is not None else 0,
                                             d.hostname))
    latest = (await session.exec(select(EstateScan).where(EstateScan.estate_id == e.id)
                                 .order_by(EstateScan.id.desc()).limit(1))).first()
    allowed = _allowlist(project)
    return _estate_out(e, {
        "domains": [{**_domain_out(d, trends.get(d.id)),
                     "allowlisted": host_allowed(ec.host_url(d.hostname), allowed)} for d in domains],
        "latest_scan": _scan_out(latest) if latest else None,
    })


@router.put("/estates/{estate_id}")
async def update_estate(estate_id: int, body: EstateIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id, "editor")
    data = body.model_dump()
    if data["cron_expression"] != e.cron_expression or (data["schedule_enabled"] and not e.schedule_enabled):
        e.next_scan_at = _next(data["cron_expression"])
    for k, v in data.items():
        setattr(e, k, v)
    e.updated_at = datetime.utcnow()
    session.add(e)
    await audit_record(session, entity_type="estate", entity_id=e.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={k: v for k, v in data.items() if k != "alert_emails"})
    await session.commit()
    await session.refresh(e)
    return _estate_out(e)


@router.delete("/estates/{estate_id}")
async def delete_estate(estate_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id, "editor")
    scan_ids = select(EstateScan.id).where(EstateScan.estate_id == e.id)
    domain_ids = select(EstateDomain.id).where(EstateDomain.estate_id == e.id)
    await session.exec(delete(EstateDomainResult).where(
        EstateDomainResult.scan_id.in_(scan_ids) | EstateDomainResult.domain_id.in_(domain_ids)))
    await session.exec(delete(EstateScan).where(EstateScan.estate_id == e.id))
    await session.exec(delete(EstateDomain).where(EstateDomain.estate_id == e.id))
    await session.delete(e)
    await audit_record(session, entity_type="estate", entity_id=estate_id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": e.name})
    await session.commit()
    return {"deleted": estate_id}


@router.post("/estates/{estate_id}/domains")
async def import_domains(estate_id: int, body: DomainImport, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id, "editor")
    allowed = _require_allowlist(project)
    names, rejected = parse_domain_import(body.text)
    existing = set((await session.exec(select(EstateDomain.hostname).where(
        EstateDomain.estate_id == e.id))).all())
    room = MAX_ESTATE_DOMAINS - len(existing)
    added = []
    for host in names:
        if host in existing:
            continue
        if not host_allowed(ec.host_url(host), allowed):
            rejected.append({"value": host, "reason": "not on the project's security allowlist"})
            continue
        if room <= 0:
            rejected.append({"value": host, "reason": f"an estate holds at most {MAX_ESTATE_DOMAINS} domains"})
            continue
        session.add(EstateDomain(estate_id=e.id, hostname=host, source="user"))
        existing.add(host)
        added.append(host)
        room -= 1
    if added:
        await audit_record(session, entity_type="estate", entity_id=e.id, action="import_domains",
                           workspace_id=project.workspace_id, principal=principal, request=request,
                           changes={"added": added[:100], "count": len(added)})
    await session.commit()
    return {"added": added, "rejected": rejected[:200]}


@router.delete("/estates/{estate_id}/domains/{domain_id}")
async def remove_domain(estate_id: int, domain_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id, "editor")
    d = await session.get(EstateDomain, domain_id)
    if not d or d.estate_id != e.id:
        raise HTTPException(status_code=404, detail="Domain not found in this estate")
    await session.exec(delete(EstateDomainResult).where(EstateDomainResult.domain_id == d.id))
    await session.delete(d)
    await audit_record(session, entity_type="estate", entity_id=e.id, action="remove_domain",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"hostname": d.hostname})
    await session.commit()
    return {"deleted": domain_id}


@router.post("/estates/{estate_id}/scan", status_code=202)
async def scan_now(estate_id: int, request: Request,
                   session: AsyncSession = Depends(get_session),
                   principal: AuthPrincipal = Depends(get_current_principal)):
    e, project = await _estate_for(session, principal, estate_id, "editor")
    _require_allowlist(project)
    from app.tasks.estate_tasks import STALE_SCAN
    busy = (await session.exec(select(EstateScan.id).where(
        EstateScan.estate_id == e.id, EstateScan.status.in_(("pending", "running")),
        EstateScan.created_at > datetime.utcnow() - STALE_SCAN))).first()
    if busy:
        raise HTTPException(status_code=409, detail="A scan of this estate is already in progress")
    scan = EstateScan(estate_id=e.id, trigger="manual", requested_by_id=principal.user.id)
    session.add(scan)
    await session.flush()
    await audit_record(session, entity_type="estate", entity_id=e.id, action="scan",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"scan_id": scan.id})
    await session.commit()
    await session.refresh(scan)
    from app.tasks.estate_tasks import run_estate_scan
    try:
        run_estate_scan.delay(e.id, scan.id, "manual")
    except Exception as exc:  # noqa: BLE001
        print(f"[Estate] failed to queue scan {scan.id}: {exc}")
    return _scan_out(scan)


@router.get("/estates/{estate_id}/scans")
async def list_scans(estate_id: int, limit: int = Query(30, ge=1, le=200),
                     session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    e, _ = await _estate_for(session, principal, estate_id)
    rows = (await session.exec(select(EstateScan).where(EstateScan.estate_id == e.id)
                               .order_by(EstateScan.id.desc()).limit(limit))).all()
    return [_scan_out(s) for s in rows]


@router.get("/estate-domains/{domain_id}")
async def domain_detail(domain_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    d = await session.get(EstateDomain, domain_id)
    if not d:
        raise HTTPException(status_code=404, detail="Domain not found")
    e, _ = await _estate_for(session, principal, d.estate_id)
    history = (await session.exec(select(EstateDomainResult).where(
        EstateDomainResult.domain_id == d.id).order_by(EstateDomainResult.created_at.desc())
        .limit(60))).all()
    latest = history[0] if history else None
    return {
        "domain": _domain_out(d, [h.score for h in reversed(history)][-TREND_POINTS:]),
        "estate": {"id": e.id, "name": e.name},
        "latest": None if not latest else {
            "scan_id": latest.scan_id, "score": latest.score, "checks": latest.checks or [],
            "deductions": latest.deductions or [], "created_at": latest.created_at,
            # The arithmetic, spelled out: 100 - Σ deductions = score (floored at 0).
            "arithmetic": {"start": 100, "deducted": sum(x["points"] for x in latest.deductions or []),
                           "score": latest.score},
        },
        "history": [{"scan_id": h.scan_id, "score": h.score, "created_at": h.created_at}
                    for h in history],
        "rubric": {k: {"points": v[0], "reason": v[1]} for k, v in ec.RUBRIC.items()},
    }


@router.get("/workspaces/{workspace_id}/estate-certificates")
async def certificate_calendar(workspace_id: int, session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    """Certificate expiries across the workspace's estates — only the projects
    the caller (and the API key's scope) can read."""
    projects = (await session.exec(select(Project).where(Project.workspace_id == workspace_id))).all()
    visible = []
    for p in projects:
        if principal.api_key is not None and not api_key_allows_project(principal.api_key, p.id):
            continue
        if await access_service.has_project_access(principal.user.id, p.id, session, min_role="viewer"):
            visible.append(p)
    if not visible:
        if not projects:
            raise HTTPException(status_code=404, detail="Workspace not found or has no projects")
        raise HTTPException(status_code=403, detail="Access denied")
    names = {p.id: p.name for p in visible}
    rows = (await session.exec(select(EstateDomain, Estate).join(Estate, Estate.id == EstateDomain.estate_id)
                               .where(Estate.project_id.in_(list(names)),
                                      EstateDomain.cert_expires_at != None)  # noqa: E711
                               .order_by(EstateDomain.cert_expires_at))).all()
    now = datetime.utcnow()
    return [{"domain_id": d.id, "hostname": d.hostname, "cert_expires_at": d.cert_expires_at,
             "days_left": (d.cert_expires_at - now).days, "estate_id": e.id, "estate_name": e.name,
             "project_id": e.project_id, "project_name": names[e.project_id]} for d, e in rows]


# ---------------------------------------------------------------------------
# Monitored pages
# ---------------------------------------------------------------------------

def _require_page_host(project: Project, url: str) -> None:
    allowed = _require_allowlist(project)
    if not host_allowed(url, allowed):
        raise HTTPException(status_code=403, detail="Page host is not on this project's security allowlist")


def _page_data(body: MonitoredPageIn) -> Dict[str, Any]:
    data = body.model_dump()
    data["element_checks"] = [c.model_dump() for c in body.element_checks] if body.element_checks else None
    return data


@router.get("/projects/{project_id}/monitored-pages")
async def list_pages(project_id: int, session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_for(session, principal, project_id)
    pages = (await session.exec(select(MonitoredPage).where(
        MonitoredPage.project_id == project_id).order_by(MonitoredPage.name))).all()
    out = []
    for p in pages:
        counts = (await session.exec(select(
            func.count(PageScript.id),
            func.count(PageScript.id).filter(PageScript.authorized == False)).where(  # noqa: E712
            PageScript.page_id == p.id, PageScript.present == True))).one()  # noqa: E712
        out.append(_page_out(p, {"script_count": counts[0], "unapproved_count": counts[1]}))
    return out


@router.post("/projects/{project_id}/monitored-pages", status_code=201)
async def create_page(project_id: int, body: MonitoredPageIn, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_for(session, principal, project_id, "editor")
    _require_page_host(project, body.url)
    p = MonitoredPage(project_id=project_id, created_by_id=principal.user.id,
                      next_check_at=_next(body.cron_expression), **_page_data(body))
    session.add(p)
    await session.flush()
    await audit_record(session, entity_type="monitored_page", entity_id=p.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": p.name, "url": p.url})
    await session.commit()
    await session.refresh(p)
    return _page_out(p)


@router.get("/monitored-pages/{page_id}")
async def get_page(page_id: int, session: AsyncSession = Depends(get_session),
                   principal: AuthPrincipal = Depends(get_current_principal)):
    p, _ = await _page_for(session, principal, page_id)
    return _page_out(p)


@router.put("/monitored-pages/{page_id}")
async def update_page(page_id: int, body: MonitoredPageIn, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    p, project = await _page_for(session, principal, page_id, "editor")
    _require_page_host(project, body.url)
    data = _page_data(body)
    if data["url"] != p.url:
        # A different page: the old baselines say nothing about it.
        p.content_baseline = p.content_baseline_at = p.headers_baseline = None
    if data["cron_expression"] != p.cron_expression or (data["schedule_enabled"] and not p.schedule_enabled):
        p.next_check_at = _next(data["cron_expression"])
    for k, v in data.items():
        setattr(p, k, v)
    session.add(p)
    await audit_record(session, entity_type="monitored_page", entity_id=p.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={k: v for k, v in data.items() if k != "alert_emails"})
    await session.commit()
    await session.refresh(p)
    return _page_out(p)


@router.delete("/monitored-pages/{page_id}")
async def delete_page(page_id: int, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    p, project = await _page_for(session, principal, page_id, "editor")
    await session.exec(delete(PageScript).where(PageScript.page_id == p.id))
    await session.exec(delete(PageChangeEvent).where(PageChangeEvent.page_id == p.id))
    await session.delete(p)
    await audit_record(session, entity_type="monitored_page", entity_id=page_id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": p.name, "url": p.url})
    await session.commit()
    return {"deleted": page_id}


@router.post("/monitored-pages/{page_id}/check", status_code=202)
async def check_now(page_id: int, request: Request,
                    session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    p, project = await _page_for(session, principal, page_id, "editor")
    _require_page_host(project, p.url)
    await audit_record(session, entity_type="monitored_page", entity_id=p.id, action="check",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"url": p.url})
    await session.commit()
    from app.tasks.estate_tasks import check_monitored_page
    try:
        check_monitored_page.delay(p.id)
    except Exception as exc:  # noqa: BLE001
        print(f"[Estate] failed to queue page check {p.id}: {exc}")
    return {"queued": True, "page_id": p.id}


@router.post("/monitored-pages/{page_id}/baseline/reset")
async def reset_baseline(page_id: int, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    """Accept the page as it is now: the next check records a fresh content
    baseline. Audited — it is how a defacement alert gets silenced."""
    p, project = await _page_for(session, principal, page_id, "editor")
    p.content_baseline = p.content_baseline_at = None
    session.add(p)
    session.add(PageChangeEvent(page_id=p.id, kind="baseline_reset",
                                summary=f"Content baseline reset by {principal.user.email}"))
    await audit_record(session, entity_type="monitored_page", entity_id=p.id, action="reset_baseline",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"url": p.url})
    await session.commit()
    return _page_out(p)


@router.get("/monitored-pages/{page_id}/scripts")
async def list_scripts(page_id: int, include_removed: bool = False,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    p, _ = await _page_for(session, principal, page_id)
    q = select(PageScript).where(PageScript.page_id == p.id)
    if not include_removed:
        q = q.where(PageScript.present == True)  # noqa: E712
    rows = (await session.exec(q)).all()
    # Unapproved first, then third-party, then by URL.
    rows = sorted(rows, key=lambda s: (s.authorized, not s.third_party, s.src or s.script_key))
    return [_script_out(s) for s in rows]


async def _script_for(session, principal, page_id: int, script_id: int):
    p, project = await _page_for(session, principal, page_id, "editor")
    s = await session.get(PageScript, script_id)
    if not s or s.page_id != p.id:
        raise HTTPException(status_code=404, detail="Script not found on this page")
    return p, project, s


@router.post("/monitored-pages/{page_id}/scripts/{script_id}/approve")
async def approve_script(page_id: int, script_id: int, body: ApproveIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    p, project, s = await _script_for(session, principal, page_id, script_id)
    justification = body.justification.strip()
    if len(justification) < 10:
        raise HTTPException(status_code=422, detail="justification must be at least 10 characters")
    now = datetime.utcnow()
    s.authorized, s.justification = True, justification
    s.authorized_by_id, s.authorized_at = principal.user.id, now
    session.add(s)
    session.add(PageChangeEvent(page_id=p.id, kind="script_approved",
                                summary=f"Approved: {s.src or s.script_key}"[:500],
                                detail={"script_id": s.id, "sha384": s.sha384,
                                        "justification": justification,
                                        "by": principal.user.email}))
    # PCI DSS 6.4.3: who authorised which exact content, and why.
    await audit_record(session, entity_type="page_script", entity_id=s.id, action="approve",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"page_id": p.id, "script": s.src or s.script_key,
                                "sha384": s.sha384, "justification": justification})
    await session.commit()
    await session.refresh(s)
    return _script_out(s)


@router.post("/monitored-pages/{page_id}/scripts/{script_id}/revoke")
async def revoke_script(page_id: int, script_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    p, project, s = await _script_for(session, principal, page_id, script_id)
    s.authorized, s.authorized_at, s.authorized_by_id = False, None, None
    session.add(s)
    session.add(PageChangeEvent(page_id=p.id, kind="script_revoked",
                                summary=f"Approval withdrawn: {s.src or s.script_key}"[:500],
                                detail={"script_id": s.id, "by": principal.user.email}))
    await audit_record(session, entity_type="page_script", entity_id=s.id, action="revoke",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"page_id": p.id, "script": s.src or s.script_key})
    await session.commit()
    await session.refresh(s)
    return _script_out(s)


@router.get("/monitored-pages/{page_id}/changes")
async def list_changes(page_id: int, limit: int = Query(100, ge=1, le=500),
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    p, _ = await _page_for(session, principal, page_id)
    rows = (await session.exec(select(PageChangeEvent).where(PageChangeEvent.page_id == p.id)
                               .order_by(PageChangeEvent.created_at.desc(), PageChangeEvent.id.desc())
                               .limit(limit))).all()
    return [{"id": r.id, "kind": r.kind, "summary": r.summary, "detail": r.detail,
             "created_at": r.created_at} for r in rows]
