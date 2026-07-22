"""Unified quality dashboard + release gate (PLATFORM_VISION.md §5).

Two things on top of everything shipped so far:

- **Dashboard** — `GET /api/projects/{id}/quality` aggregates run health,
  flakiness, monitor uptime, and security findings into one snapshot so a PM/
  lead can see project quality at a glance.
- **Release gate** — `GET /api/projects/{id}/quality-gate` evaluates the runs
  for a commit/branch against a per-project policy and returns a go/no-go.
  Principal auth (JWT *or* API key) so CI/agents can call it after a run.
  `GET/PUT .../quality-gate/policy` reads/writes the policy.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.auth import get_current_user, get_current_principal, AuthPrincipal
from app.services.access_service import access_service
from app.models import (
    User, Project, TestRun, TestStatus, TestCase, FlakeRecord, TestSchedule,
    SecurityFinding, QualitySnapshot, QualityTrendPoint, QualityGatePolicy,
    QualityGateCheck, QualityGateResult, DEFAULT_QUALITY_GATE,
    CiSettings, DEFAULT_CI_SETTINGS, RunReport, ReportTestResult,
)

router = APIRouter()

FINISHED = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
FLAKE_THRESHOLD = 0.4  # matches auto-quarantine threshold in result_aggregator


def _policy(project: Project) -> QualityGatePolicy:
    """Project policy merged over built-in defaults."""
    if project.quality_gate_policy:
        merged = {**DEFAULT_QUALITY_GATE.model_dump(), **project.quality_gate_policy}
        return QualityGatePolicy(**merged)
    return QualityGatePolicy()


async def _severity_counts(session: AsyncSession, *, project_id=None, run_ids=None,
                           since=None) -> dict:
    stmt = select(SecurityFinding.severity, func.count()).group_by(SecurityFinding.severity)
    if project_id is not None:
        stmt = stmt.where(SecurityFinding.project_id == project_id)
    if run_ids is not None:
        if not run_ids:
            return {}
        stmt = stmt.where(SecurityFinding.run_id.in_(run_ids))
    if since is not None:
        stmt = stmt.where(SecurityFinding.created_at >= since)
    rows = (await session.exec(stmt)).all()
    return {sev: cnt for sev, cnt in rows}


@router.get("/projects/{project_id}/quality", response_model=QualitySnapshot)
async def project_quality(
    project_id: int,
    days: int = Query(7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    start = now - timedelta(days=days)

    runs = (await session.exec(
        select(TestRun).where(
            TestRun.project_id == project_id,
            TestRun.created_at >= start))).all()
    finished = [r for r in runs if r.status in FINISHED]
    passed = [r for r in finished if r.status == TestStatus.PASSED]
    failed = [r for r in finished if r.status in (TestStatus.FAILED, TestStatus.ERROR)]
    pass_rate = round(100.0 * len(passed) / len(finished), 2) if finished else 0.0

    # Daily trend (finished runs only).
    buckets = defaultdict(lambda: [0, 0])  # date -> [runs, passed]
    for r in finished:
        d = r.created_at.date().isoformat()
        buckets[d][0] += 1
        if r.status == TestStatus.PASSED:
            buckets[d][1] += 1
    trend = [
        QualityTrendPoint(date=d, runs=v[0], passed_runs=v[1],
                          pass_rate=round(100.0 * v[1] / v[0], 2) if v[0] else 0.0)
        for d, v in sorted(buckets.items())
    ]

    # Flakiness (records for this project's cases).
    flaky = (await session.exec(
        select(func.count()).select_from(FlakeRecord)
        .join(TestCase, TestCase.id == FlakeRecord.test_case_id)
        .where(TestCase.project_id == project_id,
               FlakeRecord.flake_score >= FLAKE_THRESHOLD))).one()
    quarantined = (await session.exec(
        select(func.count()).select_from(FlakeRecord)
        .join(TestCase, TestCase.id == FlakeRecord.test_case_id)
        .where(TestCase.project_id == project_id,
               FlakeRecord.is_quarantined == True))).one()  # noqa: E712

    # Monitors.
    monitors = (await session.exec(
        select(TestSchedule).where(
            TestSchedule.project_id == project_id,
            TestSchedule.is_monitor == True))).all()  # noqa: E712
    up = sum(1 for m in monitors if m.monitor_state == "up")
    down = sum(1 for m in monitors if m.monitor_state == "down")
    down_names = [m.name for m in monitors if m.monitor_state == "down"]

    sec_counts = await _severity_counts(session, project_id=project_id, since=start)

    return QualitySnapshot(
        project_id=project_id, window_days=days,
        total_runs=len(runs), finished_runs=len(finished),
        passed_runs=len(passed), failed_runs=len(failed), pass_rate=pass_rate,
        trend=trend,
        flaky_tests=int(flaky or 0), quarantined_tests=int(quarantined or 0),
        monitors_total=len(monitors), monitors_up=up, monitors_down=down,
        down_monitor_names=down_names,
        security_findings=sec_counts,
    )


@router.get("/projects/{project_id}/quality-gate/policy", response_model=QualityGatePolicy)
async def get_quality_gate_policy(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return _policy(project)


@router.put("/projects/{project_id}/quality-gate/policy", response_model=QualityGatePolicy)
async def set_quality_gate_policy(
    project_id: int,
    policy: QualityGatePolicy = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    project.quality_gate_policy = policy.model_dump()
    session.add(project)
    await session.commit()
    return policy


async def evaluate_gate_for_runs(
    session: AsyncSession, project: Project, finished_runs: list,
    git_commit: Optional[str] = None, git_branch: Optional[str] = None,
) -> QualityGateResult:
    """Evaluate the project's gate policy over a given set of finished runs.
    Shared by the gate endpoint and the run-report endpoint. Fails closed when
    the set is empty — you can't certify what didn't run."""
    policy = _policy(project)
    run_ids = [r.id for r in finished_runs]
    checks: list[QualityGateCheck] = []

    passed_runs = [r for r in finished_runs if r.status == TestStatus.PASSED]
    pr = round(100.0 * len(passed_runs) / len(finished_runs), 2) if finished_runs else 0.0
    checks.append(QualityGateCheck(
        name="pass_rate",
        passed=bool(finished_runs) and pr >= policy.min_pass_rate,
        actual=f"{pr}%", threshold=f">= {policy.min_pass_rate}%",
        detail=None if finished_runs else "no finished runs found"))

    sev = await _severity_counts(session, run_ids=run_ids)
    high = sev.get("high", 0)
    checks.append(QualityGateCheck(
        name="security_high_findings", passed=high <= policy.max_high_severity_findings,
        actual=str(high), threshold=f"<= {policy.max_high_severity_findings}"))
    if policy.max_medium_severity_findings is not None:
        med = sev.get("medium", 0)
        checks.append(QualityGateCheck(
            name="security_medium_findings",
            passed=med <= policy.max_medium_severity_findings,
            actual=str(med), threshold=f"<= {policy.max_medium_severity_findings}"))

    if policy.require_monitors_up:
        monitors = (await session.exec(
            select(TestSchedule).where(
                TestSchedule.project_id == project.id,
                TestSchedule.is_monitor == True))).all()  # noqa: E712
        down = [m.name for m in monitors if m.monitor_state == "down"]
        checks.append(QualityGateCheck(
            name="monitors_up", passed=len(down) == 0,
            actual=f"{len(down)} down", threshold="0 down",
            detail=", ".join(down) or None))

    passed_gate = bool(finished_runs) and all(c.passed for c in checks)
    return QualityGateResult(
        project_id=project.id, passed=passed_gate,
        git_commit=git_commit, git_branch=git_branch,
        evaluated_run_ids=run_ids, checks=checks)


@router.get("/projects/{project_id}/quality-gate", response_model=QualityGateResult)
async def evaluate_quality_gate(
    project_id: int,
    git_commit: Optional[str] = Query(None),
    git_branch: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Go/no-go for a release. Evaluates the runs for a commit (or branch, or
    the single latest run) against the project policy."""
    current_user = principal.user
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    stmt = select(TestRun).where(TestRun.project_id == project_id)
    if git_commit:
        stmt = stmt.where(TestRun.git_commit == git_commit)
    elif git_branch:
        stmt = stmt.where(TestRun.git_branch == git_branch)
    runs = (await session.exec(stmt.order_by(TestRun.created_at.desc()))).all()

    finished = [r for r in runs if r.status in FINISHED]
    if not git_commit and not git_branch:
        finished = finished[:1]  # latest finished run only

    return await evaluate_gate_for_runs(session, project, finished, git_commit, git_branch)


# ---------------------------------------------------------------------------
# CI settings (configurable, opt-in) + CI-/VCS-agnostic run report
# ---------------------------------------------------------------------------


def _ci_settings(project: Project) -> CiSettings:
    if project.ci_settings:
        merged = {**DEFAULT_CI_SETTINGS.model_dump(), **project.ci_settings}
        return CiSettings(**merged)
    return CiSettings()


@router.get("/projects/{project_id}/ci-settings", response_model=CiSettings)
async def get_ci_settings(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return _ci_settings(project)


@router.put("/projects/{project_id}/ci-settings", response_model=CiSettings)
async def set_ci_settings(
    project_id: int,
    ci: CiSettings = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    project.ci_settings = ci.model_dump()
    session.add(project)
    await session.commit()
    return ci


_ICON = {"passed": "✅", "failed": "❌", "error": "❗", "running": "⏳", "pending": "⏳"}


def _render_markdown(rep: RunReport, ci: CiSettings) -> str:
    st = rep.status.value if hasattr(rep.status, "value") else str(rep.status)
    lines = [
        f"## {_ICON.get(st, '•')} TraceIQ run #{rep.run_id} — `{st}`",
        "",
        f"**{rep.passed_tests} passed / {rep.failed_tests} failed** of {rep.total_tests}"
        + (f" · {rep.duration_ms/1000:.1f}s" if rep.duration_ms else ""),
    ]
    if rep.git:
        commit = (rep.git.get("commit") or "")[:8]
        bits = [b for b in (rep.git.get("branch"), f"`{commit}`" if commit else None) if b]
        if bits:
            lines.append("")
            lines.append("_" + " · ".join(bits) + "_")

    if rep.gate is not None:
        verdict = "✅ PASS" if rep.gate.passed else "❌ FAIL"
        enforced = "" if ci.enforce_gate else " _(advisory — not enforced)_"
        lines += ["", f"### Quality gate: {verdict}{enforced}", "",
                  "| Check | Result | Actual | Threshold |", "| --- | --- | --- | --- |"]
        for c in rep.gate.checks:
            lines.append(f"| {c.name} | {'✅' if c.passed else '❌'} | {c.actual} | {c.threshold} |")

    if rep.security:
        sev = ", ".join(f"{k}: {v}" for k, v in rep.security.items())
        lines += ["", f"**Security findings:** {sev}"]

    if rep.results:
        lines += ["", "### Results", "", "| Test | Status | Duration |", "| --- | --- | --- |"]
        for r in rep.results[:50]:
            rst = r.status.value if hasattr(r.status, "value") else str(r.status)
            dur = f"{r.duration_ms/1000:.1f}s" if r.duration_ms else ""
            name = (r.test_name or "").replace("|", "\\|")
            lines.append(f"| {_ICON.get(rst, '•')} {name} | `{rst}` | {dur} |")
        if len(rep.results) > 50:
            lines.append(f"| … | {len(rep.results) - 50} more | |")

    return "\n".join(lines)


@router.get("/runs/{run_id}/report", response_model=RunReport)
async def run_report(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """CI-/VCS-agnostic consolidated report for one run: results, security
    findings, quality-gate verdict (per the project's policy), optional git
    context, and a ready-to-paste markdown block. Keyed by run_id, so git is
    optional. Principal auth so CI/agents (API key) can call it."""
    run = (await session.exec(
        select(TestRun).where(TestRun.id == run_id)
        .options(selectinload(TestRun.results)))).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(principal.user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    results = [
        ReportTestResult(
            test_name=r.test_name, status=r.status, duration_ms=r.duration_ms,
            error_message=r.error_message, trace_url=r.trace_url)
        for r in (run.results or [])
    ]
    security = await _severity_counts(session, run_ids=[run_id])

    git = None
    if run.git_commit or run.git_branch or run.git_pr_url:
        git = {"commit": run.git_commit, "branch": run.git_branch,
               "pr_url": run.git_pr_url, "repo": run.git_repo}

    gate = None
    project = await session.get(Project, run.project_id) if run.project_id else None
    ci = _ci_settings(project) if project else CiSettings()
    if project:
        finished = [run] if run.status in FINISHED else []
        gate = await evaluate_gate_for_runs(
            session, project, finished, run.git_commit, run.git_branch)

    report = RunReport(
        run_id=run.id, project_id=run.project_id, status=run.status,
        suite_name=run.suite_name, total_tests=run.total_tests or 0,
        passed_tests=run.passed_tests or 0, failed_tests=run.failed_tests or 0,
        duration_ms=run.duration_ms, results=results, security=security,
        git=git, gate=gate)
    report.markdown = _render_markdown(report, ci)
    return report
