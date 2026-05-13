"""Phase C — Deployment-comparison runs.

A comparison run re-executes the same suite that produced `baseline_run_id`,
against a new `target_url`. The result inherits the baseline's
`test_suite_id` + browser + device so functional & visual deltas are
apples-to-apples.

Surface:
    POST /api/runs/comparison
        Body: { baseline_run_id, target_url, browser?, device? }
    GET  /api/runs/{id}/comparison
        Returns side-by-side baseline-vs-candidate result diff.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    ComparisonRunRequest,
    RunTrigger,
    TestCaseResult,
    TestRun,
    TestRunRead,
    TestStatus,
)
from app.services.access_service import access_service
from app.services.test_service import test_service

router = APIRouter()


@router.post("/runs/comparison", response_model=TestRunRead)
async def create_comparison_run(
    body: ComparisonRunRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestRunRead:
    baseline = await session.get(TestRun, body.baseline_run_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline run not found")
    if not await access_service.has_project_access(
        principal.user.id, baseline.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    effective_settings = await test_service.get_effective_settings(
        baseline.test_suite_id, session
    )
    suite_path = await test_service.get_suite_path(baseline.test_suite_id, session)

    run = TestRun(
        status=TestStatus.PENDING,
        test_suite_id=baseline.test_suite_id,
        test_case_id=baseline.test_case_id,
        project_id=baseline.project_id,
        suite_name=suite_path,
        test_case_name=baseline.test_case_name,
        request_headers=effective_settings.get("headers", {}),
        request_params=effective_settings.get("params", {}),
        allowed_domains=effective_settings.get("allowed_domains", []),
        domain_settings=effective_settings.get("domain_settings", {}),
        browser=body.browser or baseline.browser,
        device=body.device or baseline.device,
        user_id=principal.user.id,
        triggered_by=(
            RunTrigger.API_AGENT if principal.is_api_caller else RunTrigger.HUMAN
        ),
        agent_id=principal.agent_id,
        api_key_id=principal.api_key.id if principal.api_key else None,
        baseline_run_id=body.baseline_run_id,
        target_url=body.target_url,
        # Carry git context forward from baseline so the comparison run is
        # navigable from the same change.
        git_commit=baseline.git_commit,
        git_branch=baseline.git_branch,
        git_pr_url=baseline.git_pr_url,
        git_repo=baseline.git_repo,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    from app.worker import run_test_suite
    try:
        run_test_suite.delay(run.id)
    except Exception as exc:  # noqa: BLE001
        print(f"[ComparisonRun] could not queue {run.id}: {exc}")

    return TestRunRead(**run.model_dump(), results=[])


@router.get("/runs/{run_id}/comparison")
async def get_comparison(
    run_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    run = await session.get(TestRun, run_id)
    if not run or not run.baseline_run_id:
        raise HTTPException(status_code=404, detail="Not a comparison run")
    if not await access_service.has_project_access(
        principal.user.id, run.project_id, session
    ):
        raise HTTPException(status_code=403, detail="Access denied")
    baseline = await session.get(TestRun, run.baseline_run_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline missing")

    res = await session.exec(
        select(TestCaseResult).where(
            TestCaseResult.test_run_id.in_([baseline.id, run.id])
        )
    )
    by_run_and_name: Dict[str, Dict[int, TestCaseResult]] = {}
    for r in res.all():
        by_run_and_name.setdefault(r.test_name, {})[r.test_run_id] = r

    deltas: List[Dict[str, Any]] = []
    for name, mapping in by_run_and_name.items():
        b = mapping.get(baseline.id)
        c = mapping.get(run.id)
        delta = {
            "test_name": name,
            "baseline_status": b.status if b else None,
            "candidate_status": c.status if c else None,
            "baseline_duration_ms": b.duration_ms if b else None,
            "candidate_duration_ms": c.duration_ms if c else None,
            "regressed": bool(b and c and b.status == "passed" and c.status != "passed"),
            "recovered": bool(b and c and b.status != "passed" and c.status == "passed"),
        }
        deltas.append(delta)

    summary = {
        "regressed": sum(1 for d in deltas if d["regressed"]),
        "recovered": sum(1 for d in deltas if d["recovered"]),
        "unchanged": sum(1 for d in deltas if not d["regressed"] and not d["recovered"]),
    }
    return {
        "baseline_run_id": baseline.id,
        "candidate_run_id": run.id,
        "target_url": run.target_url,
        "summary": summary,
        "deltas": deltas,
    }
