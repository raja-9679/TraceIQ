"""Billing / metering service (PLATFORM_VISION.md — commercial readiness).

Resolves a workspace's plan (defaulting to free), meters usage per calendar
month, and answers quota checks. Enforcement lives at the call sites (e.g. run
creation); this module is the source of truth for limits and counters.

Limits use 0 = unlimited. Plans are seeded by the billing migration.
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Plan, WorkspaceSubscription, UsageRecord, Project

# metric → the plan-limit key it is capped by.
_METRIC_LIMIT = {"runs": "monthly_runs", "ai_generations": "ai_daily"}


def current_period() -> str:
    return datetime.utcnow().strftime("%Y-%m")


async def _free_plan(session: AsyncSession) -> Optional[Plan]:
    return (await session.exec(select(Plan).where(Plan.name == "free"))).first()


async def resolve_plan(session: AsyncSession, workspace_id: int) -> Tuple[Optional[Plan], Optional[WorkspaceSubscription]]:
    sub = (await session.exec(
        select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id))).first()
    if sub:
        plan = await session.get(Plan, sub.plan_id)
        if plan:
            return plan, sub
    return await _free_plan(session), sub


async def get_limits(session: AsyncSession, workspace_id: int) -> dict:
    plan, _ = await resolve_plan(session, workspace_id)
    return (plan.limits if plan else {}) or {}


async def get_usage(session: AsyncSession, workspace_id: int, metric: str, period: Optional[str] = None) -> int:
    period = period or current_period()
    rec = (await session.exec(
        select(UsageRecord).where(
            UsageRecord.workspace_id == workspace_id,
            UsageRecord.period == period,
            UsageRecord.metric == metric))).first()
    return rec.count if rec else 0


async def record_usage(session: AsyncSession, workspace_id: int, metric: str, n: int = 1) -> None:
    period = current_period()
    rec = (await session.exec(
        select(UsageRecord).where(
            UsageRecord.workspace_id == workspace_id,
            UsageRecord.period == period,
            UsageRecord.metric == metric))).first()
    if rec:
        rec.count += n
    else:
        rec = UsageRecord(workspace_id=workspace_id, period=period, metric=metric, count=n)
    session.add(rec)
    await session.commit()


async def check_quota(session: AsyncSession, workspace_id: int, metric: str) -> Tuple[bool, int, int]:
    """Return (allowed, used, limit). limit 0 = unlimited (always allowed)."""
    limits = await get_limits(session, workspace_id)
    limit = int(limits.get(_METRIC_LIMIT.get(metric, ""), 0) or 0)
    used = await get_usage(session, workspace_id, metric)
    allowed = (limit == 0) or (used < limit)
    return allowed, used, limit


async def workspace_id_for_project(session: AsyncSession, project_id: Optional[int]) -> Optional[int]:
    if not project_id:
        return None
    project = await session.get(Project, project_id)
    return project.workspace_id if project else None
