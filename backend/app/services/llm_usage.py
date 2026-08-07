"""LLM usage metering — records every provider call and enforces token quotas.

Providers call `record_llm_usage(...)` after each request with the token counts
the API returned. Business context (which workspace / feature / run triggered
the call) travels via a contextvar so provider code stays context-free: call
sites wrap their LLM work in `llm_call_context(...)`.

Two write targets per call:
- `LLMUsageEvent`  — one row per call; feeds the per-provider/model dashboard.
- `UsageRecord`    — monthly per-workspace rollup (metric="llm_tokens") so the
                     billing layer can cap tokens per plan (`monthly_llm_tokens`
                     limit key, 0/absent = unlimited).

Writes use a dedicated sync engine (same pattern as the Celery tasks) because
providers are sync and run from both Celery workers and FastAPI handlers —
where `provider.complete()` already blocks on the network call, so a one-row
insert is negligible. Recording failures are swallowed: metering must never
break the AI feature it measures.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Iterator, Optional

from sqlmodel import Session, create_engine, select

from app.core.config import db_url_for, settings

_sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
_engine = create_engine(_sync_db_url, echo=False, pool_pre_ping=True)

_ctx: ContextVar[dict] = ContextVar("llm_call_context", default={})


@contextmanager
def llm_call_context(
    *,
    feature: Optional[str] = None,
    workspace_id: Optional[int] = None,
    project_id: Optional[int] = None,
    run_id: Optional[int] = None,
) -> Iterator[None]:
    """Attach business context to LLM calls made inside the block.

    Merges with any outer context, so e.g. a task can set workspace/run once
    and inner helpers only add their feature name.
    """
    merged = dict(_ctx.get())
    for key, value in (
        ("feature", feature), ("workspace_id", workspace_id),
        ("project_id", project_id), ("run_id", run_id),
    ):
        if value is not None:
            merged[key] = value
    token = _ctx.set(merged)
    try:
        yield
    finally:
        _ctx.reset(token)


def get_llm_context() -> dict:
    return dict(_ctx.get())


def record_llm_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    success: bool = True,
    error: Optional[str] = None,
    source: str = "backend",
    feature: Optional[str] = None,
    workspace_id: Optional[int] = None,
    project_id: Optional[int] = None,
    run_id: Optional[int] = None,
) -> None:
    """Persist one LLM call. Explicit kwargs override the ambient context."""
    from app.models import LLMUsageEvent, UsageRecord

    ctx = _ctx.get()
    feature = feature or ctx.get("feature") or "unknown"
    workspace_id = workspace_id if workspace_id is not None else ctx.get("workspace_id")
    project_id = project_id if project_id is not None else ctx.get("project_id")
    run_id = run_id if run_id is not None else ctx.get("run_id")
    total = int(input_tokens or 0) + int(output_tokens or 0)

    try:
        with Session(_engine) as session:
            session.add(LLMUsageEvent(
                workspace_id=workspace_id, project_id=project_id, run_id=run_id,
                provider=provider, model=model or "", feature=feature, source=source,
                input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
                total_tokens=total, latency_ms=int(latency_ms or 0),
                success=success, error=(error or None) and str(error)[:500],
            ))
            if workspace_id and total:
                period = datetime.utcnow().strftime("%Y-%m")
                rec = session.exec(select(UsageRecord).where(
                    UsageRecord.workspace_id == workspace_id,
                    UsageRecord.period == period,
                    UsageRecord.metric == "llm_tokens")).first()
                if rec:
                    rec.count += total
                else:
                    rec = UsageRecord(workspace_id=workspace_id, period=period,
                                      metric="llm_tokens", count=total)
                session.add(rec)
            session.commit()
    except Exception as exc:  # noqa: BLE001 — metering must never break the caller
        print(f"[LLMUsage] failed to record usage: {exc}")


def llm_tokens_quota_exceeded(workspace_id: Optional[int] = None) -> bool:
    """True when the workspace's plan caps monthly LLM tokens and the cap is
    already spent. No plan / no `monthly_llm_tokens` limit / no workspace
    context → False (unlimited). Fails open on DB errors.
    """
    from app.models import Plan, UsageRecord, WorkspaceSubscription

    workspace_id = workspace_id if workspace_id is not None else _ctx.get().get("workspace_id")
    if not workspace_id:
        return False
    try:
        with Session(_engine) as session:
            sub = session.exec(select(WorkspaceSubscription).where(
                WorkspaceSubscription.workspace_id == workspace_id)).first()
            plan = session.get(Plan, sub.plan_id) if sub else session.exec(
                select(Plan).where(Plan.name == "free")).first()
            limit = int(((plan.limits if plan else {}) or {}).get("monthly_llm_tokens", 0) or 0)
            if limit == 0:
                return False
            period = datetime.utcnow().strftime("%Y-%m")
            rec = session.exec(select(UsageRecord).where(
                UsageRecord.workspace_id == workspace_id,
                UsageRecord.period == period,
                UsageRecord.metric == "llm_tokens")).first()
            return (rec.count if rec else 0) >= limit
    except Exception as exc:  # noqa: BLE001
        print(f"[LLMUsage] quota check failed (allowing call): {exc}")
        return False
