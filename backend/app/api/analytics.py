"""Run-history analytics — daily trend buckets and per-case flakiness.

Surface:
    GET /api/analytics/projects/{project_id}/trends?days=30
    GET /api/analytics/suites/{suite_id}/trends?days=30
    GET /api/analytics/projects/{project_id}/flakiness

Trends return one bucket per calendar day (UTC), zero-filled for days
without runs. Aggregation happens in SQL (count/sum/avg with CASE), never
by iterating run rows in Python. Flake scores themselves are maintained by
the result-aggregator pipeline (see app/tasks/result_aggregator.py).
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_session
from app.models import (
    FlakeRecord, TestCase, TestCaseResult, TestRun, TestStatus, TestSuite, User,
)
from app.services.access_service import access_service

router = APIRouter()

_COMPLETED_FAIL_STATUSES = [TestStatus.FAILED, TestStatus.ERROR]


async def _require_project_access(user_id: int, project_id: int, session: AsyncSession):
    role = await access_service.get_project_role(user_id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")


async def _daily_trends(session: AsyncSession, filters: list, days: int) -> List[Dict[str, Any]]:
    """Aggregate TestRun rows into daily buckets entirely in SQL."""
    start_day = (datetime.utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    day_col = func.date(TestRun.created_at)
    passed_expr = func.sum(
        case((TestRun.status == TestStatus.PASSED, 1), else_=0))
    failed_expr = func.sum(
        case((TestRun.status.in_(_COMPLETED_FAIL_STATUSES), 1), else_=0))

    stmt = (
        select(
            day_col.label("day"),
            func.count(TestRun.id).label("total_runs"),
            passed_expr.label("passed"),
            failed_expr.label("failed"),
            func.avg(TestRun.duration_ms).label("avg_duration_ms"),
        )
        .where(TestRun.created_at >= start_day, *filters)
        .group_by(day_col)
        .order_by(day_col)
    )
    rows = (await session.exec(stmt)).all()

    by_day: Dict[str, Any] = {}
    for row in rows:
        day = row[0]
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        by_day[key] = row

    buckets: List[Dict[str, Any]] = []
    for i in range(days):
        d = (start_day + timedelta(days=i)).date().isoformat()
        row = by_day.get(d)
        total = int(row[1]) if row else 0
        passed = int(row[2] or 0) if row else 0
        failed = int(row[3] or 0) if row else 0
        avg_duration = float(row[4]) if row is not None and row[4] is not None else None
        completed = passed + failed
        buckets.append({
            "date": d,
            "total_runs": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / completed * 100, 1) if completed else None,
            "avg_duration_ms": round(avg_duration, 1) if avg_duration is not None else None,
        })
    return buckets


@router.get("/analytics/projects/{project_id}/trends")
async def project_trends(
    project_id: int,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    await _require_project_access(current_user.id, project_id, session)
    buckets = await _daily_trends(
        session, [TestRun.project_id == project_id], days)
    return {"project_id": project_id, "days": days, "buckets": buckets}


@router.get("/analytics/suites/{suite_id}/trends")
async def suite_trends(
    suite_id: int,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    await _require_project_access(current_user.id, suite.project_id, session)
    buckets = await _daily_trends(
        session, [TestRun.test_suite_id == suite_id], days)
    return {"suite_id": suite_id, "days": days, "buckets": buckets}


@router.get("/analytics/projects/{project_id}/flakiness")
async def project_flakiness(
    project_id: int,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Per test case flake summary for a project, sorted by flake_score desc.

    `recent_failures` counts failed/errored TestCaseResults for the case's
    name within the window — computed in SQL, grouped by test name.
    """
    await _require_project_access(current_user.id, project_id, session)

    flake_stmt = (
        select(FlakeRecord, TestCase.name)
        .join(TestCase, TestCase.id == FlakeRecord.test_case_id)
        .where(TestCase.project_id == project_id)
        .order_by(FlakeRecord.flake_score.desc())
    )
    flake_rows = (await session.exec(flake_stmt)).all()

    since = datetime.utcnow() - timedelta(days=days)
    fail_stmt = (
        select(TestCaseResult.test_name, func.count(TestCaseResult.id))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestRun.created_at >= since,
            TestCaseResult.status.in_(_COMPLETED_FAIL_STATUSES),
        )
        .group_by(TestCaseResult.test_name)
    )
    failures_by_name = {name: int(count) for name, count in (await session.exec(fail_stmt)).all()}

    # A case can have several FlakeRecords (case-level step_id=None plus
    # per-step rows). Rows arrive sorted by score desc, so the first row per
    # case carries its max score; quarantine is OR-ed across its rows.
    entries: Dict[int, Dict[str, Any]] = {}
    for record, case_name in flake_rows:
        entry = entries.get(record.test_case_id)
        if entry is None:
            entries[record.test_case_id] = {
                "test_case_id": record.test_case_id,
                "name": case_name,
                "flake_score": record.flake_score,
                "is_quarantined": record.is_quarantined,
                "sample_count": record.sample_count,
                "last_failure_message": record.last_failure_message,
                "recent_failures": failures_by_name.get(case_name, 0),
            }
        elif record.is_quarantined:
            entry["is_quarantined"] = True

    return sorted(entries.values(), key=lambda e: e["flake_score"], reverse=True)
