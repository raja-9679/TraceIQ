from datetime import datetime
from typing import List, Optional, Union, Dict, Any
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_, and_, delete
from sqlalchemy import update
from sqlalchemy.orm import selectinload
from app.core.database import get_session
from app.core.auth import AuthPrincipal, get_current_principal, get_current_user
from app.core.storage import minio_client
from app.services.test_service import test_service
from app.services.access_service import access_service
from app.models import (
    User, AuditLog, AuditLogRead, Project, UserWorkspace, UserTeam, UserProjectAccess,
    TeamProjectAccess, TestSuite, TestCase, TestRun, TestRunRead, TestStatus, ExecutionMode,
    TestCaseResult, TestCaseResultRead, RunTrigger,
    SecurityFinding, SecurityFindingRead, SecurityScanResult
)

router = APIRouter()


class RunCreateContext(BaseModel):
    """Optional change-context attached to a run.

    AI agents and CI systems POST this body when triggering a regression
    check; humans triggering a run from the UI leave it empty.
    """
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    git_pr_url: Optional[str] = None
    git_repo: Optional[str] = None
    triggered_by: Optional[RunTrigger] = None
    agent_id: Optional[str] = None
    # Run against a specific ProjectEnvironment; None uses the project's
    # default environment (if one is configured).
    environment_id: Optional[int] = None
    # Route this run's jobs to a developer's local worker (started with
    # `npm run worker:local` in execution-engine) instead of the server
    # workers — lets agents/CI test a dev server on localhost.
    local_worker_id: Optional[str] = None
    # Phase MOB: which uploaded MobileAppBuild a mobile_appium run installs
    # and tests. Required for suites whose cases use the mobile executor.
    app_build_id: Optional[int] = None


def _build_run_defaults(principal: AuthPrincipal, body: Optional[RunCreateContext]) -> Dict[str, Any]:
    """Compute the run-level fields that are constant across all created runs."""
    ctx = body or RunCreateContext()
    # If the call came in via an API key, default `triggered_by` to api_agent.
    # Explicit body value still wins so a CI system that authenticates with
    # an API key can label its runs as `ci`.
    if ctx.triggered_by is not None:
        triggered_by = ctx.triggered_by
    elif principal.is_api_caller:
        triggered_by = RunTrigger.API_AGENT
    else:
        triggered_by = RunTrigger.HUMAN

    agent_id = ctx.agent_id or principal.agent_id
    api_key_id = principal.api_key.id if principal.api_key else None
    return {
        "git_commit": ctx.git_commit,
        "git_branch": ctx.git_branch,
        "git_pr_url": ctx.git_pr_url,
        "git_repo": ctx.git_repo,
        "triggered_by": triggered_by,
        "agent_id": agent_id,
        "api_key_id": api_key_id,
        "local_worker_id": (ctx.local_worker_id or None) and str(ctx.local_worker_id)[:64],
    }


@router.post("/runs", response_model=Union[TestRunRead, List[TestRunRead]])
async def create_run(
    suite_id: int,
    case_id: Optional[int] = None,
    browser: Optional[List[str]] = Query(None),
    device: Optional[List[str]] = Query(None),
    tags: Optional[List[str]] = Query(None),
    context: Optional[RunCreateContext] = Body(None),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    current_user = principal.user
    run_defaults = _build_run_defaults(principal, context)
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")

    # Check project access - EDITOR required for running tests
    if not await access_service.has_project_access(current_user.id, suite.project_id, session, min_role="editor"):
        raise HTTPException(
            status_code=403, detail="You do not have permission to run tests in this project")

    # Plan quota: enforce the workspace's monthly run allowance (0 = unlimited).
    from app.services import billing as _billing
    _ws_id = await _billing.workspace_id_for_project(session, suite.project_id)
    if _ws_id:
        allowed, used, limit = await _billing.check_quota(session, _ws_id, "runs")
        if not allowed:
            raise HTTPException(
                status_code=402,
                detail=f"Monthly run quota reached ({used}/{limit}). Upgrade the workspace plan to run more.")

    # Get effective settings for this suite
    effective_settings = await test_service.get_effective_settings(suite_id, session)

    # Pin the run to an uploaded app binary for mobile_appium cases; the
    # dispatcher presigns it into the job payload.
    if context and context.app_build_id:
        from app.models import MobileAppBuild
        build = await session.get(MobileAppBuild, context.app_build_id)
        if not build or build.project_id != suite.project_id:
            raise HTTPException(
                status_code=400,
                detail="app_build_id does not belong to this project")
        run_defaults["app_build_id"] = context.app_build_id

    # Pin the run to a specific environment when requested; the dispatcher
    # falls back to the project's default environment when this is None.
    if context and context.environment_id:
        from app.models import ProjectEnvironment
        env = await session.get(ProjectEnvironment, context.environment_id)
        if not env or env.project_id != suite.project_id:
            raise HTTPException(
                status_code=400,
                detail="environment_id does not belong to this project")
        run_defaults["environment_id"] = context.environment_id

    # ── Execution-matrix resolution ──────────────────────────────────────
    # Precedence per test case:
    #   explicit request params  >  case.run_matrix  >  suite chain settings
    #   (browsers/devices, inherited like headers)  >  the triggering user's
    #   Settings defaults  >  chromium / no device emulation.
    from app.settings_models import UserSettings
    user_settings = (await session.exec(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )).first()

    def _user_default_browsers() -> List[str]:
        if user_settings and user_settings.multi_browser_enabled and user_settings.selected_browsers:
            return user_settings.selected_browsers
        if user_settings and user_settings.default_browser:
            return [user_settings.default_browser]
        return ["chromium"]

    def _user_default_devices() -> Optional[List[str]]:
        if user_settings and user_settings.multi_device_enabled and user_settings.selected_devices:
            return user_settings.selected_devices
        return None

    def _resolve_matrix(case: Optional[TestCase], eff: Dict[str, Any]):
        case_matrix = (getattr(case, "run_matrix", None) or {}) if case else {}
        browsers = (browser
                    or case_matrix.get("browsers")
                    or eff.get("browsers")
                    or _user_default_browsers())
        devices = (device
                   or case_matrix.get("devices")
                   or eff.get("devices")
                   or _user_default_devices())
        return browsers, (devices if devices else [None])

    created_runs = []

    try:
        # Recursive function to process suites and create runs
        async def process_suite(s_id: int, parent_settings: Dict[str, Any]):
            current_suite = await session.get(TestSuite, s_id)
            if not current_suite:
                return

            # Calculate effective settings for this level
            current_effective_settings = await test_service.get_effective_settings(s_id, session)
            suite_path = await test_service.get_suite_path(s_id, session)

            if current_suite.execution_mode == ExecutionMode.SEPARATE:
                # 1. Create individual runs for direct test cases
                result = await session.exec(select(TestCase).where(TestCase.test_suite_id == s_id))
                direct_cases = result.all()

                for case in direct_cases:
                    case_browsers, case_devices = _resolve_matrix(case, current_effective_settings)
                    for target_browser in case_browsers:
                        for target_device in case_devices:
                            run = TestRun(
                                status=TestStatus.PENDING,
                                test_suite_id=s_id,
                                test_case_id=case.id,
                                project_id=suite.project_id,
                                suite_name=suite_path,
                                test_case_name=case.name,
                                request_headers=current_effective_settings.get(
                                    "headers", {}),
                                request_params=current_effective_settings.get(
                                    "params", {}),
                                allowed_domains=current_effective_settings.get(
                                    "allowed_domains", []),
                                domain_settings=current_effective_settings.get(
                                    "domain_settings", {}),
                                browser=target_browser,
                                device=target_device,
                                user_id=current_user.id,
                                **run_defaults,
                            )
                            session.add(run)
                            await session.flush()
                            created_runs.append(run)

                # 2. Recurse for sub-modules
                result = await session.exec(select(TestSuite).where(TestSuite.parent_id == s_id))
                sub_modules = result.all()
                for sub in sub_modules:
                    await process_suite(sub.id, current_effective_settings)

            else:  # CONTINUOUS
                suite_browsers, suite_devices = _resolve_matrix(None, current_effective_settings)
                for target_browser in suite_browsers:
                    for target_device in suite_devices:
                        run = TestRun(
                            status=TestStatus.PENDING,
                            test_suite_id=s_id,
                            test_case_id=None,
                            project_id=suite.project_id,
                            suite_name=suite_path,
                            test_case_name=None,
                            request_headers=current_effective_settings.get(
                                "headers", {}),
                            request_params=current_effective_settings.get(
                                "params", {}),
                            allowed_domains=current_effective_settings.get(
                                "allowed_domains", []),
                            domain_settings=current_effective_settings.get(
                                "domain_settings", {}),
                            browser=target_browser,
                            device=target_device,
                            user_id=current_user.id,
                            **run_defaults,
                        )
                        session.add(run)
                        await session.flush()
                        created_runs.append(run)

                # 2. Recurse for sub-modules to find SEPARATE modules
                async def find_and_process_separate_descendants(p_id):
                    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == p_id))
                    subs = result.all()
                    for sub in subs:
                        if sub.execution_mode == ExecutionMode.SEPARATE:
                            await process_suite(sub.id, current_effective_settings)
                        else:
                            await find_and_process_separate_descendants(sub.id)

                await find_and_process_separate_descendants(s_id)

        # If a specific case is requested, just run that case
        if case_id:
            suite_path = await test_service.get_suite_path(suite_id, session)
            case = await session.get(TestCase, case_id)
            test_case_name = case.name if case else None
            case_browsers, case_devices = _resolve_matrix(case, effective_settings)
            for target_browser in case_browsers:
                for target_device in case_devices:
                    run = TestRun(
                        status=TestStatus.PENDING,
                        test_suite_id=suite_id,
                        test_case_id=case_id,
                        project_id=suite.project_id,
                        suite_name=suite_path,
                        test_case_name=test_case_name,
                        request_headers=effective_settings.get("headers", {}),
                        request_params=effective_settings.get("params", {}),
                        allowed_domains=effective_settings.get(
                            "allowed_domains", []),
                        domain_settings=effective_settings.get(
                            "domain_settings", {}),
                        browser=target_browser,
                        device=target_device,
                        user_id=current_user.id,
                        **run_defaults,
                    )
                    session.add(run)
                    await session.flush()
                    created_runs.append(run)
        else:
            # Run the suite recursively
            await process_suite(suite_id, effective_settings)

        await session.commit()
        for r in created_runs:
            await session.refresh(r)

        # Queue tasks after commit
        from app.worker import run_test_suite
        for run in created_runs:
            try:
                run_test_suite.delay(run.id, tags=tags)
            except Exception as e:
                print(f"Failed to queue run {run.id}: {e}")

        # Meter the run against the workspace's monthly quota (best-effort).
        if _ws_id and created_runs:
            try:
                await _billing.record_usage(session, _ws_id, "runs", len(created_runs))
            except Exception as e:
                print(f"Failed to record run usage for workspace {_ws_id}: {e}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Internal Server Error: {str(e)}")

    return [
        TestRunRead(
            **run.model_dump(),
            results=[]
        ) for run in created_runs
    ]


@router.get("/runs")
async def get_runs(
    project_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    status: Optional[str] = None,
    browser: Optional[str] = None,
    device: Optional[str] = None,
    git_commit: Optional[str] = None,
    git_branch: Optional[str] = None,
    triggered_by: Optional[str] = None,
    agent_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Build query with filters and security join
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id ==
                                       Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
    from app.models import TeamProjectAccess, UserTeam, UserProjectAccess
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(
        UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id ==
                                        Project.id).where(UserProjectAccess.user_id == current_user.id)

    query = select(TestRun).where(
        or_(
            TestRun.project_id.in_(org_stmt),
            TestRun.project_id.in_(team_stmt),
            TestRun.project_id.in_(user_stmt)
        )
    )

    if project_id:
        if not await access_service.has_project_access(current_user.id, project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
        query = query.where(TestRun.project_id == project_id)

    # Apply filters
    if search:
        query = query.where(
            (TestRun.suite_name.contains(search)) |
            (TestRun.test_case_name.contains(search))
        )
    if status:
        query = query.where(TestRun.status == status)
    if browser:
        query = query.where(TestRun.browser == browser)
    if device:
        query = query.where(TestRun.device == device)
    if git_commit:
        query = query.where(TestRun.git_commit == git_commit)
    if git_branch:
        query = query.where(TestRun.git_branch == git_branch)
    if triggered_by:
        query = query.where(TestRun.triggered_by == triggered_by)
    if agent_id:
        query = query.where(TestRun.agent_id == agent_id)

    # Get total count with filters
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await session.exec(count_query)
    total = count_result.one()

    from sqlalchemy.orm import defer
    
    # Get paginated runs without heavyweight results
    query = query.order_by(TestRun.created_at.desc()).limit(limit).offset(
        offset).options(
            selectinload(TestRun.user),
            defer(TestRun.network_events),
            defer(TestRun.execution_log),
            defer(TestRun.screenshots),
            defer(TestRun.request_headers),
            defer(TestRun.response_headers),
            defer(TestRun.request_params),
            defer(TestRun.allowed_domains),
            defer(TestRun.domain_settings),
            defer(TestRun.ai_analysis)
        )
    result = await session.exec(query)
    runs = result.all()
    
    # Expunge to prevent accidental lazy loading
    for r in runs:
        session.expunge(r)

    return {
        "runs": [
            TestRunRead(
                **{k: v for k, v in run.__dict__.items() if not k.startswith('_')},
                results=[],
            ) for run in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/runs/{run_id}", response_model=TestRunRead)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        # Eager load results and user
        query = select(TestRun).where(TestRun.id == run_id).options(
            selectinload(TestRun.results), selectinload(TestRun.user))
        result = await session.exec(query)
        run = result.first()

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        if not await access_service.has_project_access(current_user.id, run.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")

        # Manually construct response to avoid validation issues with lazy/eager loading
        response = TestRunRead(
            **run.model_dump(),
            results=[TestCaseResultRead.model_validate(
                r) for r in run.results],
            user=run.user
        )
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Internal Server Error: {str(e)}")


def _severity_counts(findings) -> Dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        if f.severity in counts:
            counts[f.severity] += 1
    return counts


@router.post("/runs/{run_id}/security-scan", response_model=SecurityScanResult)
async def security_scan_run(run_id: int, session: AsyncSession = Depends(get_session),
                            current_user: User = Depends(get_current_user)):
    """Passive security scan of a run's already-captured responses (re-runnable).

    Read-only against the target — it only re-analyses recorded data. Replaces
    any prior passive findings for the run.
    """
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(current_user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.passive_security import analyze_run, summarize
    findings = analyze_run(run)

    await session.exec(
        delete(SecurityFinding).where(
            SecurityFinding.run_id == run_id,
            SecurityFinding.scan_type == "passive"))
    rows = []
    for f in findings:
        row = SecurityFinding(
            run_id=run.id, project_id=run.project_id, scan_type="passive",
            category=f["category"], severity=f["severity"], title=f["title"],
            description=f["description"], evidence=f["evidence"], target_url=f["target_url"])
        session.add(row)
        rows.append(row)
    await session.commit()
    for r in rows:
        await session.refresh(r)

    return SecurityScanResult(
        run_id=run.id, scan_type="passive", counts=summarize(findings),
        findings=[SecurityFindingRead.model_validate(r, from_attributes=True) for r in rows])


@router.get("/runs/{run_id}/security-findings", response_model=SecurityScanResult)
async def get_security_findings(run_id: int, session: AsyncSession = Depends(get_session),
                                current_user: User = Depends(get_current_user)):
    """List stored security findings for a run (populated at finalize or on demand)."""
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(current_user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    severity_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rows = (await session.exec(
        select(SecurityFinding).where(SecurityFinding.run_id == run_id))).all()
    rows.sort(key=lambda r: severity_rank.get(r.severity, 9))

    return SecurityScanResult(
        run_id=run_id, scan_type="passive", counts=_severity_counts(rows),
        findings=[SecurityFindingRead.model_validate(r, from_attributes=True) for r in rows])


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if not await access_service.has_project_access(current_user.id, run.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete artifacts from MinIO
    minio_client.delete_run_artifacts(run_id)

    # Delete associated TestCaseResults
    result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run_id))
    for res in result_cases.all():
        await session.delete(res)

    # Delete from DB
    await session.delete(run)
    await session.commit()

    return {"status": "success", "message": f"Run {run_id} deleted"}


@router.delete("/runs")
async def delete_runs(
    run_ids: Optional[List[int]] = Query(None),
    all: bool = False,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    if all:
        # Scope to projects the current user has access to — never delete across all tenants
        org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
        team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
        user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)

        result = await session.exec(
            select(TestRun).where(
                or_(
                    TestRun.project_id.in_(org_stmt),
                    TestRun.project_id.in_(team_stmt),
                    TestRun.project_id.in_(user_stmt),
                )
            )
        )
        runs = result.all()
        # Membership scoping above limits the blast radius; still require the
        # editor role per project so viewers cannot bulk-delete.
        editable: dict[int, bool] = {}
        deleted = 0
        for run in runs:
            if run.project_id not in editable:
                editable[run.project_id] = await access_service.has_project_access(
                    current_user.id, run.project_id, session, min_role="editor")
            if not editable[run.project_id]:
                continue
            minio_client.delete_run_artifacts(run.id)
            result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in result_cases.all():
                await session.delete(res)
            await session.delete(run)
            deleted += 1
        await session.commit()
        return {"status": "success", "message": f"All {deleted} runs deleted"}

    if run_ids:
        # Delete specific runs — only those in projects the caller can edit.
        result = await session.exec(select(TestRun).where(TestRun.id.in_(run_ids)))
        runs = result.all()
        denied = 0
        deleted = 0
        for run in runs:
            if not await access_service.has_project_access(
                    current_user.id, run.project_id, session, min_role="editor"):
                denied += 1
                continue
            minio_client.delete_run_artifacts(run.id)
            # Delete associated TestCaseResults
            result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in result_cases.all():
                await session.delete(res)
            await session.delete(run)
            deleted += 1
        await session.commit()
        if denied and not deleted:
            raise HTTPException(status_code=403, detail="Access denied for all requested runs")
        return {"status": "success", "message": f"{deleted} runs deleted" + (f", {denied} denied" if denied else "")}

    raise HTTPException(
        status_code=400, detail="Must specify run_ids or all=true")


@router.get("/artifacts/{object_name:path}")
async def get_artifact_url(
    object_name: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # Artifact keys are laid out as runs/{run_id}/... — scope access to the
    # run's project so a signed URL can't be minted for another tenant's
    # traces/videos (which contain response bodies and headers).
    import re
    m = re.match(r"^runs/(\d+)/", object_name)
    if not m:
        raise HTTPException(status_code=400, detail="Unrecognized artifact path")
    run = await session.get(TestRun, int(m.group(1)))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await access_service.has_project_access(current_user.id, run.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    url = minio_client.get_presigned_url(object_name)
    return {"url": url}


@router.get("/audit/{entity_type}/{entity_id}", response_model=List[AuditLogRead])
async def get_audit_log(entity_type: str, entity_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Basic permission check: user must belong to the workspace of the entity
    # This needs more granular logic based on entity_type
    query = select(AuditLog).options(selectinload(AuditLog.user)
                                     ).order_by(AuditLog.timestamp.desc())

    if entity_type == 'suite':
        suite = await session.get(TestSuite, entity_id)
        if not suite or not await access_service.has_project_access(current_user.id, suite.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")

        case_ids_result = await session.exec(select(TestCase.id).where(TestCase.test_suite_id == entity_id))
        case_ids = case_ids_result.all()
        query = query.where(or_(and_(AuditLog.entity_type == 'suite', AuditLog.entity_id == entity_id), and_(
            AuditLog.entity_type == 'case', AuditLog.entity_id.in_(case_ids))))
    elif entity_type == 'case':
        if not await access_service.has_test_case_access(current_user.id, entity_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
        query = query.where(AuditLog.entity_type ==
                            entity_type, AuditLog.entity_id == entity_id)
    else:
        # Fallback security
        query = query.where(AuditLog.user_id == current_user.id)

    result = await session.exec(query)
    return result.all()


@router.post("/runs/{run_id}/webhook")
async def process_webhook(
    run_id: int,
    payload: Dict[str, Any],
    x_traceiq_secret: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session)
):
    import logging
    from app.core.config import settings as _cfg
    _logger = logging.getLogger(__name__)
    expected = _cfg.WEBHOOK_SECRET or _cfg.SECRET_KEY
    if not x_traceiq_secret or x_traceiq_secret != expected:
        _logger.warning("[Webhook] Rejected request with invalid or missing secret for run %s", run_id)
        raise HTTPException(status_code=403, detail="Invalid or missing webhook secret")

    await test_service.process_test_run_result(run_id, payload, session)
    return {"status": "received"}


@router.post("/runs/{run_id}/finalize")
async def finalize_test_run(
    run_id: int,
    payload: Dict[str, Any],
    x_traceiq_secret: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session)
):
    """
    Called by Execution Controller when a run completes.

    Receives curated results with optional AI analysis.
    Backend handles:
    1. Storing AI analysis to DB
    2. Triggering notifications based on user preferences
    """
    import logging
    from app.core.config import settings as _cfg
    _logger = logging.getLogger(__name__)
    expected = _cfg.WEBHOOK_SECRET or _cfg.SECRET_KEY
    if not x_traceiq_secret or x_traceiq_secret != expected:
        _logger.warning("[Finalize] Rejected request with invalid or missing secret for run %s", run_id)
        raise HTTPException(status_code=403, detail="Internal service access only")

    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    # Idempotency guard. Workers retry finalize on network failure, and every
    # task below has an externally-visible side effect (customer email, outbound
    # webhook into customer CI, monitor uptime state). Claim the run atomically
    # so a concurrent redelivery loses the race and returns without re-firing.
    claim = await session.exec(
        update(TestRun)
        .where(TestRun.id == run_id, TestRun.finalized_at.is_(None))
        .values(finalized_at=datetime.utcnow())
    )
    await session.commit()
    if claim.rowcount == 0:
        _logger.info(
            "[Finalize] Run %s already finalized at %s — skipping side effects",
            run_id, run.finalized_at,
        )
        return {"status": "already_finalized", "run_id": run_id}

    # Store AI analysis if provided
    ai_analysis = payload.get('aiAnalysis')
    if ai_analysis:
        await session.refresh(run)
        run.ai_analysis = ai_analysis
        session.add(run)
        await session.commit()
        print(f"[Finalize] Stored AI analysis for run {run_id}")

    # Trigger notifications based on user/project preferences
    # This is done via Celery task for reliability
    try:
        from app.tasks.notification_tasks import send_run_notifications
        send_run_notifications.delay(run_id, payload)
        print(f"[Finalize] Queued notifications for run {run_id}")
    except Exception as e:
        print(
            f"[Finalize] Failed to queue notifications for run {run_id}: {e}")
        # Don't fail the request - notifications are best-effort

    # Fan-out workspace-registered outbound webhooks (CI bots, agent callbacks).
    # Best-effort; failures inside the task do not affect the run.
    try:
        from app.tasks.outbound_webhook_tasks import dispatch_run_webhooks
        dispatch_run_webhooks.delay(run_id)
        print(f"[Finalize] Queued outbound webhooks for run {run_id}")
    except Exception as e:
        print(f"[Finalize] Failed to queue outbound webhooks for run {run_id}: {e}")

    # Phase B: proactive selector-heal pass. Best-effort; only fires when
    # PROACTIVE_HEAL_ENABLED=true and an LLM provider is configured.
    try:
        from app.tasks.heal_tasks import propose_selector_heals_for_run
        propose_selector_heals_for_run.delay(run_id)
    except Exception as e:
        print(f"[Finalize] Failed to queue heal proposals for run {run_id}: {e}")

    # Synthetic monitoring: if this run belongs to a monitor schedule, record a
    # health check and fire streak-based alerts. No-op for non-monitor runs.
    try:
        from app.tasks.monitor_tasks import evaluate_monitor_for_run
        evaluate_monitor_for_run.delay(run_id)
    except Exception as e:
        print(f"[Finalize] Failed to queue monitor evaluation for run {run_id}: {e}")

    # Passive security scan: analyse already-captured responses for missing
    # security headers, insecure cookies, etc. Best-effort; read-only.
    try:
        from app.tasks.security_tasks import scan_run_passive
        scan_run_passive.delay(run_id)
    except Exception as e:
        print(f"[Finalize] Failed to queue passive security scan for run {run_id}: {e}")

    # Failure triage: fingerprint failing results into clusters so identical
    # root causes de-duplicate across runs. Best-effort.
    try:
        from app.tasks.triage_tasks import cluster_run_failures
        cluster_run_failures.delay(run_id)
    except Exception as e:
        print(f"[Finalize] Failed to queue failure clustering for run {run_id}: {e}")

    return {"status": "finalized", "run_id": run_id}


@router.post("/runs/{run_id}/force-complete")
async def force_complete_test_run(
    run_id: int,
    status: Optional[TestStatus] = TestStatus.ERROR,
    error_message: Optional[str] = "Manually marked as complete by administrator",
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Manually force-complete a stuck test run.
    Requires EDITOR permission on the project.
    """
    # Get the test run
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    # Check if user has EDITOR access to the project
    if not await access_service.has_project_access(current_user.id, run.project_id, session, min_role="editor"):
        raise HTTPException(
            status_code=403, detail="You do not have permission to modify this test run")

    # Check if test is already in a final state
    if run.status in [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]:
        raise HTTPException(
            status_code=400,
            detail=f"Test run is already in final state: {run.status}"
        )

    # Update the test run
    run.status = status
    run.error_message = error_message

    session.add(run)

    # Get project to find workspace_id for audit log
    project = await session.get(Project, run.project_id)
    workspace_id = project.workspace_id if project else None

    # Log the manual intervention in audit log
    audit_log = AuditLog(
        user_id=current_user.id,
        workspace_id=workspace_id,
        action="force_complete_test_run",
        entity_type="test_run",
        entity_id=run_id,
        changes={
            "status": status.value,
            "error_message": error_message,
            "previous_status": TestStatus.RUNNING.value
        }
    )
    session.add(audit_log)

    await session.commit()
    await session.refresh(run)

    return {
        "status": "success",
        "run_id": run_id,
        "new_status": run.status,
        "message": "Test run manually completed"
    }
