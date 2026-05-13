"""Outbound webhook registry — workspace-scoped fan-out for run events.

Surface:
    POST   /api/workspaces/{workspace_id}/webhooks       create
    GET    /api/workspaces/{workspace_id}/webhooks       list
    PATCH  /api/workspaces/{workspace_id}/webhooks/{id}  enable/disable
    DELETE /api/workspaces/{workspace_id}/webhooks/{id}  delete

Webhook secrets are generated server-side and returned once at create time
(same model as API keys). They are used to HMAC-SHA256 sign outbound
payloads — recipients verify the signature in `X-TraceIQ-Signature`.
"""
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    UserWorkspace,
    WorkspaceWebhook,
    WorkspaceWebhookCreate,
    WorkspaceWebhookRead,
)

router = APIRouter()


class WebhookCreateResponse(WorkspaceWebhookRead):
    secret: str


class WebhookPatch(BaseModel):
    is_active: Optional[bool] = None
    event_filter: Optional[str] = None
    name: Optional[str] = None


async def _ensure_workspace_member(workspace_id: int, user_id: int, session: AsyncSession) -> UserWorkspace:
    res = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.user_id == user_id,
        )
    )
    membership = res.first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return membership


def _to_read(w: WorkspaceWebhook) -> WorkspaceWebhookRead:
    return WorkspaceWebhookRead(
        id=w.id,
        workspace_id=w.workspace_id,
        project_id=w.project_id,
        name=w.name,
        url=w.url,
        event_filter=w.event_filter,
        is_active=w.is_active,
        created_at=w.created_at,
        last_delivery_at=w.last_delivery_at,
        last_delivery_status=w.last_delivery_status,
        failure_count=w.failure_count,
    )


@router.post(
    "/workspaces/{workspace_id}/webhooks",
    response_model=WebhookCreateResponse,
)
async def create_webhook(
    workspace_id: int,
    body: WorkspaceWebhookCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> WebhookCreateResponse:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id mismatch")
    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    secret = secrets.token_urlsafe(32)
    webhook = WorkspaceWebhook(
        workspace_id=workspace_id,
        project_id=body.project_id,
        name=body.name,
        url=body.url,
        secret=secret,
        event_filter=body.event_filter,
        created_by_id=principal.user.id,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)

    return WebhookCreateResponse(
        **_to_read(webhook).model_dump(),
        secret=secret,
    )


@router.get(
    "/workspaces/{workspace_id}/webhooks",
    response_model=List[WorkspaceWebhookRead],
)
async def list_webhooks(
    workspace_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[WorkspaceWebhookRead]:
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    res = await session.exec(
        select(WorkspaceWebhook)
        .where(WorkspaceWebhook.workspace_id == workspace_id)
        .order_by(WorkspaceWebhook.created_at.desc())
    )
    return [_to_read(w) for w in res.all()]


@router.patch(
    "/workspaces/{workspace_id}/webhooks/{webhook_id}",
    response_model=WorkspaceWebhookRead,
)
async def patch_webhook(
    workspace_id: int,
    webhook_id: int,
    body: WebhookPatch,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceWebhookRead:
    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    webhook = await session.get(WorkspaceWebhook, webhook_id)
    if not webhook or webhook.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if body.is_active is not None:
        webhook.is_active = body.is_active
    if body.event_filter is not None:
        webhook.event_filter = body.event_filter
    if body.name is not None:
        webhook.name = body.name
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return _to_read(webhook)


@router.delete("/workspaces/{workspace_id}/webhooks/{webhook_id}")
async def delete_webhook(
    workspace_id: int,
    webhook_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    webhook = await session.get(WorkspaceWebhook, webhook_id)
    if not webhook or webhook.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await session.delete(webhook)
    await session.commit()
    return {"status": "deleted", "id": webhook_id}
