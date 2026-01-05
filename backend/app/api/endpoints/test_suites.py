from typing import List, Optional, Union, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_, and_
from sqlalchemy.orm import selectinload
from app.core.database import get_session
from app.core.auth import get_current_user
from app.services.test_service import test_service
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service
from app.models import (
    User, AuditLog, Project, UserWorkspace, UserTeam, UserProjectAccess, UserSystemRole, Role, Workspace, TeamProjectAccess,
    TestSuite, TestSuiteReadWithChildren, TestSuiteUpdate, TestCase, ExecutionMode
)

router = APIRouter()

@router.post("/suites", response_model=TestSuiteReadWithChildren)
async def create_test_suite(suite: TestSuite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # If no project_id provided, try to find a default project for the user
    if not suite.project_id:
        org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
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

        # Enforce Execution Mode: Parent must be SEPARATE
        if parent.execution_mode == ExecutionMode.CONTINUOUS:
            parent.execution_mode = ExecutionMode.SEPARATE
            parent.updated_at = datetime.utcnow()
            session.add(parent)
            # We should probably audit this implicit change, but for now we'll rely on the result being visible.

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
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
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
    
    # Enforce Execution Mode Logic
    if "execution_mode" in update_data and update_data["execution_mode"] == ExecutionMode.CONTINUOUS:
        # Check if it has sub-modules
        result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
        if result.first():
            raise HTTPException(status_code=400, detail="Suites with sub-modules must use separate execution mode. Remove sub-modules first.")

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

async def create_suite_from_data(data: Dict[str, Any], parent_id: Optional[int], project_id: int, session: AsyncSession, user_id: int):
    execution_mode = data.get("execution_mode", ExecutionMode.CONTINUOUS)
    if data.get("sub_modules"):
        execution_mode = ExecutionMode.SEPARATE

    new_suite = TestSuite(
        name=data.get("name", "Imported Suite"),
        description=data.get("description"),
        execution_mode=execution_mode,
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

@router.get("/suites/{suite_id}/export")
async def export_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
        
    if not await access_service.has_project_access(current_user.id, suite.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return await get_suite_export_data(suite_id, session)

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
