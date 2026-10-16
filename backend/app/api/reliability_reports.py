"""Reliability reports (WP12b): monitor SLOs, flake trends/patterns,
third-party dependency health.

Surface:
    GET  /api/schedules/{id}/slo                     ?window_days=7|30|90
    PUT  /api/schedules/{id}/slo                     {availability_target, latency_p95_ms, window_days} (editor)
    GET  /api/projects/{id}/slo                      every monitor of the project
    GET  /api/analytics/projects/{id}/flake-trend    ?days= (<= 180) daily snapshots + top cases
    GET  /api/analytics/projects/{id}/flake-patterns ?days= (<= 90) by browser/hour/env/step type
    GET  /api/projects/{id}/dependency-health        ?days= (<= 180) per vendor

All reads need project viewer access (and a project-scoped API key must match
the project); the SLO write needs editor. Flake trends and vendor health read
only the roll-up tables written by app.tasks.reliability_tasks; patterns run
grouped SQL over a bounded window using ix_testrun_project_created.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlalchemy import case, text
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    FlakeCaseScoreDaily, FlakeDailySnapshot, MonitorCheck, Project, ProjectEnvironment,
    TestCase, TestCaseResult, TestRun, TestSchedule, TestStatus, VendorHealthDaily,
)
from app.services import flake_patterns, slo as slo_svc, vendor_health
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

_FINISHED = [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]
_FAIL = [TestStatus.FAILED, TestStatus.ERROR]


async def require_project(project_id: Optional[int], principal: AuthPrincipal, session: AsyncSession,
                          min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id) if project_id else None
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project.id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project.id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403,
                            detail="Editor access required" if min_role != "viewer" else "Access denied")
    return project


# ---------------------------------------------------------------------------
# SLOs
# ---------------------------------------------------------------------------

def _window(window_days: Optional[int], schedule: TestSchedule) -> int:
    w = window_days or schedule.slo_window_days or slo_svc.DEFAULT_WINDOW
    if w not in slo_svc.WINDOWS:
        raise HTTPException(status_code=422, detail=f"window_days must be one of {list(slo_svc.WINDOWS)}")
    return w


async def _monitor(schedule_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> TestSchedule:
    schedule = await session.get(TestSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await require_project(schedule.project_id, principal, session, min_role)
    if not schedule.is_monitor:
        raise HTTPException(status_code=400, detail="Schedule is not a monitor")
    return schedule


async def _slo_report(schedule: TestSchedule, window: int, session: AsyncSession) -> Dict[str, Any]:
    now = datetime.utcnow()
    rows = (await session.exec(
        select(MonitorCheck.checked_at, MonitorCheck.is_up, MonitorCheck.region, TestRun.duration_ms)
        .select_from(MonitorCheck)
        .outerjoin(TestRun, TestRun.id == MonitorCheck.run_id)
        .where(MonitorCheck.schedule_id == schedule.id,
               MonitorCheck.checked_at >= now - timedelta(days=window))
    )).all()
    checks = [slo_svc.Check(checked_at=c, is_up=bool(u), region=r, duration_ms=d) for c, u, r, d in rows]
    out = slo_svc.report(checks, target=schedule.slo_availability_target,
                         latency_target=schedule.slo_latency_p95_ms, window_days=window, now=now)
    out.update({
        "schedule_id": schedule.id, "name": schedule.name, "project_id": schedule.project_id,
        "is_active": schedule.is_active, "monitor_state": schedule.monitor_state,
        "configured": {
            "availability_target": schedule.slo_availability_target,
            "latency_p95_ms": schedule.slo_latency_p95_ms,
            "window_days": schedule.slo_window_days,
        },
    })
    return out


@router.get("/schedules/{schedule_id}/slo")
async def monitor_slo(schedule_id: int, window_days: Optional[int] = Query(None),
                      principal: AuthPrincipal = Depends(get_current_principal),
                      session: AsyncSession = Depends(get_session)):
    schedule = await _monitor(schedule_id, principal, session)
    return await _slo_report(schedule, _window(window_days, schedule), session)


class SloTargets(BaseModel):
    availability_target: Optional[float] = PydField(default=None, gt=0, le=100)
    latency_p95_ms: Optional[int] = PydField(default=None, ge=1, le=600_000)
    window_days: Optional[int] = None


@router.put("/schedules/{schedule_id}/slo")
async def set_monitor_slo(schedule_id: int, body: SloTargets,
                          principal: AuthPrincipal = Depends(get_current_principal),
                          session: AsyncSession = Depends(get_session)):
    schedule = await _monitor(schedule_id, principal, session, min_role="editor")
    if body.window_days is not None and body.window_days not in slo_svc.WINDOWS:
        raise HTTPException(status_code=422, detail=f"window_days must be one of {list(slo_svc.WINDOWS)}")
    before = {"availability_target": schedule.slo_availability_target,
              "latency_p95_ms": schedule.slo_latency_p95_ms,
              "window_days": schedule.slo_window_days}
    schedule.slo_availability_target = body.availability_target
    schedule.slo_latency_p95_ms = body.latency_p95_ms
    schedule.slo_window_days = body.window_days
    session.add(schedule)
    project = await session.get(Project, schedule.project_id)
    await audit_record(session, entity_type="monitor_slo", entity_id=schedule.id, action="update",
                       principal=principal, workspace_id=project.workspace_id if project else None,
                       changes={"before": before, "after": body.model_dump()})
    await session.commit()
    await session.refresh(schedule)
    return await _slo_report(schedule, _window(None, schedule), session)


@router.get("/projects/{project_id}/slo")
async def project_slos(project_id: int, window_days: Optional[int] = Query(None),
                       principal: AuthPrincipal = Depends(get_current_principal),
                       session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    monitors = (await session.exec(select(TestSchedule).where(
        TestSchedule.project_id == project_id, TestSchedule.is_monitor == True)  # noqa: E712
        .order_by(TestSchedule.name))).all()
    reports = [await _slo_report(m, _window(window_days, m), session) for m in monitors]
    return {
        "project_id": project_id,
        "monitors": reports,
        "missing": None if monitors else "This project has no monitors. Mark a schedule as a monitor to get SLO reports.",
    }


# ---------------------------------------------------------------------------
# Flake trend + patterns
# ---------------------------------------------------------------------------

@router.get("/analytics/projects/{project_id}/flake-trend")
async def flake_trend(project_id: int, days: int = Query(90, ge=7, le=180), top: int = Query(8, ge=1, le=25),
                      principal: AuthPrincipal = Depends(get_current_principal),
                      session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    since = (datetime.utcnow() - timedelta(days=days - 1)).date()
    snaps = (await session.exec(select(FlakeDailySnapshot).where(
        FlakeDailySnapshot.project_id == project_id, FlakeDailySnapshot.day >= since)
        .order_by(FlakeDailySnapshot.day))).all()
    daily = [{
        "date": s.day.isoformat(), "flaky_cases": s.flaky_cases,
        "quarantined_cases": s.quarantined_cases, "avg_flake_score": s.avg_flake_score,
        "results_total": s.results_total, "results_flaky": s.results_flaky,
        "results_failed": s.results_failed,
        "flaky_result_pct": round(100.0 * s.results_flaky / s.results_total, 1) if s.results_total else None,
    } for s in snaps]

    # Top cases by their latest snapshot score, with their score history.
    latest_day = snaps[-1].day if snaps else None
    cases: List[Dict[str, Any]] = []
    if latest_day:
        top_rows = (await session.exec(
            select(FlakeCaseScoreDaily.test_case_id, TestCase.name, FlakeCaseScoreDaily.flake_score)
            .join(TestCase, TestCase.id == FlakeCaseScoreDaily.test_case_id)
            .where(FlakeCaseScoreDaily.project_id == project_id,
                   FlakeCaseScoreDaily.day == latest_day, FlakeCaseScoreDaily.flake_score > 0)
            .order_by(FlakeCaseScoreDaily.flake_score.desc()).limit(top))).all()
        ids = [r[0] for r in top_rows]
        hist = (await session.exec(select(FlakeCaseScoreDaily).where(
            FlakeCaseScoreDaily.test_case_id.in_(ids), FlakeCaseScoreDaily.day >= since)
            .order_by(FlakeCaseScoreDaily.day))).all() if ids else []
        series: Dict[int, List[Dict[str, Any]]] = {}
        for h in hist:
            series.setdefault(h.test_case_id, []).append(
                {"date": h.day.isoformat(), "flake_score": h.flake_score, "is_quarantined": h.is_quarantined})
        cases = [{"test_case_id": cid, "name": name, "flake_score": score, "history": series.get(cid, [])}
                 for cid, name, score in top_rows]
    return {
        "project_id": project_id, "days": days, "daily": daily, "cases": cases,
        "flaky_score_threshold": flake_patterns.FLAKY_SCORE,
        "missing": None if snaps else
        "No snapshots yet. The flake trend is recorded every 6 hours from now on; history before that is not available.",
    }


@router.get("/analytics/projects/{project_id}/flake-patterns")
async def flake_patterns_report(project_id: int, days: int = Query(30, ge=1, le=90),
                                principal: AuthPrincipal = Depends(get_current_principal),
                                session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    since = datetime.utcnow() - timedelta(days=days)
    flaky = func.sum(case((TestCaseResult.is_flaky == True, 1), else_=0))  # noqa: E712
    failed = func.sum(case((TestCaseResult.status.in_(_FAIL), 1), else_=0))
    where = (TestRun.project_id == project_id, TestRun.created_at >= since,
             TestCaseResult.status.in_(_FINISHED))

    async def grouped(col):
        return (await session.exec(
            select(col, func.count(TestCaseResult.id), flaky, failed)
            .select_from(TestCaseResult).join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(*where).group_by(col))).all()

    by_browser = flake_patterns.rate_buckets(await grouped(TestRun.browser))
    hour = func.extract("hour", TestRun.created_at)
    by_hour = flake_patterns.hour_buckets(await grouped(hour))
    envs = {e.id: e.name for e in (await session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == project_id))).all()}
    by_env = flake_patterns.rate_buckets(
        await grouped(TestRun.environment_id),
        label=lambda k: envs.get(k, f"environment #{k}"))
    for b in by_env:
        if b["key"] == flake_patterns.UNKNOWN:
            b["label"] = "default (no environment)"

    # Step type of the first failed step in the result's per-step evidence.
    # Done in SQL so the (large) evidence JSON never leaves the database.
    step_rows = (await session.exec(text("""
        SELECT r.is_flaky,
               (SELECT e->>'type' FROM json_array_elements(
                    CASE WHEN json_typeof(r.result_payload->'step_evidence') = 'array'
                         THEN r.result_payload->'step_evidence' ELSE '[]'::json END) e
                WHERE e->>'status' = 'failed' LIMIT 1) AS step_type,
               count(*)
        FROM testcaseresult r JOIN testrun t ON t.id = r.test_run_id
        WHERE t.project_id = :pid AND t.created_at >= :since
          AND (r.is_flaky OR r.status IN ('FAILED', 'ERROR'))
        GROUP BY 1, 2
    """).bindparams(pid=project_id, since=since))).all()
    by_step = flake_patterns.step_type_buckets(step_rows)

    total = sum(b["total"] for b in by_browser)
    return {
        "project_id": project_id, "days": days, "results": total,
        "flaky_results": sum(b["flaky"] for b in by_browser),
        "by_browser": by_browser, "by_hour_utc": by_hour, "by_environment": by_env,
        "by_failing_step_type": by_step,
        "missing": None if total else f"No finished test results in the last {days} days.",
    }


# ---------------------------------------------------------------------------
# Third-party dependency health
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/dependency-health")
async def dependency_health(project_id: int, days: int = Query(30, ge=1, le=180),
                            principal: AuthPrincipal = Depends(get_current_principal),
                            session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    since = (datetime.utcnow() - timedelta(days=days - 1)).date()
    rows = (await session.exec(select(VendorHealthDaily).where(
        VendorHealthDaily.project_id == project_id, VendorHealthDaily.day >= since)
        .order_by(VendorHealthDaily.day))).all()
    vendors = vendor_health.summarise(rows)
    computed = max((r.computed_at for r in rows), default=None)
    return {
        "project_id": project_id, "days": days, "since": since.isoformat(),
        "computed_at": computed, "vendors": vendors,
        "latency_note": "Latency percentiles are bucketed (upper bound of the bucket), so they are approximate.",
        "missing": None if vendors else
        "No third-party requests recorded in this window. Runs are rolled up hourly from their captured network traffic.",
    }
