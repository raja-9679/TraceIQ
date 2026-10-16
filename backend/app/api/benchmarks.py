"""Cross-site benchmarking endpoints (WP12c).

    GET  /projects/{id}/benchmark-groups          list (newest first)
    POST /projects/{id}/benchmark-groups          {name, test_suite_id, targets, browser?}
         -> one suite-level run per target (target_url override), same group
    POST /benchmark-groups/{id}/rerun             same config, new group
    GET  /benchmark-groups/{id}                   matrix report
    DELETE /benchmark-groups/{id}                 the group (its runs stay, untagged)

See app/services/benchmarks.py for the target rules.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.journey_reports import project_for
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    BenchmarkGroup, PageBudgetSample, Project, RunTrigger, TestCaseResult, TestRun, TestStatus,
    TestSuite,
)
from app.services import benchmarks as bm
from app.services.audit import record as audit_record
from app.services.test_service import test_service

router = APIRouter()

BROWSERS = ("chromium", "firefox", "webkit")


class BenchmarkIn(BaseModel):
    name: str = PydField(min_length=1, max_length=200)
    test_suite_id: int
    targets: List[Any]
    browser: str = "chromium"


BenchmarkError = bm.BenchmarkError
normalize_base_url = bm.normalize_base_url


async def check_target(project: Project, url: str) -> None:
    """One target URL against net_guard + the project's egress allowlist."""
    from app.services.network_policy import effective_for_project
    try:
        await bm.check_target_reachable_policy(url, effective_for_project(project))
    except bm.BenchmarkError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def validate_targets(project: Project, raw: Any) -> List[Dict[str, str]]:
    """Shape + net_guard + the project's egress allowlist. 400 on failure."""
    from app.services.network_policy import effective_for_project
    try:
        targets = bm.clean_targets(raw)
        policy = effective_for_project(project)
        for t in targets:
            await bm.check_target_reachable_policy(t["base_url"], policy)
    except bm.BenchmarkError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return targets


def _group_out(g: BenchmarkGroup, runs: Optional[List[TestRun]] = None) -> Dict[str, Any]:
    out = {"id": g.id, "project_id": g.project_id, "test_suite_id": g.test_suite_id,
           "name": g.name, "targets": g.targets or [], "browser": g.browser,
           "created_by_id": g.created_by_id,
           "created_at": g.created_at.isoformat() if g.created_at else None}
    if runs is not None:
        statuses = [getattr(r.status, "value", r.status) for r in runs]
        out["run_ids"] = [r.id for r in runs]
        out["status"] = ("running" if any(s in ("pending", "running") for s in statuses)
                         else "complete" if statuses else "no runs")
    return out


async def _dispatch(session: AsyncSession, principal: AuthPrincipal, project: Project,
                    suite: TestSuite, name: str, targets: List[Dict[str, str]], browser: str
                    ) -> BenchmarkGroup:
    from app.services import billing as _billing
    ws_id = await _billing.workspace_id_for_project(session, project.id)
    # plan.run_quota via the policy engine, "would exceed" form (planned runs).
    from app.services.policy import PolicyContext, evaluate
    verdict = await evaluate("run.dispatch", PolicyContext.from_principal(
        principal, workspace_id=ws_id, project_id=project.id, stage="create",
        session=session, facts={"planned": len(targets)}))
    if not verdict.allowed:
        raise verdict.http_exception()
    group = BenchmarkGroup(project_id=project.id, test_suite_id=suite.id, name=name,
                           targets=targets, browser=browser, created_by_id=principal.user.id)
    session.add(group)
    await session.flush()
    # Same construction as a comparison run: a suite-level run with the
    # suite chain's settings, pointed at another base URL.
    eff = await test_service.get_effective_settings(suite.id, session)
    path = await test_service.get_suite_path(suite.id, session)
    runs = []
    for t in targets:
        run = TestRun(
            status=TestStatus.PENDING, test_suite_id=suite.id, test_case_id=None,
            project_id=project.id, suite_name=path, test_case_name=None,
            request_headers=eff.get("headers", {}), request_params=eff.get("params", {}),
            allowed_domains=eff.get("allowed_domains", []),
            domain_settings=eff.get("domain_settings", {}),
            browser=browser, user_id=principal.user.id,
            triggered_by=RunTrigger.API_AGENT if principal.is_api_caller else RunTrigger.HUMAN,
            agent_id=principal.agent_id,
            api_key_id=principal.api_key.id if principal.api_key else None,
            target_url=t["base_url"], benchmark_group_id=group.id,
            selection_mode="full")
        session.add(run)
        runs.append(run)
    await session.flush()
    await audit_record(session, entity_type="benchmark_group", entity_id=group.id, action="create",
                       workspace_id=project.workspace_id, principal=principal,
                       changes={"suite_id": suite.id, "targets": [t["base_url"] for t in targets]})
    await session.commit()
    from app.worker import run_test_suite
    for run in runs:
        try:
            run_test_suite.delay(run.id)
        except Exception as exc:  # noqa: BLE001
            print(f"[Benchmark] could not queue run {run.id}: {exc}")
    if ws_id:
        try:
            await _billing.record_usage(session, ws_id, "runs", len(runs))
        except Exception as exc:  # noqa: BLE001
            print(f"[Benchmark] usage not recorded for workspace {ws_id}: {exc}")
    from app.services.agent_controls import record_usage
    await record_usage(session, principal, ws_id, runs=len(runs))
    await session.refresh(group)
    return group


@router.get("/projects/{project_id}/benchmark-groups")
async def list_groups(project_id: int, limit: int = Query(50, ge=1, le=200),
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    groups = (await session.exec(select(BenchmarkGroup).where(
        BenchmarkGroup.project_id == project_id).order_by(BenchmarkGroup.id.desc()).limit(limit))).all()
    runs_by: Dict[int, List[TestRun]] = {}
    if groups:
        for r in (await session.exec(select(TestRun).where(
                TestRun.benchmark_group_id.in_([g.id for g in groups]),
                TestRun.project_id == project_id))).all():
            runs_by.setdefault(r.benchmark_group_id, []).append(r)
    return {"groups": [_group_out(g, runs_by.get(g.id, [])) for g in groups],
            "limits": {"min_targets": bm.MIN_TARGETS, "max_targets": bm.MAX_TARGETS},
            "browsers": list(BROWSERS)}


@router.post("/projects/{project_id}/benchmark-groups")
async def create_group(project_id: int, body: BenchmarkIn, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    project = await project_for(session, principal, project_id, "editor")
    suite = await session.get(TestSuite, body.test_suite_id)
    if not suite or suite.project_id != project_id:
        raise HTTPException(status_code=400, detail="test_suite_id does not belong to this project")
    if body.browser not in BROWSERS:
        raise HTTPException(status_code=400, detail=f"browser must be one of {', '.join(BROWSERS)}")
    targets = await validate_targets(project, body.targets)
    group = await _dispatch(session, principal, project, suite, body.name.strip(), targets, body.browser)
    return _group_out(group, [])


async def _group_for(session, principal, group_id: int, min_role="viewer"):
    g = await session.get(BenchmarkGroup, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    project = await project_for(session, principal, g.project_id, min_role)
    return g, project


@router.post("/benchmark-groups/{group_id}/rerun")
async def rerun_group(group_id: int, session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    g, project = await _group_for(session, principal, group_id, "editor")
    suite = await session.get(TestSuite, g.test_suite_id)
    if not suite or suite.project_id != project.id:
        raise HTTPException(status_code=400, detail="the suite no longer exists")
    # Re-validated: DNS or the project's network policy may have changed.
    targets = await validate_targets(project, g.targets or [])
    group = await _dispatch(session, principal, project, suite, g.name, targets, g.browser)
    return _group_out(group, [])


@router.get("/benchmark-groups/{group_id}")
async def get_group(group_id: int, session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    g, project = await _group_for(session, principal, group_id)
    # Filtered by project as well as group id, like run sweeps.
    runs = (await session.exec(select(TestRun).where(
        TestRun.benchmark_group_id == g.id, TestRun.project_id == project.id)
        .order_by(TestRun.id))).all()
    run_ids = [r.id for r in runs]
    results = (await session.exec(select(
        TestCaseResult.test_run_id, TestCaseResult.test_name, TestCaseResult.test_case_id,
        TestCaseResult.status, TestCaseResult.duration_ms).where(
        TestCaseResult.test_run_id.in_(run_ids)))).all() if run_ids else []
    samples = (await session.exec(select(PageBudgetSample.test_run_id, PageBudgetSample.metrics).where(
        PageBudgetSample.test_run_id.in_(run_ids),
        PageBudgetSample.project_id == project.id))).all() if run_ids else []
    matrix = bm.build_matrix(
        g.targets or [],
        [{"id": r.id, "target_url": r.target_url, "status": r.status, "duration_ms": r.duration_ms}
         for r in runs],
        [{"test_run_id": a, "test_name": b, "test_case_id": c, "status": d, "duration_ms": e}
         for a, b, c, d, e in results],
        [{"test_run_id": a, "metrics": b} for a, b in samples])
    from app.models import TestCase
    suite_cases = []
    if g.test_suite_id:
        ids = await test_service.subtree_case_ids(g.test_suite_id, session)
        if ids:
            suite_cases = (await session.exec(select(TestCase).where(
                TestCase.id.in_(ids), TestCase.project_id == project.id))).all()
    suite = await session.get(TestSuite, g.test_suite_id)
    return {**_group_out(g, list(runs)), "suite_name": suite.name if suite else None,
            "matrix": matrix, "absolute_goto_cases": bm.absolute_goto_cases(suite_cases)}


@router.delete("/benchmark-groups/{group_id}")
async def delete_group(group_id: int, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    g, project = await _group_for(session, principal, group_id, "editor")
    await session.exec(update(TestRun).where(TestRun.benchmark_group_id == g.id)
                       .values(benchmark_group_id=None))
    await audit_record(session, entity_type="benchmark_group", entity_id=g.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": g.name})
    await session.delete(g)
    await session.commit()
    return {"deleted": group_id}
