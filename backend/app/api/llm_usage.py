"""LLM usage API — per-workspace token stats + worker-side ingest.

- `GET /workspaces/{id}/llm-usage` powers the AI-usage dashboard: totals,
  per-provider/model breakdown, per-feature breakdown, and a daily series.
- `POST /internal/llm-usage` lets the Node execution workers report token
  usage from their own LLM calls (selector heal, in-worker failure analysis).
  Authenticated by the worker shared secret, same as the baseline-resolve
  internal endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.models import (
    LLMUsageEvent, Project, TestRun, UsageRecord, User, UserWorkspace,
)
from app.services import billing as billing_svc

router = APIRouter()


async def _member(session: AsyncSession, workspace_id: int, user_id: int) -> UserWorkspace:
    row = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.workspace_id == workspace_id,
        UserWorkspace.user_id == user_id))).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return row


@router.get("/workspaces/{workspace_id}/llm-usage")
async def workspace_llm_usage(
    workspace_id: int,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    await _member(session, workspace_id, current_user.id)
    days = max(1, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)
    base_where = (
        LLMUsageEvent.workspace_id == workspace_id,
        LLMUsageEvent.created_at >= since,
    )

    calls = func.count(LLMUsageEvent.id)
    in_tok = func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0)
    out_tok = func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0)
    tot_tok = func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0)

    totals_row = (await session.exec(
        select(calls, in_tok, out_tok, tot_tok,
               func.coalesce(func.avg(LLMUsageEvent.latency_ms), 0))
        .where(*base_where))).first()

    by_provider_rows = (await session.exec(
        select(LLMUsageEvent.provider, LLMUsageEvent.model, calls, in_tok, out_tok, tot_tok,
               func.coalesce(func.avg(LLMUsageEvent.latency_ms), 0),
               func.sum(case((LLMUsageEvent.success == True, 1), else_=0)))  # noqa: E712
        .where(*base_where)
        .group_by(LLMUsageEvent.provider, LLMUsageEvent.model)
        .order_by(tot_tok.desc()))).all()

    by_feature_rows = (await session.exec(
        select(LLMUsageEvent.feature, calls, tot_tok)
        .where(*base_where)
        .group_by(LLMUsageEvent.feature)
        .order_by(tot_tok.desc()))).all()

    day = func.date(LLMUsageEvent.created_at)
    daily_rows = (await session.exec(
        select(day, calls, tot_tok)
        .where(*base_where)
        .group_by(day)
        .order_by(day))).all()

    # Current calendar-month tokens vs the plan cap (0 = unlimited).
    period = billing_svc.current_period()
    period_used = await billing_svc.get_usage(session, workspace_id, "llm_tokens", period)
    limits = await billing_svc.get_limits(session, workspace_id)
    token_limit = int(limits.get("monthly_llm_tokens", 0) or 0)

    return {
        "workspace_id": workspace_id,
        "window_days": days,
        "totals": {
            "calls": totals_row[0] if totals_row else 0,
            "input_tokens": int(totals_row[1]) if totals_row else 0,
            "output_tokens": int(totals_row[2]) if totals_row else 0,
            "total_tokens": int(totals_row[3]) if totals_row else 0,
            "avg_latency_ms": round(float(totals_row[4]), 1) if totals_row else 0,
        },
        "by_provider": [
            {
                "provider": r[0], "model": r[1], "calls": r[2],
                "input_tokens": int(r[3]), "output_tokens": int(r[4]),
                "total_tokens": int(r[5]), "avg_latency_ms": round(float(r[6]), 1),
                "success_rate": round((r[7] or 0) / r[2], 3) if r[2] else 1.0,
            }
            for r in by_provider_rows
        ],
        "by_feature": [
            {"feature": r[0], "calls": r[1], "total_tokens": int(r[2])}
            for r in by_feature_rows
        ],
        "daily": [
            {"date": str(r[0]), "calls": r[1], "total_tokens": int(r[2])}
            for r in daily_rows
        ],
        "period": period,
        "period_tokens_used": period_used,
        "period_tokens_limit": token_limit,
    }


class LLMUsageEventIn(BaseModel):
    provider: str
    model: str = ""
    feature: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    success: bool = True
    error: Optional[str] = None
    run_id: Optional[int] = None


class LLMUsageIngest(BaseModel):
    events: List[LLMUsageEventIn]


@router.post("/internal/llm-usage")
async def ingest_worker_llm_usage(
    body: LLMUsageIngest,
    x_worker_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Execution-worker usage ingest. Workspace/project are resolved from the
    run each event belongs to, so the worker only needs to know the run id."""
    expected = settings.WEBHOOK_SECRET or settings.SECRET_KEY
    if not x_worker_secret or x_worker_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid worker secret")

    period = datetime.utcnow().strftime("%Y-%m")
    rollup: dict[int, int] = {}
    accepted = 0
    for ev in body.events[:200]:
        workspace_id = project_id = None
        if ev.run_id:
            run = await session.get(TestRun, ev.run_id)
            if run and run.project_id:
                project_id = run.project_id
                project = await session.get(Project, run.project_id)
                workspace_id = project.workspace_id if project else None
        total = max(0, int(ev.input_tokens)) + max(0, int(ev.output_tokens))
        session.add(LLMUsageEvent(
            workspace_id=workspace_id, project_id=project_id, run_id=ev.run_id,
            provider=ev.provider, model=ev.model, feature=ev.feature,
            source="execution-engine",
            input_tokens=max(0, int(ev.input_tokens)),
            output_tokens=max(0, int(ev.output_tokens)),
            total_tokens=total, latency_ms=max(0, int(ev.latency_ms)),
            success=ev.success, error=(ev.error or None) and str(ev.error)[:500],
        ))
        if workspace_id and total:
            rollup[workspace_id] = rollup.get(workspace_id, 0) + total
        accepted += 1

    for ws_id, tokens in rollup.items():
        rec = (await session.exec(select(UsageRecord).where(
            UsageRecord.workspace_id == ws_id,
            UsageRecord.period == period,
            UsageRecord.metric == "llm_tokens"))).first()
        if rec:
            rec.count += tokens
        else:
            rec = UsageRecord(workspace_id=ws_id, period=period,
                              metric="llm_tokens", count=tokens)
        session.add(rec)

    await session.commit()
    return {"accepted": accepted}
