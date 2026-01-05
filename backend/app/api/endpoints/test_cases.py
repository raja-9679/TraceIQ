from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.storage import minio_client
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service
from app.models import (
    User, AuditLog, TestCase, TestCaseRead, TestCaseUpdate, TestSuite, TestRun, TestCaseResult
)

router = APIRouter()

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
