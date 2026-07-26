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
    FailureCluster,
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


# ---------------------------------------------------------------------------
# Test-effectiveness analytics (PLATFORM_VISION.md §5, item 4)
# ---------------------------------------------------------------------------


@router.get("/analytics/projects/{project_id}/test-effectiveness")
async def test_effectiveness(
    project_id: int,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Per-test signal metrics over the window: how often each test runs, how
    often it fails, its failure rate, how many distinct failure clusters it
    surfaced (its 'signal'), and its average duration. Grouped by test name."""
    await _require_project_access(current_user.id, project_id, session)
    since = datetime.utcnow() - timedelta(days=days)

    fail_case = case((TestCaseResult.status.in_(_COMPLETED_FAIL_STATUSES), 1), else_=0)
    stmt = (
        select(
            TestCaseResult.test_name,
            func.count(TestCaseResult.id).label("runs"),
            func.sum(fail_case).label("failures"),
            func.avg(TestCaseResult.duration_ms).label("avg_ms"),
            func.count(func.distinct(TestCaseResult.cluster_id)).label("clusters"),
        )
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= since)
        .group_by(TestCaseResult.test_name)
    )
    rows = (await session.exec(stmt)).all()
    out = []
    for name, runs, failures, avg_ms, clusters in rows:
        runs = int(runs or 0)
        failures = int(failures or 0)
        out.append({
            "test_name": name,
            "runs": runs,
            "failures": failures,
            "failure_rate": round(100.0 * failures / runs, 1) if runs else 0.0,
            "clusters_surfaced": int(clusters or 0),
            "avg_duration_ms": round(float(avg_ms), 1) if avg_ms is not None else None,
        })
    # Most-failing first — the actionable ranking.
    out.sort(key=lambda r: (r["failures"], r["failure_rate"]), reverse=True)
    return out[:limit]


@router.get("/analytics/projects/{project_id}/effectiveness-summary")
async def effectiveness_summary(
    project_id: int,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Project-level quality health: run totals, cluster status counts, MTTR of
    resolved failure clusters, and top failing / flakiest / slowest tests."""
    await _require_project_access(current_user.id, project_id, session)
    since = datetime.utcnow() - timedelta(days=days)

    # Run totals over the window.
    finished = [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]
    run_rows = (await session.exec(
        select(TestRun.status, func.count(TestRun.id))
        .where(TestRun.project_id == project_id, TestRun.created_at >= since,
               TestRun.status.in_(finished))
        .group_by(TestRun.status))).all()
    run_counts = {str(s.value if hasattr(s, "value") else s): int(c) for s, c in run_rows}
    total_finished = sum(run_counts.values())
    passed = run_counts.get("passed", 0)
    pass_rate = round(100.0 * passed / total_finished, 1) if total_finished else 0.0

    # Failure-cluster status counts.
    cl_rows = (await session.exec(
        select(FailureCluster.status, func.count(FailureCluster.id))
        .where(FailureCluster.project_id == project_id)
        .group_by(FailureCluster.status))).all()
    cluster_counts = {str(s): int(c) for s, c in cl_rows}

    # MTTR (hours) over clusters resolved within the window.
    resolved = (await session.exec(
        select(FailureCluster.first_seen_at, FailureCluster.resolved_at)
        .where(FailureCluster.project_id == project_id,
               FailureCluster.resolved_at != None,  # noqa: E711
               FailureCluster.resolved_at >= since))).all()
    mttr_hours = None
    if resolved:
        secs = [(r_at - f_at).total_seconds() for f_at, r_at in resolved if r_at and f_at]
        if secs:
            mttr_hours = round((sum(secs) / len(secs)) / 3600.0, 1)

    # Top failing + slowest (reuse the effectiveness rollup).
    fail_case = case((TestCaseResult.status.in_(_COMPLETED_FAIL_STATUSES), 1), else_=0)
    eff = (await session.exec(
        select(TestCaseResult.test_name,
               func.count(TestCaseResult.id),
               func.sum(fail_case),
               func.avg(TestCaseResult.duration_ms))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= since)
        .group_by(TestCaseResult.test_name))).all()
    eff_list = [{"test_name": n, "runs": int(r or 0), "failures": int(f or 0),
                 "avg_duration_ms": round(float(a), 1) if a is not None else None}
                for n, r, f, a in eff]
    top_failing = sorted([e for e in eff_list if e["failures"] > 0],
                         key=lambda e: e["failures"], reverse=True)[:5]
    slowest = sorted([e for e in eff_list if e["avg_duration_ms"] is not None],
                     key=lambda e: e["avg_duration_ms"], reverse=True)[:5]

    # Flakiest (from flake records).
    flake_rows = (await session.exec(
        select(TestCase.name, func.max(FlakeRecord.flake_score))
        .join(FlakeRecord, FlakeRecord.test_case_id == TestCase.id)
        .where(TestCase.project_id == project_id)
        .group_by(TestCase.name).order_by(func.max(FlakeRecord.flake_score).desc()).limit(5))).all()
    flakiest = [{"test_name": n, "flake_score": round(float(s or 0), 2)} for n, s in flake_rows]

    return {
        "project_id": project_id,
        "window_days": days,
        "total_finished_runs": total_finished,
        "pass_rate": pass_rate,
        "run_counts": run_counts,
        "cluster_counts": cluster_counts,
        "open_clusters": cluster_counts.get("open", 0),
        "mttr_hours": mttr_hours,
        "top_failing_tests": top_failing,
        "slowest_tests": slowest,
        "flakiest_tests": flakiest,
    }
