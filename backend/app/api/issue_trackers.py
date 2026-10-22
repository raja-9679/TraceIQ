"""Issue-tracker configs + ticket creation from runs.

Configs are workspace-scoped (like webhooks/API keys); credentials are stored
Fernet-encrypted and never returned. Ticket creation is dispatched to a Celery
task that also uploads the run's artifacts.

A config is a shared credential plus a URL the backend POSTs run evidence to,
so writing one is a workspace-admin action (as for outbound webhooks), every
write is audited (never the secret), and the base URL goes through net_guard
here and again before every request in app/services/issue_trackers.py.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_principal, AuthPrincipal
from app.core.secrets import encrypt_secret
from app.core.net_guard import UnsafeUrlError, validate_outbound_url
from app.api.policies import require_workspace_admin
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.models import (
    Project, TestCaseResult, UserWorkspace, TestRun,
    IssueTrackerConfig, IssueTrackerConfigCreate, IssueTrackerConfigUpdate, IssueTrackerConfigRead,
    IssueTicket, IssueTicketCreate, IssueTicketRead,
)

router = APIRouter()

_PROVIDERS = {"jira", "itop", "github", "linear"}
# Linear connections exist to import requirements (app/api/requirements.py);
# there is no outbound Linear provider, so ticket creation refuses them up front
# instead of failing later in the Celery task.
OUTBOUND_PROVIDERS = {"jira", "itop", "github"}


async def _ensure_workspace_member(workspace_id: int, user_id: int, session: AsyncSession,
                                   project_id: Optional[int] = None, write: bool = False):
    row = await access_service.workspace_membership(user_id, workspace_id, session,
                                                    project_id=project_id, write=write)
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


async def _check_base_url(provider: str, base_url) -> None:
    """SSRF guard at save time: the URL every send will use (GitHub falls back
    to api.github.com, which needs no check). Send time re-checks."""
    url = (base_url or "").strip()
    if not url:
        if provider in ("jira", "itop"):
            raise HTTPException(status_code=400, detail="base_url is required")
        return
    try:
        await validate_outbound_url(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Tracker URL rejected: {exc}")


def _audit_changes(c: IssueTrackerConfig, secret_changed: bool = False) -> dict:
    out = {"name": c.name, "provider": c.provider, "base_url": c.base_url,
           "enabled": c.enabled}
    if secret_changed:
        out["credential"] = "changed"     # never the value
    return out


def _to_read(c: IssueTrackerConfig) -> IssueTrackerConfigRead:
    return IssueTrackerConfigRead(
        id=c.id, workspace_id=c.workspace_id, provider=c.provider, name=c.name,
        base_url=c.base_url, auth_user=c.auth_user, settings=c.settings,
        enabled=c.enabled, created_at=c.created_at)


@router.post("/workspaces/{workspace_id}/issue-trackers", response_model=IssueTrackerConfigRead)
async def create_tracker(workspace_id: int, body: IssueTrackerConfigCreate,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    if body.provider.lower() not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {sorted(_PROVIDERS)}")
    if not body.auth_secret:
        raise HTTPException(status_code=400, detail="auth_secret is required")
    base_url = body.base_url
    if body.provider.lower() == "linear" and not (base_url or "").strip():
        from app.services.tracker_import import LINEAR_DEFAULT_URL
        base_url = LINEAR_DEFAULT_URL
    await _check_base_url(body.provider.lower(), base_url)
    config = IssueTrackerConfig(
        workspace_id=workspace_id, provider=body.provider.lower(), name=body.name,
        base_url=base_url, auth_user=body.auth_user,
        auth_secret_encrypted=encrypt_secret(body.auth_secret),
        settings=body.settings, enabled=body.enabled, created_by_id=principal.user.id)
    session.add(config)
    await session.flush()
    await audit_record(session, entity_type="issue_tracker", entity_id=config.id,
                       action="create", workspace_id=workspace_id, principal=principal,
                       changes=_audit_changes(config))
    await session.commit()
    await session.refresh(config)
    return _to_read(config)


@router.get("/workspaces/{workspace_id}/issue-trackers", response_model=List[IssueTrackerConfigRead])
async def list_trackers(workspace_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    rows = (await session.exec(
        select(IssueTrackerConfig).where(IssueTrackerConfig.workspace_id == workspace_id))).all()
    return [_to_read(c) for c in rows]


@router.patch("/workspaces/{workspace_id}/issue-trackers/{config_id}", response_model=IssueTrackerConfigRead)
async def update_tracker(workspace_id: int, config_id: int, body: IssueTrackerConfigUpdate,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    config = await session.get(IssueTrackerConfig, config_id)
    if not config or config.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracker not found")
    data = body.model_dump(exclude_unset=True)
    if "base_url" in data:
        await _check_base_url(config.provider, data["base_url"])
    secret_changed = False
    if "auth_secret" in data:
        secret = data.pop("auth_secret")
        if secret:
            config.auth_secret_encrypted = encrypt_secret(secret)
            secret_changed = True
    for k, v in data.items():
        setattr(config, k, v)
    session.add(config)
    await audit_record(session, entity_type="issue_tracker", entity_id=config.id,
                       action="update", workspace_id=workspace_id, principal=principal,
                       changes=_audit_changes(config, secret_changed))
    await session.commit()
    await session.refresh(config)
    return _to_read(config)


@router.delete("/workspaces/{workspace_id}/issue-trackers/{config_id}")
async def delete_tracker(workspace_id: int, config_id: int,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    config = await session.get(IssueTrackerConfig, config_id)
    if not config or config.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracker not found")
    await audit_record(session, entity_type="issue_tracker", entity_id=config.id,
                       action="delete", workspace_id=workspace_id, principal=principal,
                       changes=_audit_changes(config))
    # Imported requirements outlive the connection they came from (they are
    # the project's records); they just can't be re-synced until reattached.
    from sqlalchemy import update as sa_update
    from app.models import Requirement
    await session.exec(sa_update(Requirement)
                       .where(Requirement.tracker_config_id == config_id)
                       .values(tracker_config_id=None))
    await session.delete(config)
    await session.commit()
    return {"status": "deleted"}


def _default_summary(run: TestRun) -> str:
    label = run.suite_name or run.test_case_name or f"Run #{run.id}"
    return f"[TraceIQ] {label} — {run.status.value if hasattr(run.status, 'value') else run.status}"


def _default_description(run: TestRun) -> str:
    lines = [
        f"TraceIQ run #{run.id} — status **{run.status.value if hasattr(run.status, 'value') else run.status}**",
        f"Passed {run.passed_tests}/{run.total_tests}, failed {run.failed_tests}.",
    ]
    if run.error_message:
        lines.append(f"\nError: {run.error_message}")
    if run.git_commit:
        lines.append(f"\nCommit: {run.git_commit}" + (f" ({run.git_branch})" if run.git_branch else ""))
    if run.git_pr_url:
        lines.append(f"PR: {run.git_pr_url}")
    return "\n".join(lines)


@router.post("/runs/{run_id}/tickets", response_model=IssueTicketRead, status_code=202)
async def create_ticket(run_id: int, body: IssueTicketCreate,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(principal.user.id, run.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")

    config = await session.get(IssueTrackerConfig, body.config_id)
    project = await session.get(Project, run.project_id) if run.project_id else None
    # The tracker must belong to the run's own workspace (as for triage
    # tickets): otherwise a member of two workspaces could send one
    # workspace's run evidence to the other's tracker. 404 so the call doesn't
    # confirm another workspace's config id exists.
    if not config or not project or config.workspace_id != project.workspace_id:
        raise HTTPException(status_code=404, detail="Tracker config not found")
    await _ensure_workspace_member(config.workspace_id, principal.user.id, session,
                                   project_id=project.id, write=True)
    if body.result_id is not None:
        result = await session.get(TestCaseResult, body.result_id)
        if not result or result.test_run_id != run_id:
            raise HTTPException(status_code=404, detail="Result not found in this run")
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Tracker is disabled")
    from app.services.demo_workspace import DEMO_NO_OUTBOUND, is_demo_workspace
    if await is_demo_workspace(session, config.workspace_id):
        raise HTTPException(status_code=409, detail=DEMO_NO_OUTBOUND)
    if config.provider not in OUTBOUND_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"{config.provider} connections are import-only")

    summary = body.summary or _default_summary(run)
    description = body.description or _default_description(run)

    ticket = IssueTicket(
        config_id=config.id, workspace_id=config.workspace_id, run_id=run_id,
        result_id=body.result_id, provider=config.provider, summary=summary,
        status="pending", created_by_id=principal.user.id)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    try:
        from app.tasks.ticket_tasks import create_issue_ticket
        create_issue_ticket.delay(
            ticket.id, description=description, priority=body.priority,
            attach_trace=body.attach_trace, attach_video=body.attach_video,
            attach_screenshots=body.attach_screenshots)
    except Exception as e:  # noqa: BLE001
        ticket.status = "error"
        ticket.error = f"Could not queue: {e}"
        session.add(ticket)
        await session.commit()

    return IssueTicketRead.model_validate(ticket, from_attributes=True)


@router.get("/runs/{run_id}/tickets", response_model=List[IssueTicketRead])
async def list_tickets(run_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(principal.user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    rows = (await session.exec(
        select(IssueTicket).where(IssueTicket.run_id == run_id)
        .order_by(IssueTicket.created_at.desc()))).all()
    return [IssueTicketRead.model_validate(r, from_attributes=True) for r in rows]
