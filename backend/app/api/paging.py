"""Project paging channels, maintenance windows and egress policy (WP8).

    GET/POST        /api/projects/{id}/paging-channels
    PUT/DELETE      /api/projects/{id}/paging-channels/{channel_id}
    POST            /api/projects/{id}/paging-channels/{channel_id}/test
    GET             /api/projects/{id}/paging-events
    GET/POST        /api/projects/{id}/maintenance-windows
    PUT/DELETE      /api/projects/{id}/maintenance-windows/{window_id}
    GET/PUT         /api/projects/{id}/network-policy

Reads are viewer-level and never return a routing key. Writes are project
ADMIN: a paging channel decides who gets woken up, a maintenance window
decides who does not, and an egress policy decides what the project's browser
may reach — none of that is test editing. Every write is audited.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.secrets import encrypt_secret
from app.models import MaintenanceWindow, PagingChannel, PagingEvent, Project
from app.services import paging as paging_svc
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="This API key is not scoped to this project")
    if not await access_service.has_project_access(
            principal.user.id, project_id, session, min_role=min_role):
        raise HTTPException(
            status_code=403,
            detail="Access denied" if min_role == "viewer"
            else f"{min_role.capitalize()} role required")
    return project


# --------------------------------------------------------------------------
# Paging channels
# --------------------------------------------------------------------------

class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    # Routing key (PagerDuty) / API key (Opsgenie). Required on create; on
    # update an empty/omitted value keeps the stored one.
    secret: Optional[str] = Field(default=None, max_length=512)
    api_region: Optional[str] = None
    severity: str = "error"
    on_monitor: bool = True
    on_run_failure: bool = False
    enabled: bool = True


def _channel_out(c: PagingChannel) -> Dict[str, Any]:
    return {"id": c.id, "project_id": c.project_id, "name": c.name, "kind": c.kind,
            "api_region": c.api_region, "severity": c.severity,
            "on_monitor": c.on_monitor, "on_run_failure": c.on_run_failure,
            "enabled": c.enabled, "last_sent_at": c.last_sent_at,
            "last_error": c.last_error, "created_at": c.created_at,
            "secret_set": bool(c.secret_encrypted)}


def _validate_channel(body: ChannelIn) -> None:
    if body.kind not in paging_svc.KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {paging_svc.KINDS}")
    if body.severity not in paging_svc.SEVERITIES:
        raise HTTPException(status_code=400,
                            detail=f"severity must be one of {paging_svc.SEVERITIES}")
    if body.kind == "opsgenie" and (body.api_region or "us") not in paging_svc.OPSGENIE_ALERTS_URL:
        raise HTTPException(status_code=400, detail="api_region must be 'us' or 'eu'")


@router.get("/projects/{project_id}/paging-channels")
async def list_channels(project_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(project_id, principal, session)
    rows = (await session.exec(select(PagingChannel).where(
        PagingChannel.project_id == project_id).order_by(PagingChannel.id))).all()
    return [_channel_out(c) for c in rows]


@router.post("/projects/{project_id}/paging-channels", status_code=201)
async def create_channel(project_id: int, body: ChannelIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    _validate_channel(body)
    if not (body.secret or "").strip():
        raise HTTPException(status_code=400, detail="secret (routing key / API key) is required")
    channel = PagingChannel(
        project_id=project_id, name=body.name.strip(), kind=body.kind,
        secret_encrypted=encrypt_secret(body.secret.strip()),
        api_region=(body.api_region or "us") if body.kind == "opsgenie" else None,
        severity=body.severity, on_monitor=body.on_monitor,
        on_run_failure=body.on_run_failure, enabled=body.enabled,
        created_by_id=principal.user.id)
    session.add(channel)
    await session.flush()
    await audit_record(session, entity_type="paging_channel", entity_id=channel.id,
                       action="create", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes={"name": channel.name, "kind": channel.kind,
                                "project_id": project_id,
                                "on_monitor": channel.on_monitor,
                                "on_run_failure": channel.on_run_failure})
    await session.commit()
    await session.refresh(channel)
    return _channel_out(channel)


async def _channel(project_id: int, channel_id: int, session: AsyncSession) -> PagingChannel:
    channel = await session.get(PagingChannel, channel_id)
    if not channel or channel.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paging channel not found")
    return channel


@router.put("/projects/{project_id}/paging-channels/{channel_id}")
async def update_channel(project_id: int, channel_id: int, body: ChannelIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    channel = await _channel(project_id, channel_id, session)
    _validate_channel(body)
    before = _channel_out(channel)
    channel.name = body.name.strip()
    channel.kind = body.kind
    channel.api_region = (body.api_region or "us") if body.kind == "opsgenie" else None
    channel.severity = body.severity
    channel.on_monitor = body.on_monitor
    channel.on_run_failure = body.on_run_failure
    channel.enabled = body.enabled
    rotated = bool((body.secret or "").strip())
    if rotated:
        channel.secret_encrypted = encrypt_secret(body.secret.strip())
    session.add(channel)
    after = _channel_out(channel)
    changes = {k: {"from": before[k], "to": after[k]} for k in
               ("name", "kind", "api_region", "severity", "on_monitor",
                "on_run_failure", "enabled") if before[k] != after[k]}
    if rotated:
        changes["secret"] = "rotated"
    await audit_record(session, entity_type="paging_channel", entity_id=channel.id,
                       action="update", workspace_id=project.workspace_id,
                       principal=principal, request=request, changes=changes)
    await session.commit()
    await session.refresh(channel)
    return _channel_out(channel)


@router.delete("/projects/{project_id}/paging-channels/{channel_id}", status_code=204)
async def delete_channel(project_id: int, channel_id: int, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    channel = await _channel(project_id, channel_id, session)
    await audit_record(session, entity_type="paging_channel", entity_id=channel.id,
                       action="delete", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes={"name": channel.name, "kind": channel.kind})
    # PagingEvent.channel_id is a soft reference: the log outlives the channel.
    await session.delete(channel)
    await session.commit()


@router.post("/projects/{project_id}/paging-channels/{channel_id}/test")
async def test_channel(project_id: int, channel_id: int,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    """Send a trigger and an immediate resolve under a one-off dedup key, so
    the test proves the key works without leaving an incident open."""
    import asyncio
    from app.core.secrets import decrypt_secret
    await _project(project_id, principal, session, "admin")
    channel = await _channel(project_id, channel_id, session)
    key = f"traceiq:test:{channel.id}:{int(datetime.utcnow().timestamp())}"
    secret = decrypt_secret(channel.secret_encrypted)
    try:
        for action in ("trigger", "resolve"):
            await asyncio.to_thread(
                paging_svc.send, channel, secret, action, key,
                summary="TraceIQ test page — safe to ignore",
                details={"project_id": project_id})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300]}
    return {"ok": True, "dedup_key": key}


@router.get("/projects/{project_id}/paging-events")
async def list_paging_events(project_id: int, limit: int = Query(50, ge=1, le=500),
                             session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(project_id, principal, session)
    rows = (await session.exec(select(PagingEvent).where(
        PagingEvent.project_id == project_id)
        .order_by(PagingEvent.created_at.desc(), PagingEvent.id.desc()).limit(limit))).all()
    return [r.model_dump() for r in rows]


# --------------------------------------------------------------------------
# Maintenance windows
# --------------------------------------------------------------------------

class WindowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    weekdays: Optional[List[int]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    timezone: str = "UTC"
    enabled: bool = True


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is None:
        return value
    from datetime import timezone
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _window_fields(body: WindowIn) -> Dict[str, Any]:
    fields = {
        "name": body.name.strip(),
        "starts_at": _naive_utc(body.starts_at), "ends_at": _naive_utc(body.ends_at),
        "weekdays": sorted(set(body.weekdays)) if body.weekdays else None,
        "start_time": body.start_time or None, "end_time": body.end_time or None,
        "timezone": (body.timezone or "UTC").strip(), "enabled": body.enabled,
    }
    try:
        paging_svc.validate_window(
            starts_at=fields["starts_at"], ends_at=fields["ends_at"],
            weekdays=fields["weekdays"], start_time=fields["start_time"],
            end_time=fields["end_time"], timezone=fields["timezone"])
    except paging_svc.WindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return fields


def _window_out(w: MaintenanceWindow, now: Optional[datetime] = None) -> Dict[str, Any]:
    out = w.model_dump()
    out["active_now"] = paging_svc.window_active(w, now or datetime.utcnow())
    return out


@router.get("/projects/{project_id}/maintenance-windows")
async def list_windows(project_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(project_id, principal, session)
    rows = (await session.exec(select(MaintenanceWindow).where(
        MaintenanceWindow.project_id == project_id).order_by(MaintenanceWindow.id))).all()
    now = datetime.utcnow()
    return [_window_out(w, now) for w in rows]


@router.post("/projects/{project_id}/maintenance-windows", status_code=201)
async def create_window(project_id: int, body: WindowIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    window = MaintenanceWindow(project_id=project_id, created_by_id=principal.user.id,
                               **_window_fields(body))
    session.add(window)
    await session.flush()
    await audit_record(session, entity_type="maintenance_window", entity_id=window.id,
                       action="create", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes=window.model_dump(mode="json", exclude={"id", "created_at"}))
    await session.commit()
    await session.refresh(window)
    return _window_out(window)


@router.put("/projects/{project_id}/maintenance-windows/{window_id}")
async def update_window(project_id: int, window_id: int, body: WindowIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    window = await session.get(MaintenanceWindow, window_id)
    if not window or window.project_id != project_id:
        raise HTTPException(status_code=404, detail="Maintenance window not found")
    before = window.model_dump(mode="json")
    for key, value in _window_fields(body).items():
        setattr(window, key, value)
    session.add(window)
    after = window.model_dump(mode="json")
    await audit_record(session, entity_type="maintenance_window", entity_id=window.id,
                       action="update", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes={k: {"from": before[k], "to": after[k]}
                                for k in after if before.get(k) != after[k]})
    await session.commit()
    await session.refresh(window)
    return _window_out(window)


@router.delete("/projects/{project_id}/maintenance-windows/{window_id}", status_code=204)
async def delete_window(project_id: int, window_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session, "admin")
    window = await session.get(MaintenanceWindow, window_id)
    if not window or window.project_id != project_id:
        raise HTTPException(status_code=404, detail="Maintenance window not found")
    await audit_record(session, entity_type="maintenance_window", entity_id=window.id,
                       action="delete", workspace_id=project.workspace_id,
                       principal=principal, request=request, changes={"name": window.name})
    await session.delete(window)
    await session.commit()


# --------------------------------------------------------------------------
# Egress (network) policy
# --------------------------------------------------------------------------

def _describe_network(project: Project) -> Dict[str, Any]:
    from app.services.network_policy import NetworkPolicy, effective_for_project
    try:
        stored = NetworkPolicy(**(project.network_policy or {})).model_dump()
    except Exception:
        stored = None
    return {"stored": stored, "effective": effective_for_project(project)}


@router.get("/projects/{project_id}/network-policy")
async def get_network_policy(project_id: int, session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(project_id, principal, session)
    return _describe_network(project)


@router.put("/projects/{project_id}/network-policy")
async def put_network_policy(project_id: int, body: Dict[str, Any], request: Request,
                             session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)):
    from app.services.network_policy import NetworkPolicy
    project = await _project(project_id, principal, session, "admin")
    try:
        policy = NetworkPolicy(**{**(project.network_policy or {}), **(body or {})})
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError / ValueError
        raise HTTPException(status_code=422, detail=str(exc)[:500])
    before = project.network_policy
    project.network_policy = policy.model_dump()      # reassign: JSON column
    session.add(project)
    await audit_record(session, entity_type="project", entity_id=project_id,
                       action="update_network_policy", workspace_id=project.workspace_id,
                       principal=principal, request=request,
                       changes={"from": before, "to": project.network_policy})
    await session.commit()
    await session.refresh(project)
    return _describe_network(project)
