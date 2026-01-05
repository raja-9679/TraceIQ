from typing import List, Optional, Union, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_, and_
from sqlalchemy.orm import selectinload
from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.storage import minio_client
from app.services.test_service import test_service
from app.services.access_service import access_service
from app.models import (
    User, AuditLog, AuditLogRead, Project, UserWorkspace, UserTeam, UserProjectAccess,
    TestSuite, TestCase, TestRun, TestRunRead, TestStatus, ExecutionMode, TestCaseResult, TestCaseResultRead
)

router = APIRouter()

@router.post("/runs", response_model=Union[TestRunRead, List[TestRunRead]])
async def create_run(
    suite_id: int, 
    case_id: Optional[int] = None, 
    browser: List[str] = Query(["chromium"]), 
    device: Optional[List[str]] = Query(None), 
    session: AsyncSession = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check project access - EDITOR required for running tests
    if not await access_service.has_project_access(current_user.id, suite.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="You do not have permission to run tests in this project")

    # Get effective settings for this suite
    effective_settings = await test_service.get_effective_settings(suite_id, session)

    # Normalize devices list
    target_devices = device if device else [None]

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
                    for target_browser in browser:
                        for target_device in target_devices:
                            run = TestRun(
                                status=TestStatus.PENDING, 
                                test_suite_id=s_id, 
                                test_case_id=case.id,
                                project_id=suite.project_id,
                                suite_name=suite_path,
                                test_case_name=case.name,
                                request_headers=current_effective_settings.get("headers", {}),
                                request_params=current_effective_settings.get("params", {}),
                                allowed_domains=current_effective_settings.get("allowed_domains", []),
                                domain_settings=current_effective_settings.get("domain_settings", {}),
                                browser=target_browser,
                                device=target_device,
                                user_id=current_user.id
                            )
                            session.add(run)
                            await session.flush()
                            created_runs.append(run)

                # 2. Recurse for sub-modules
                result = await session.exec(select(TestSuite).where(TestSuite.parent_id == s_id))
                sub_modules = result.all()
                for sub in sub_modules:
                    await process_suite(sub.id, current_effective_settings)

            else: # CONTINUOUS
                for target_browser in browser:
                    for target_device in target_devices:
                        run = TestRun(
                            status=TestStatus.PENDING, 
                            test_suite_id=s_id, 
                            test_case_id=None,
                            project_id=suite.project_id,
                            suite_name=suite_path,
                            test_case_name=None,
                            request_headers=current_effective_settings.get("headers", {}),
                            request_params=current_effective_settings.get("params", {}),
                            allowed_domains=current_effective_settings.get("allowed_domains", []),
                            domain_settings=current_effective_settings.get("domain_settings", {}),
                            browser=target_browser,
                            device=target_device,
                            user_id=current_user.id
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
             for target_browser in browser:
                for target_device in target_devices:
                    suite_path = await test_service.get_suite_path(suite_id, session)
                    case = await session.get(TestCase, case_id)
                    test_case_name = case.name if case else None
                    
                    run = TestRun(
                        status=TestStatus.PENDING, 
                        test_suite_id=suite_id, 
                        test_case_id=case_id,
                        project_id=suite.project_id,
                        suite_name=suite_path,
                        test_case_name=test_case_name,
                        request_headers=effective_settings.get("headers", {}),
                        request_params=effective_settings.get("params", {}),
                        allowed_domains=effective_settings.get("allowed_domains", []),
                        domain_settings=effective_settings.get("domain_settings", {}),
                        browser=target_browser,
                        device=target_device,
                        user_id=current_user.id
                    )
                    session.add(run)
                    await session.flush()
                    created_runs.append(run)
        else:
            # Run the suite recursively
            await process_suite(suite_id, effective_settings)

        await session.commit()
        for r in created_runs: await session.refresh(r)

        # Queue tasks after commit
        from app.worker import run_test_suite
        for run in created_runs:
            try:
                run_test_suite.delay(run.id)
            except Exception as e:
                print(f"Failed to queue run {run.id}: {e}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

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
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Build query with filters and security join
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
    from app.models import TeamProjectAccess, UserTeam, UserProjectAccess
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    
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
    
    # Get total count with filters
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await session.exec(count_query)
    total = count_result.one()
    
    # Get paginated runs with eager loaded results and user
    query = query.order_by(TestRun.created_at.desc()).limit(limit).offset(offset).options(selectinload(TestRun.results), selectinload(TestRun.user))
    result = await session.exec(query)
    runs = result.all()
    
    return {
        "runs": [
            TestRunRead(
                **run.model_dump(),
                results=[TestCaseResultRead.model_validate(r) for r in run.results],
                user=run.user
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
        query = select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.results), selectinload(TestRun.user))
        result = await session.exec(query)
        run = result.first()
        
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
            
        if not await access_service.has_project_access(current_user.id, run.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")

        # Manually construct response to avoid validation issues with lazy/eager loading
        response = TestRunRead(
            **run.model_dump(),
            results=[TestCaseResultRead.model_validate(r) for r in run.results],
            user=run.user
        )
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

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
        # Delete all runs
        result = await session.exec(select(TestRun))
        runs = result.all()
        for run in runs:
            minio_client.delete_run_artifacts(run.id)
            # Delete associated TestCaseResults
            result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in result_cases.all():
                await session.delete(res)
            await session.delete(run)
        await session.commit()
        return {"status": "success", "message": f"All {len(runs)} runs deleted"}
    
    if run_ids:
        # Delete specific runs
        result = await session.exec(select(TestRun).where(TestRun.id.in_(run_ids)))
        runs = result.all()
        for run in runs:
            minio_client.delete_run_artifacts(run.id)
            # Delete associated TestCaseResults
            result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in result_cases.all():
                await session.delete(res)
            await session.delete(run)
        await session.commit()
        return {"status": "success", "message": f"{len(runs)} runs deleted"}
        
    raise HTTPException(status_code=400, detail="Must specify run_ids or all=true")

@router.get("/artifacts/{object_name:path}")
async def get_artifact_url(object_name: str, current_user: User = Depends(get_current_user)):
    url = minio_client.get_presigned_url(object_name)
    return {"url": url}

@router.get("/audit/{entity_type}/{entity_id}", response_model=List[AuditLogRead])
async def get_audit_log(entity_type: str, entity_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Basic permission check: user must belong to the workspace of the entity
    # This needs more granular logic based on entity_type
    query = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.timestamp.desc())
    
    if entity_type == 'suite':
        suite = await session.get(TestSuite, entity_id)
        if not suite or not await access_service.has_project_access(current_user.id, suite.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
            
        case_ids_result = await session.exec(select(TestCase.id).where(TestCase.test_suite_id == entity_id))
        case_ids = case_ids_result.all()
        query = query.where(or_(and_(AuditLog.entity_type == 'suite', AuditLog.entity_id == entity_id), and_(AuditLog.entity_type == 'case', AuditLog.entity_id.in_(case_ids))))
    elif entity_type == 'case':
        if not await access_service.has_test_case_access(current_user.id, entity_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
        query = query.where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
    else:
        # Fallback security
        query = query.where(AuditLog.user_id == current_user.id)

    result = await session.exec(query)
    return result.all()
