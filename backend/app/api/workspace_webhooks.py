"""Outbound webhook registry — workspace-scoped fan-out for run events.

Surface (workspace admin; project-scoped API keys refused — WP15f):
    POST   /api/workspaces/{workspace_id}/webhooks       create
    GET    /api/workspaces/{workspace_id}/webhooks       list
    PATCH  /api/workspaces/{workspace_id}/webhooks/{id}  enable/disable
    DELETE /api/workspaces/{workspace_id}/webhooks/{id}  delete
    GET    /api/workspaces/{workspace_id}/webhooks/{id}/deliveries          delivery log
    POST   /api/workspaces/{workspace_id}/webhooks/{id}/deliveries/{d}/redeliver

Admin, not member: a webhook receives every run of the workspace (results,
error text, artifact links), and its URL is often itself a credential
(Slack-style URLs carry the token in the path). Any member used to be able
to add one and read them all.

Webhook secrets are generated server-side and returned once at create time
(same model as API keys). They are used to HMAC-SHA256 sign outbound
payloads — recipients verify the signature in `X-TraceIQ-Signature`.
"""
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.services import webhook_delivery as wd
from app.services.audit import record as audit_record
from app.api.policies import require_workspace_admin
from app.models import (
    Project,
    WebhookDelivery,
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
    await require_workspace_admin(session, principal, workspace_id)
    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None or project.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Project not found in this workspace")

    # SSRF guard: this URL is later POSTed to by a Celery task. Validate at
    # registration so a workspace admin can't point it at cloud metadata or an
    # internal service. (Send-time re-validation in the dispatch task is the
    # stronger control against DNS rebinding — see TODO in outbound_webhook_tasks.)
    from app.core.net_guard import validate_outbound_url, UnsafeUrlError
    try:
        await validate_outbound_url(body.url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Webhook URL rejected: {exc}")

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
    await session.flush()
    await audit_record(session, entity_type="workspace_webhook", entity_id=webhook.id,
                       action="create", workspace_id=workspace_id, principal=principal,
                       changes={"name": body.name, "project_id": body.project_id,
                                "event_filter": body.event_filter})
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
    await require_workspace_admin(session, principal, workspace_id)
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
    await require_workspace_admin(session, principal, workspace_id)

    webhook = await session.get(WorkspaceWebhook, webhook_id)
    if not webhook or webhook.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await audit_record(session, entity_type="workspace_webhook", entity_id=webhook_id,
                       action="update", workspace_id=workspace_id, principal=principal,
                       changes=body.model_dump(exclude_none=True))
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
    await require_workspace_admin(session, principal, workspace_id)

    webhook = await session.get(WorkspaceWebhook, webhook_id)
    if not webhook or webhook.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    from sqlalchemy import delete as sa_delete
    await session.exec(sa_delete(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id))
    await audit_record(session, entity_type="workspace_webhook", entity_id=webhook_id,
                       action="delete", workspace_id=workspace_id, principal=principal,
                       changes={"name": webhook.name})
    await session.delete(webhook)
    await session.commit()
    return {"status": "deleted", "id": webhook_id}


@router.get("/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries")
async def list_deliveries(
    workspace_id: int,
    webhook_id: int,
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, pattern="^(pending|ok|failed)$"),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Newest attempts first. Metadata only — never the payload or the URL."""
    await require_workspace_admin(session, principal, workspace_id)
    webhook = await session.get(WorkspaceWebhook, webhook_id)
    if not webhook or webhook.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    stmt = (select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id,
                   WebhookDelivery.workspace_id == workspace_id)
            .order_by(WebhookDelivery.id.desc()).limit(limit))
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    rows = (await session.exec(stmt)).all()
    return {"webhook_id": webhook_id, "retention_days": 30,
            "deliveries": [wd.to_read(r) for r in rows]}


@router.post("/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries/{delivery_row_id}/redeliver",
             status_code=202)
async def redeliver(
    workspace_id: int,
    webhook_id: int,
    delivery_row_id: int,
    request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Send a logged delivery again: same body and delivery id, freshly
    signed, as a new attempt row. Works on a disabled webhook too (testing a
    receiver before re-enabling it). Audited."""
    await require_workspace_admin(session, principal, workspace_id)
    webhook = await session.get(WorkspaceWebhook, webhook_id)
    row = await session.get(WebhookDelivery, delivery_row_id)
    if (not webhook or webhook.workspace_id != workspace_id or row is None
            or row.webhook_id != webhook_id or row.workspace_id != workspace_id):
        raise HTTPException(status_code=404, detail="Delivery not found")
    await audit_record(session, entity_type="workspace_webhook", entity_id=webhook_id,
                       action="redeliver", workspace_id=workspace_id, principal=principal,
                       request=request,
                       changes={"delivery_id": row.delivery_id, "event": row.event, "run_id": row.run_id})
    await session.commit()
    from app.tasks.outbound_webhook_tasks import redeliver_webhook_delivery
    redeliver_webhook_delivery.delay(delivery_row_id)
    return {"status": "queued", "delivery_id": row.delivery_id}
