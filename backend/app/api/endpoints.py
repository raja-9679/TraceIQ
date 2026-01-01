from typing import List, Optional, Union, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_, and_
from app.core.database import get_session

from app.worker import run_test_suite
from app.core.storage import minio_client
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.models import (
    User, AuditLog, AuditLogRead, Project, UserWorkspace, UserProjectAccess,
    TestSuite, TestSuiteRead, TestSuiteReadWithChildren, TestSuiteUpdate, 
    TestCase, TestCaseRead, TestCaseUpdate, TestCaseResult, TestCaseResultRead,
    TestRun, TestRunRead, TestStatus, ExecutionMode, UserRead, UserSettings
)

router = APIRouter()

from app.services.test_service import test_service
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service

@router.post("/suites", response_model=TestSuiteReadWithChildren)
async def create_test_suite(suite: TestSuite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # If no project_id provided, try to find a default project for the user
    if not suite.project_id:
        org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
        from app.models import TeamProjectAccess, UserTeam, UserProjectAccess
        team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
        user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
        
        result = await session.exec(
            select(Project)
            .where(
                or_(
                    Project.id.in_(org_stmt),
                    Project.id.in_(team_stmt),
                    Project.id.in_(user_stmt)
                )
            )
            .limit(1)
        )
        default_project = result.first()
        if default_project:
            suite.project_id = default_project.id
        else:
            raise HTTPException(status_code=400, detail="Project ID is required, and no default project was found.")

    # Check project access
    from app.services.rbac_service import rbac_service
    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=suite.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You do not have permission to create suites/modules in this project")

    # Enforce unique naming among siblings
    result = await session.exec(
        select(TestSuite).where(
            TestSuite.parent_id == suite.parent_id,
            TestSuite.name == suite.name,
            TestSuite.project_id == suite.project_id
        )
    )
    if result.first():
        raise HTTPException(status_code=400, detail=f"A module with name '{suite.name}' already exists in this level")

    if suite.parent_id:
        parent = await session.get(TestSuite, suite.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent suite not found")
        
        # Enforce mutual exclusivity
        result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite.parent_id))
        if result.first():
            raise HTTPException(status_code=400, detail="Cannot add sub-module to a suite that contains test cases")

    suite.created_by_id = current_user.id
    suite.updated_by_id = current_user.id
    session.add(suite)
    await session.commit()
    await session.refresh(suite)
    
    # Audit Log
    audit = AuditLog(
        entity_type="suite",
        entity_id=suite.id,
        action="create",
        user_id=current_user.id,
        changes=suite.model_dump(mode='json')
    )
    session.add(audit)
    await session.commit()
    
    result = await session.exec(
        select(TestSuite)
        .where(TestSuite.id == suite.id)
        .options(
            selectinload(TestSuite.test_cases),
            selectinload(TestSuite.sub_modules),
            selectinload(TestSuite.parent)
        )
    )
    db_suite = result.first()
    if db_suite:
        total_cases, total_subs = await test_service.count_recursive_items(db_suite.id, session)
        effective_settings = await test_service.get_effective_settings(db_suite.id, session)
        resp = TestSuiteReadWithChildren.model_validate(db_suite)
        resp.total_test_cases = total_cases
        resp.total_sub_modules = total_subs
        resp.effective_settings = effective_settings
        
        if resp.sub_modules:
            for sub in resp.sub_modules:
                sub_cases, sub_subs = await test_service.count_recursive_items(sub.id, session)
                sub.total_test_cases = sub_cases
                sub.total_sub_modules = sub_subs
                
        return resp
    return None

@router.get("/suites", response_model=List[TestSuiteReadWithChildren])
async def list_test_suites(
    project_id: Optional[int] = None, 
    session: AsyncSession = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    # Filter by user access
    # Filter by user access
    # Check if user is Tenant Admin
    from app.services.rbac_service import rbac_service
    # Optimize: check if user is a tenant admin for ANY tenant
    # For now, let's just check if they have permission to view projects globally or in the target project's scope if provided.
    
    # If project_id is provided, we check specific permission below.
    # If NOT provided, we need to list ALL suites they have access to.
    
    # Existing logic builds a list of "allowed projects" based on direct membership.
    # We should add "Projects in Tenants where I am Tenant Admin".
    
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
    
    from app.models import TeamProjectAccess, UserTeam, UserProjectAccess, UserSystemRole, Workspace, Role
    
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    
    # Tenant Admin Logic: Get all projects in workspaces belonging to tenants managed by this user
    tenant_admin_stmt = (
        select(Project.id)
        .join(Workspace, Workspace.id == Project.workspace_id)
        .join(UserSystemRole, UserSystemRole.tenant_id == Workspace.tenant_id)
        .where(
            UserSystemRole.user_id == current_user.id,
            UserSystemRole.role_id.in_(
                select(Role.id).where(Role.name == "Tenant Admin")
            )
        )
    )

    query = select(TestSuite).where(
        or_(
            TestSuite.project_id.in_(org_stmt),
            TestSuite.project_id.in_(team_stmt),
            TestSuite.project_id.in_(user_stmt),
            TestSuite.project_id.in_(tenant_admin_stmt)
        )
    )
    
    if project_id:
        if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=project_id):
            raise HTTPException(status_code=403, detail="Access denied to this project")
        query = query.where(TestSuite.project_id == project_id)
        
    result = await session.exec(
        query.options(
            selectinload(TestSuite.test_cases),
            selectinload(TestSuite.sub_modules),
            selectinload(TestSuite.parent)
        )
    )
    suites = result.all()
    resp_suites = []
    for suite in suites:
        total_cases, total_subs = await test_service.count_recursive_items(suite.id, session)
        resp = TestSuiteReadWithChildren.model_validate(suite)
        resp.total_test_cases = total_cases
        resp.total_sub_modules = total_subs
        
        if resp.sub_modules:
            for sub in resp.sub_modules:
                sub_cases, sub_subs = await test_service.count_recursive_items(sub.id, session)
                sub.total_test_cases = sub_cases
                sub.total_sub_modules = sub_subs
                
        resp_suites.append(resp)
    return resp_suites

@router.get("/suites/{suite_id}", response_model=TestSuiteReadWithChildren)
async def get_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(
        select(TestSuite)
        .where(TestSuite.id == suite_id)
        .options(
            selectinload(TestSuite.test_cases),
            selectinload(TestSuite.sub_modules),
            selectinload(TestSuite.parent)
        )
    )
    suite = result.first()
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check project access
    from app.services.rbac_service import rbac_service
    if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=suite.project_id):
        raise HTTPException(status_code=403, detail="Access denied")
    
    total_cases, total_subs = await test_service.count_recursive_items(suite.id, session)
    effective_settings = await test_service.get_effective_settings(suite.id, session)
    resp = TestSuiteReadWithChildren.model_validate(suite)
    resp.total_test_cases = total_cases
    resp.total_sub_modules = total_subs
    resp.effective_settings = effective_settings

    if resp.sub_modules:
        for sub in resp.sub_modules:
            sub_cases, sub_subs = await test_service.count_recursive_items(sub.id, session)
            sub.total_test_cases = sub_cases
            sub.total_sub_modules = sub_subs

    return resp

@router.put("/suites/{suite_id}", response_model=TestSuiteReadWithChildren)
async def update_test_suite(suite_id: int, suite_update: TestSuiteUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    db_suite = await session.get(TestSuite, suite_id)
    if not db_suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check project access - ADMIN required for editing modules
    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=db_suite.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot edit suites in this project")

    # Update fields
    update_data = suite_update.model_dump(exclude_unset=True)
    changes = {}
    for key, value in update_data.items():
        old_value = getattr(db_suite, key)
        if old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(db_suite, key, value)
    
    if "settings" in update_data:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(db_suite, "settings")

    if changes:
        db_suite.updated_by_id = current_user.id
        db_suite.updated_at = datetime.utcnow()
        session.add(db_suite)
        audit = AuditLog(entity_type="suite", entity_id=suite_id, action="update", user_id=current_user.id, changes=changes)
        session.add(audit)
        await session.commit()
    
    result = await session.exec(
        select(TestSuite)
        .where(TestSuite.id == suite_id)
        .options(selectinload(TestSuite.test_cases), selectinload(TestSuite.sub_modules), selectinload(TestSuite.parent))
    )
    db_suite = result.first()
    
    total_cases, total_subs = await test_service.count_recursive_items(db_suite.id, session)
    effective_settings = await test_service.get_effective_settings(db_suite.id, session)
    resp = TestSuiteReadWithChildren.model_validate(db_suite)
    resp.total_test_cases = total_cases
    resp.total_sub_modules = total_subs
    resp.effective_settings = effective_settings

    if resp.sub_modules:
        for sub in resp.sub_modules:
            sub_cases, sub_subs = await test_service.count_recursive_items(sub.id, session)
            sub.total_test_cases = sub_cases
            sub.total_sub_modules = sub_subs

    return resp

@router.delete("/suites/{suite_id}")
async def delete_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check project access - ADMIN required for deleting modules
    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=suite.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot delete suites in this project")
    
    await test_service.recursive_delete_suite(suite_id, session)
    
    audit = AuditLog(entity_type="suite", entity_id=suite_id, action="delete", user_id=current_user.id, changes={})
    session.add(audit)
    await session.commit()
    return {"status": "success", "message": f"Suite {suite_id} and all its contents deleted"}

@router.post("/suites/{suite_id}/cases", response_model=TestCaseRead)
async def create_test_case(suite_id: int, case: TestCase, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check project access
    if not await rbac_service.has_permission(session, current_user.id, "test:create", project_id=suite.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot create test cases in this project")

    # Enforce mutual exclusivity
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    if result.first():
        raise HTTPException(status_code=400, detail="Cannot add test case to a suite that contains sub-modules")

    case.test_suite_id = suite_id
    case.project_id = suite.project_id
    case.created_by_id = current_user.id
    case.updated_by_id = current_user.id
    session.add(case)
    await session.commit()
    await session.refresh(case)
    
    audit = AuditLog(entity_type="case", entity_id=case.id, action="create", user_id=current_user.id, changes=case.model_dump(mode='json'))
    session.add(audit)
    await session.commit()
    return case

@router.get("/cases/{case_id}", response_model=TestCaseRead)
async def get_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    if not await access_service.has_test_case_access(current_user.id, case_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
        
    return case

@router.put("/cases/{case_id}", response_model=TestCaseRead)
async def update_test_case(case_id: int, case_update: TestCaseUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    db_case = await session.get(TestCase, case_id)
    if not db_case:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    if not await rbac_service.has_permission(session, current_user.id, "test:create", project_id=db_case.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot update test cases in this project")

    case_data = case_update.model_dump(exclude_unset=True)
    changes = {}
    for key, value in case_data.items():
        old_value = getattr(db_case, key)
        if old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(db_case, key, value)
            
    if "steps" in case_data:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(db_case, "steps")

    if changes:
        db_case.updated_by_id = current_user.id
        db_case.updated_at = datetime.utcnow()
        session.add(db_case)
        audit = AuditLog(entity_type="case", entity_id=case_id, action="update", user_id=current_user.id, changes=changes)
        session.add(audit)
        await session.commit()
        await session.refresh(db_case)
        
    return db_case

@router.delete("/cases/{case_id}")
async def delete_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    if not await rbac_service.has_permission(session, current_user.id, "test:create", project_id=case.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot delete test cases in this project")
    
    # Delete associated TestRuns
    result_runs = await session.exec(select(TestRun).where(TestRun.test_case_id == case_id))
    for run in result_runs.all():
        run_results = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
        for res in run_results.all():
            await session.delete(res)
            
        minio_client.delete_run_artifacts(run.id)
        await session.delete(run)

    await session.delete(case)
    audit = AuditLog(entity_type="case", entity_id=case_id, action="delete", user_id=current_user.id, changes={})
    session.add(audit)
    await session.commit()
    return {"status": "success", "message": f"Test case {case_id} deleted"}

# --- Import/Export ---

@router.get("/suites/{suite_id}/export")
async def export_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
        
    if not await access_service.has_project_access(current_user.id, suite.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return await get_suite_export_data(suite_id, session)

async def get_suite_export_data(suite_id: int, session: AsyncSession):
    suite = await session.get(TestSuite, suite_id)
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    cases = result.all()
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    subs = result.all()
    
    return {
        "name": suite.name,
        "description": suite.description,
        "execution_mode": suite.execution_mode,
        "settings": suite.settings,
        "inherit_settings": suite.inherit_settings,
        "test_cases": [{"name": c.name, "steps": c.steps} for c in cases],
        "sub_modules": [await get_suite_export_data(sub.id, session) for sub in subs]
    }

@router.post("/suites/import-suite")
async def import_top_level_suite(suite_data: Dict[str, Any], project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")

    new_suite = await create_suite_from_data(suite_data, None, project_id, session, current_user.id)
    await session.commit()
    
    audit = AuditLog(entity_type="suite", entity_id=new_suite.id, action="import", user_id=current_user.id, changes={"source": "import"})
    session.add(audit)
    await session.commit()
    return {"status": "success", "id": new_suite.id}

async def create_suite_from_data(data: Dict[str, Any], parent_id: Optional[int], project_id: int, session: AsyncSession, user_id: int):
    new_suite = TestSuite(
        name=data.get("name", "Imported Suite"),
        description=data.get("description"),
        execution_mode=data.get("execution_mode", ExecutionMode.CONTINUOUS),
        settings=data.get("settings", {"headers": {}, "params": {}}),
        inherit_settings=data.get("inherit_settings", True),
        parent_id=parent_id,
        project_id=project_id,
        created_by_id=user_id,
        updated_by_id=user_id
    )
    session.add(new_suite)
    await session.flush()
    
    for case_data in data.get("test_cases", []):
        new_case = TestCase(
            name=case_data.get("name"),
            steps=case_data.get("steps", []),
            test_suite_id=new_suite.id,
            project_id=project_id,
            created_by_id=user_id,
            updated_by_id=user_id
        )
        session.add(new_case)
        
    for sub_data in data.get("sub_modules", []):
        await create_suite_from_data(sub_data, new_suite.id, project_id, session, user_id)
        
    return new_suite

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
                            try:
                                from app.worker import run_test_suite
                                run_test_suite.delay(run.id)
                            except Exception as e:
                                print(f"Failed to queue run {run.id}: {e}")

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
                        try:
                            from app.worker import run_test_suite
                            run_test_suite.delay(run.id)
                        except Exception as e:
                            print(f"Failed to queue run {run.id}: {e}")

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
                    try:
                        from app.worker import run_test_suite
                        run_test_suite.delay(run.id)
                    except Exception as e:
                        print(f"Failed to queue run {run.id}: {e}")
        else:
            # Run the suite recursively
            await process_suite(suite_id, effective_settings)

        await session.commit()
        for r in created_runs: await session.refresh(r)

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
