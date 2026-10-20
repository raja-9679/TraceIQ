"""Unified quality dashboard + release gate (PLATFORM_VISION.md §5).

Two things on top of everything shipped so far:

- **Dashboard** — `GET /api/projects/{id}/quality` aggregates run health,
  flakiness, monitor uptime, and security findings into one snapshot so a PM/
  lead can see project quality at a glance.
- **Release gate** — `GET /api/projects/{id}/quality-gate` evaluates the runs
  for a commit/branch against a per-project policy and returns a go/no-go.
  Principal auth (JWT *or* API key) so CI/agents can call it after a run.
  `GET/PUT .../quality-gate/policy` reads/writes the policy.
- **Release readiness** — `GET /api/projects/{id}/release-readiness` wraps the
  gate for one commit with the evidence behind it (runs, critical cases,
  quarantines, requirements) and a ready / not ready / insufficient-data
  verdict. Logic in app/services/release_readiness.py.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.auth import get_current_user, get_current_principal, AuthPrincipal
from app.services.access_service import access_service
from app.services.flake_policy import active_quarantine_clause
from app.services import gate_policy_versions, release_readiness, run_status
from app.services.audit import record as audit_record
from app.api.jobs import api_key_allows_project
from app.models import (
    User, Project, TestRun, TestStatus, TestCase, TestCaseResult, FlakeRecord,
    TestSchedule,
    SecurityFinding, QualitySnapshot, QualityTrendPoint, QualityGatePolicy,
    QualityGateCheck, QualityGateResult, DEFAULT_QUALITY_GATE, ReleaseReadiness,
    CiSettings, DEFAULT_CI_SETTINGS, RunReport, ReportTestResult,
)

router = APIRouter()

FINISHED = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
FLAKE_THRESHOLD = 0.4  # matches auto-quarantine threshold in result_aggregator


def _policy(project: Project) -> QualityGatePolicy:
    """Project policy merged over built-in defaults."""
    return gate_policy_versions.effective_policy(project)


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
               active_quarantine_clause()))).one()

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
    before = _policy(project).model_dump()
    after = policy.model_dump()
    project.quality_gate_policy = after
    session.add(project)
    # The gate decides releases; who loosened it, and when, has to be answerable.
    changed = {k: {"from": before.get(k), "to": v} for k, v in after.items() if before.get(k) != v}
    # Commits with the change: every policy that was ever in force stays
    # addressable, so a past verdict can be re-evaluated against it (WP15f).
    version = await gate_policy_versions.ensure_current(
        session, project, policy, created_by_id=current_user.id, commit=False)
    if changed:
        await audit_record(
            session, entity_type="project", entity_id=project_id,
            action="quality_gate_policy_update", workspace_id=project.workspace_id,
            user_id=current_user.id, changes={**changed, "policy_version": version.version})
    await session.commit()
    return policy


@router.get("/projects/{project_id}/quality-gate/policy/versions")
async def list_quality_gate_policy_versions(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Every policy that has judged this project's gate, newest first."""
    await _readable_project(project_id, session, principal)
    rows = await gate_policy_versions.list_versions(session, project_id)
    return {"project_id": project_id, "versions": [gate_policy_versions.to_read(r) for r in rows]}


@router.get("/projects/{project_id}/quality-gate/policy/versions/{version}")
async def get_quality_gate_policy_version(
    project_id: int,
    version: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    await _readable_project(project_id, session, principal)
    row = await gate_policy_versions.get_version(session, project_id, version)
    if row is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    return gate_policy_versions.to_read(row, include_policy=True)


def coverage_check(finished_runs: list) -> QualityGateCheck:
    """How much of the suite the evaluated runs covered. Informational only —
    it never fails the gate (targeted runs are allowed to pass, labelled
    partial); it exists so nobody reads a 4-case pass as a full pass."""
    # Runs created outside POST /runs (schedules, comparisons, older rows)
    # carry no selection data; judge by the runs that do, and say so.
    tracked = [r for r in finished_runs if getattr(r, "selection_mode", None)]
    untracked = len(finished_runs) - len(tracked)
    if any(r.selection_mode == "full" for r in tracked):
        actual = "full suite"
    elif tracked:
        ran = {r.test_case_id for r in tracked if r.test_case_id is not None}
        total = max((r.selection_total or 0) for r in tracked)
        actual = f"targeted — {len(ran)} of {total} cases ran"
        if untracked:
            actual += f" (+{untracked} run(s) with no selection data)"
    else:
        actual = "unknown (runs carry no selection data)"
    return QualityGateCheck(name="coverage", passed=True, actual=actual,
                            threshold="informational",
                            detail=("partial coverage — not every case ran"
                                    if actual.startswith("targeted") else None))


async def evaluate_gate_for_runs(
    session: AsyncSession, project: Project, finished_runs: list,
    git_commit: Optional[str] = None, git_branch: Optional[str] = None,
    *, policy_version=None, ignored_run_ids: Optional[List[int]] = None,
) -> QualityGateResult:
    """Evaluate the project's gate policy over a given set of finished runs.
    Shared by the gate endpoint and the run-report endpoint. Fails closed when
    the set is empty — you can't certify what didn't run.

    `policy_version` (a GatePolicyVersion) re-judges with a past policy;
    otherwise the current policy is used and recorded as a version, and the
    result names it either way (WP15f)."""
    if policy_version is None:
        policy_version = await gate_policy_versions.ensure_current(session, project)
    policy = gate_policy_versions.policy_of(policy_version)
    # Anything else in finished_runs that has no verdict is left out loudly.
    ignored = list(ignored_run_ids or []) + [r.id for r in finished_runs if r.status not in FINISHED]
    finished_runs = [r for r in finished_runs if r.status in FINISHED]
    run_ids = [r.id for r in finished_runs]
    checks: list[QualityGateCheck] = []

    passed_runs = [r for r in finished_runs if r.status == TestStatus.PASSED]
    pr = round(100.0 * len(passed_runs) / len(finished_runs), 2) if finished_runs else 0.0
    checks.append(QualityGateCheck(
        name="pass_rate",
        passed=bool(finished_runs) and pr >= policy.min_pass_rate,
        actual=f"{pr}%", threshold=f">= {policy.min_pass_rate}%",
        detail=(("no finished runs found" if not finished_runs else None) if not ignored
                else f"{len(ignored)} cancelled / timed-out run(s) not counted"
                     + ("" if finished_runs else "; no other finished runs")),
        missing_data=not finished_runs))
    checks.append(coverage_check(finished_runs))

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

    # External CI results (ingested JUnit) must be green when required.
    if policy.require_external_tests_pass:
        from app.models import ExternalTestReport
        ext_query = select(ExternalTestReport).where(
            ExternalTestReport.project_id == project.id)
        if git_commit:
            ext_query = ext_query.where(ExternalTestReport.git_commit == git_commit)
        ext_query = ext_query.order_by(ExternalTestReport.created_at.desc()).limit(1)
        latest_ext = (await session.exec(ext_query)).first()
        if latest_ext is None:
            checks.append(QualityGateCheck(
                name="external_tests", passed=False, actual="no report",
                threshold="0 failures", missing_data=True,
                detail=("no external CI report ingested"
                        + (f" for commit {git_commit}" if git_commit else ""))))
        else:
            broken = latest_ext.failures + latest_ext.errors
            checks.append(QualityGateCheck(
                name="external_tests", passed=broken == 0,
                actual=f"{broken} failed of {latest_ext.tests}",
                threshold="0 failures",
                detail=f"{latest_ext.source} report {latest_ext.id}"
                       + (f" ({latest_ext.suite_name})" if latest_ext.suite_name else "")))

    # Performance budgets — worst web-vitals sample across the evaluated runs.
    if run_ids and (policy.max_lcp_ms or policy.max_cls or policy.max_ttfb_ms):
        results = (await session.exec(
            select(TestCaseResult.result_payload, TestCaseResult.test_name).where(
                TestCaseResult.test_run_id.in_(run_ids)))).all()
        worst: dict = {"lcp_ms": None, "cls": None, "ttfb_ms": None}
        worst_test: dict = {}
        for payload, test_name in results:
            wv = (payload or {}).get("web_vitals") or {}
            for key in worst:
                val = wv.get(key)
                if val is not None and (worst[key] is None or val > worst[key]):
                    worst[key] = val
                    worst_test[key] = test_name

        def _budget_check(name: str, key: str, limit: float, unit: str = "ms"):
            actual = worst[key]
            if actual is None:
                # No samples — inconclusive, don't fail the gate on missing data.
                checks.append(QualityGateCheck(
                    name=name, passed=True, actual="no samples",
                    threshold=f"<= {limit}{unit}",
                    detail="no web-vitals captured in evaluated runs"))
            else:
                checks.append(QualityGateCheck(
                    name=name, passed=actual <= limit,
                    actual=f"{actual}{unit}", threshold=f"<= {limit}{unit}",
                    detail=f"worst: {worst_test.get(key)}"))

        if policy.max_lcp_ms:
            _budget_check("perf_lcp", "lcp_ms", policy.max_lcp_ms)
        if policy.max_cls:
            _budget_check("perf_cls", "cls", policy.max_cls, unit="")
        if policy.max_ttfb_ms:
            _budget_check("perf_ttfb", "ttfb_ms", policy.max_ttfb_ms)

    checks.extend(await release_readiness.readiness_gate_checks(
        session, project.id, policy, run_ids, git_commit))

    passed_gate = bool(finished_runs) and all(c.passed for c in checks)
    return QualityGateResult(
        project_id=project.id, passed=passed_gate,
        git_commit=git_commit, git_branch=git_branch,
        evaluated_run_ids=run_ids, checks=checks,
        policy_version=policy_version.version, policy_sha256=policy_version.sha256,
        ignored_run_ids=sorted(set(ignored)))


async def _readable_project(project_id: int, session: AsyncSession,
                            principal: AuthPrincipal) -> Project:
    """Viewer access, and a project-scoped API key only reads its own project
    whatever its creator could reach."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return project


@router.get("/projects/{project_id}/quality-gate", response_model=QualityGateResult)
async def evaluate_quality_gate(
    project_id: int,
    git_commit: Optional[str] = Query(None),
    git_branch: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
    # Annotated so a direct (non-HTTP) call gets plain None defaults.
    policy_version: Annotated[Optional[int], Query(ge=1)] = None,
    run_ids: Annotated[Optional[List[int]], Query()] = None,
):
    """Go/no-go for a release. Evaluates the runs for a commit (or branch, or
    the single latest run) against the project policy.

    To reproduce an earlier verdict pass the `policy_version` and the
    `evaluated_run_ids` it reported as `run_ids` (they must belong to this
    project). Cancelled / timed-out runs are never counted; they are listed
    in `ignored_run_ids`."""
    project = await _readable_project(project_id, session, principal)

    version = None
    if policy_version is not None:
        version = await gate_policy_versions.get_version(session, project_id, policy_version)
        if version is None:
            raise HTTPException(status_code=404, detail="Policy version not found")

    stmt = select(TestRun).where(TestRun.project_id == project_id)
    if run_ids:
        if len(run_ids) > 500:
            raise HTTPException(status_code=400, detail="At most 500 run_ids")
        stmt = stmt.where(TestRun.id.in_(run_ids))
    elif git_commit:
        stmt = stmt.where(TestRun.git_commit == git_commit)
    elif git_branch:
        stmt = stmt.where(TestRun.git_branch == git_branch)
    runs = (await session.exec(stmt.order_by(TestRun.created_at.desc()))).all()
    if run_ids and len({r.id for r in runs}) != len(set(run_ids)):
        # Another project's run id must not be distinguishable from a missing one.
        raise HTTPException(status_code=404, detail="Run not found in this project")

    finished = [r for r in runs if r.status in FINISHED]
    if not run_ids and not git_commit and not git_branch:
        finished = finished[:1]  # latest finished run only
    ignored = [r.id for r in runs if r.status in run_status.NO_VERDICT]
    if not run_ids and not git_commit and not git_branch:
        ignored = []          # "latest run" scope: older no-verdict runs are not in it

    return await evaluate_gate_for_runs(session, project, finished, git_commit, git_branch,
                                        policy_version=version, ignored_run_ids=ignored)


@router.get("/projects/{project_id}/release-readiness", response_model=ReleaseReadiness)
async def release_readiness_view(
    project_id: int,
    git_commit: Optional[str] = Query(None),
    git_branch: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """One go/no-go for a release with the reasons and the evidence behind
    it. Without a commit, the newest finished run (on `git_branch`, if given)
    chooses the commit, and every finished run on that commit is evaluated."""
    project = await _readable_project(project_id, session, principal)
    scope, commit, runs = await release_readiness.resolve_runs(
        session, project_id, git_commit, git_branch)
    gate = await evaluate_gate_for_runs(session, project, runs, commit, git_branch)
    return await release_readiness.build_readiness(
        session, project_id, scope, commit, git_branch, runs, gate)


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
