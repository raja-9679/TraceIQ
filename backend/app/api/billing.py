"""Billing / plans / usage API (PLATFORM_VISION.md — commercial readiness).

Works in two modes:
- **Manual** (default): plans exist and quotas are enforced; a workspace admin
  assigns a plan directly. No Stripe needed.
- **Stripe** (when STRIPE_SECRET_KEY is set): checkout + webhook drive the
  subscription. The endpoints are present but return 501 until configured.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Body, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.config import settings
from app.services import billing as billing_svc
from app.models import (
    User, UserWorkspace, Plan, PlanRead, WorkspaceSubscription, BillingStatus,
)

router = APIRouter()


async def _member(session: AsyncSession, workspace_id: int, user_id: int) -> UserWorkspace:
    row = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.workspace_id == workspace_id,
        UserWorkspace.user_id == user_id))).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return row


@router.get("/plans", response_model=List[PlanRead])
async def list_plans(session: AsyncSession = Depends(get_session),
                     current_user: User = Depends(get_current_user)):
    return (await session.exec(select(Plan).where(Plan.is_active == True))).all()  # noqa: E712


@router.get("/workspaces/{workspace_id}/billing", response_model=BillingStatus)
async def workspace_billing(workspace_id: int, session: AsyncSession = Depends(get_session),
                            current_user: User = Depends(get_current_user)):
    await _member(session, workspace_id, current_user.id)
    plan, sub = await billing_svc.resolve_plan(session, workspace_id)
    if not plan:
        raise HTTPException(status_code=500, detail="No plans configured")
    period = billing_svc.current_period()
    limits = plan.limits or {}
    usage = {
        "runs": await billing_svc.get_usage(session, workspace_id, "runs", period),
        "ai_generations": await billing_svc.get_usage(session, workspace_id, "ai_generations", period),
        "llm_tokens": await billing_svc.get_usage(session, workspace_id, "llm_tokens", period),
    }
    return BillingStatus(
        workspace_id=workspace_id, plan=PlanRead(**plan.model_dump()),
        status=sub.status if sub else "active", period=period,
        usage=usage, limits=limits,
        current_period_end=sub.current_period_end if sub else None,
        stripe_configured=bool(settings.STRIPE_SECRET_KEY))


@router.post("/workspaces/{workspace_id}/subscription", response_model=BillingStatus)
async def assign_plan(workspace_id: int, body: dict = Body(...),
                      session: AsyncSession = Depends(get_session),
                      current_user: User = Depends(get_current_user)):
    """Manually assign a plan to a workspace (admin). The Stripe path uses
    checkout + webhook instead."""
    member = await _member(session, workspace_id, current_user.id)
    if (member.role or "").lower() not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Workspace admin required to change plan")
    plan_name = body.get("plan_name")
    plan = (await session.exec(select(Plan).where(Plan.name == plan_name))).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Unknown plan '{plan_name}'")

    sub = (await session.exec(select(WorkspaceSubscription).where(
        WorkspaceSubscription.workspace_id == workspace_id))).first()
    if sub:
        sub.plan_id = plan.id
        sub.status = "active"
        sub.updated_at = datetime.utcnow()
    else:
        sub = WorkspaceSubscription(workspace_id=workspace_id, plan_id=plan.id, status="active")
    session.add(sub)
    await session.commit()
    return await workspace_billing(workspace_id, session, current_user)


@router.post("/billing/stripe/checkout")
async def stripe_checkout(body: dict = Body(...), session: AsyncSession = Depends(get_session),
                          current_user: User = Depends(get_current_user)):
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured on this deployment")
    workspace_id = body.get("workspace_id")
    plan_name = body.get("plan_name")
    await _member(session, workspace_id, current_user.id)
    plan = (await session.exec(select(Plan).where(Plan.name == plan_name))).first()
    if not plan or not plan.stripe_price_id:
        raise HTTPException(status_code=400, detail="Plan has no Stripe price configured")
    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        cs = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            success_url=settings.STRIPE_SUCCESS_URL,
            cancel_url=settings.STRIPE_CANCEL_URL,
            metadata={"workspace_id": str(workspace_id), "plan_id": str(plan.id)},
        )
        return {"checkout_url": cs.url}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")


@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Stripe is not configured")
    import stripe
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {e}")

    obj = event["data"]["object"]
    etype = event["type"]
    if etype == "checkout.session.completed":
        md = obj.get("metadata", {})
        ws_id, plan_id = md.get("workspace_id"), md.get("plan_id")
        if ws_id and plan_id:
            sub = (await session.exec(select(WorkspaceSubscription).where(
                WorkspaceSubscription.workspace_id == int(ws_id)))).first()
            if not sub:
                sub = WorkspaceSubscription(workspace_id=int(ws_id), plan_id=int(plan_id))
            sub.plan_id = int(plan_id)
            sub.status = "active"
            sub.stripe_customer_id = obj.get("customer")
            sub.stripe_subscription_id = obj.get("subscription")
            sub.updated_at = datetime.utcnow()
            session.add(sub)
            await session.commit()
    elif etype in ("customer.subscription.deleted", "customer.subscription.updated"):
        stripe_sub_id = obj.get("id")
        sub = (await session.exec(select(WorkspaceSubscription).where(
            WorkspaceSubscription.stripe_subscription_id == stripe_sub_id))).first()
        if sub:
            sub.status = "canceled" if etype.endswith("deleted") else obj.get("status", sub.status)
            sub.updated_at = datetime.utcnow()
            session.add(sub)
            await session.commit()
    return {"received": True}
