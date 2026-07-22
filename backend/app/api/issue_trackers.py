"""Issue-tracker configs + ticket creation from runs.

Configs are workspace-scoped (like webhooks/API keys); credentials are stored
Fernet-encrypted and never returned. Ticket creation is dispatched to a Celery
task that also uploads the run's artifacts.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_principal, AuthPrincipal
from app.core.secrets import encrypt_secret
from app.services.access_service import access_service
from app.models import (
    UserWorkspace, TestRun,
    IssueTrackerConfig, IssueTrackerConfigCreate, IssueTrackerConfigUpdate, IssueTrackerConfigRead,
    IssueTicket, IssueTicketCreate, IssueTicketRead,
)

router = APIRouter()

_PROVIDERS = {"jira", "itop", "github"}


async def _ensure_workspace_member(workspace_id: int, user_id: int, session: AsyncSession):
    row = (await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.user_id == user_id))).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


def _to_read(c: IssueTrackerConfig) -> IssueTrackerConfigRead:
    return IssueTrackerConfigRead(
        id=c.id, workspace_id=c.workspace_id, provider=c.provider, name=c.name,
        base_url=c.base_url, auth_user=c.auth_user, settings=c.settings,
        enabled=c.enabled, created_at=c.created_at)


@router.post("/workspaces/{workspace_id}/issue-trackers", response_model=IssueTrackerConfigRead)
async def create_tracker(workspace_id: int, body: IssueTrackerConfigCreate,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    if body.provider.lower() not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {sorted(_PROVIDERS)}")
    if not body.auth_secret:
        raise HTTPException(status_code=400, detail="auth_secret is required")
    config = IssueTrackerConfig(
        workspace_id=workspace_id, provider=body.provider.lower(), name=body.name,
        base_url=body.base_url, auth_user=body.auth_user,
        auth_secret_encrypted=encrypt_secret(body.auth_secret),
        settings=body.settings, enabled=body.enabled, created_by_id=principal.user.id)
    session.add(config)
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
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    config = await session.get(IssueTrackerConfig, config_id)
    if not config or config.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracker not found")
    data = body.model_dump(exclude_unset=True)
    if "auth_secret" in data:
        secret = data.pop("auth_secret")
        if secret:
            config.auth_secret_encrypted = encrypt_secret(secret)
    for k, v in data.items():
        setattr(config, k, v)
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return _to_read(config)


@router.delete("/workspaces/{workspace_id}/issue-trackers/{config_id}")
async def delete_tracker(workspace_id: int, config_id: int,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    config = await session.get(IssueTrackerConfig, config_id)
    if not config or config.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracker not found")
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
    if not config:
        raise HTTPException(status_code=404, detail="Tracker config not found")
    await _ensure_workspace_member(config.workspace_id, principal.user.id, session)
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Tracker is disabled")

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
