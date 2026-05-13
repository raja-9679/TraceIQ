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
    """Delete a test case + all of its dependent records.

    Earlier this endpoint walked TestRun rows via `session.delete()` per row,
    relying on SQLAlchemy's unit-of-work to order operations. That doesn't work
    here: TestCase has no inverse relationship to TestRun, so the ORM cannot
    infer that runs must be deleted before the case. The session sometimes
    issued the case DELETE first, hitting the FK on testrun.test_case_id.

    Phase B/C/D added more references too: VisualBaseline, FlakeRecord,
    SelectorHealProposal, CaseProposal.target_case_id. Each has its own FK
    that would block the delete.

    This handler uses explicit `sqlalchemy.delete()` statements (bulk DELETEs)
    for each dependent table, flushed before the case delete. Order is
    guaranteed; partial state is impossible because everything runs in the
    same transaction.

    Special case: TestSchedule references are NOT auto-deleted. A schedule
    is a user-configured artifact; silently breaking it on case delete is
    surprising. We return 409 Conflict and force the caller to update or
    delete the schedule first.
    """
    from sqlalchemy import delete as sa_delete
    from app.models import (
        VisualBaseline,
        FlakeRecord,
        SelectorHealProposal,
        CaseProposal,
        UserTestCaseAccess,
        TestSchedule,
    )

    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")

    if not await rbac_service.has_permission(session, current_user.id, "test:create", project_id=case.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot delete test cases in this project")

    # 409 if a schedule still points at this case.
    sched_result = await session.exec(select(TestSchedule).where(TestSchedule.test_case_id == case_id))
    referencing_schedules = sched_result.all()
    if referencing_schedules:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Cannot delete: test case is referenced by one or more schedules",
                "schedule_ids": [s.id for s in referencing_schedules],
                "remedy": "Delete or update the schedule(s) to remove the reference, then retry.",
            },
        )

    # 1. Best-effort MinIO artifact cleanup for the case's runs.
    run_ids_result = await session.exec(select(TestRun.id).where(TestRun.test_case_id == case_id))
    run_ids = list(run_ids_result.all())
    for run_id in run_ids:
        try:
            minio_client.delete_run_artifacts(run_id)
        except Exception as exc:  # noqa: BLE001
            # Don't let a missing object or storage hiccup abort the delete.
            print(f"[DeleteCase] MinIO cleanup for run {run_id} failed (continuing): {exc}")

    # 2. Bulk-delete TestCaseResult rows whose run pointed at this case.
    if run_ids:
        await session.exec(
            sa_delete(TestCaseResult).where(TestCaseResult.test_run_id.in_(run_ids))
        )

    # 3. Bulk-delete the runs themselves.
    await session.exec(sa_delete(TestRun).where(TestRun.test_case_id == case_id))

    # 4. Phase B/C/D dependents.
    await session.exec(sa_delete(VisualBaseline).where(VisualBaseline.test_case_id == case_id))
    await session.exec(sa_delete(FlakeRecord).where(FlakeRecord.test_case_id == case_id))
    await session.exec(sa_delete(SelectorHealProposal).where(SelectorHealProposal.test_case_id == case_id))
    await session.exec(sa_delete(CaseProposal).where(CaseProposal.target_case_id == case_id))

    # 5. Granular per-user case access overrides.
    await session.exec(sa_delete(UserTestCaseAccess).where(UserTestCaseAccess.test_case_id == case_id))

    # 6. Flush so all dependent DELETEs hit the DB before the case DELETE.
    await session.flush()

    # 7. Now safe.
    await session.delete(case)
    audit = AuditLog(entity_type="case", entity_id=case_id, action="delete", user_id=current_user.id, changes={})
    session.add(audit)
    await session.commit()
    return {"status": "success", "message": f"Test case {case_id} deleted",
            "runs_deleted": len(run_ids)}
