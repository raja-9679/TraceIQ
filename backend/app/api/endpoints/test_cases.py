from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import AuthPrincipal, get_current_principal, get_current_user
from app.core.storage import minio_client
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service
from app.services.redaction import redact_audit_changes
from app.models import (
    User, AuditLog, TestCase, TestCaseRead, TestCaseUpdate, TestSuite, TestRun, TestCaseResult,
    ExecutorType
)

router = APIRouter()


class PlaywrightImportRequest(BaseModel):
    """Import an existing Playwright spec as a raw_playwright test case."""
    suite_id: int
    name: str
    script: str


@router.post("/cases/import-playwright", response_model=TestCaseRead)
async def import_playwright_case(
    req: PlaywrightImportRequest,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Create a raw_playwright case from an uploaded Playwright spec. The spec
    runs verbatim on the worker (see PLATFORM_VISION.md §4) — no step
    interpretation, so step-level heal/trace features don't apply. Execution is
    gated worker-side by RAW_PLAYWRIGHT_ENABLED and requires a sandboxed worker
    image; importing here only stores the script."""
    current_user = principal.user
    if not req.script or not req.script.strip():
        raise HTTPException(status_code=400, detail="script is empty")

    suite = await session.get(TestSuite, req.suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    if not await rbac_service.has_permission(session, current_user.id, "test:create", project_id=suite.project_id):
        raise HTTPException(status_code=403, detail="Permission denied: You cannot create test cases in this project")
    if (await session.exec(select(TestSuite).where(TestSuite.parent_id == req.suite_id))).first():
        raise HTTPException(status_code=400, detail="Cannot add test case to a suite that contains sub-modules")

    case = TestCase(
        name=req.name,
        executor=ExecutorType.RAW_PLAYWRIGHT,
        raw_script=req.script,
        steps=[],
        test_suite_id=req.suite_id,
        project_id=suite.project_id,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
        created_by_agent_id=principal.agent_id,
        agent_session_id=principal.agent_session_id,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)

    audit = AuditLog(entity_type="case", entity_id=case.id, action="import",
                     user_id=current_user.id, changes={"executor": "raw_playwright", "name": req.name})
    session.add(audit)
    await session.commit()
    return case

@router.post("/suites/{suite_id}/cases", response_model=TestCaseRead)
async def create_test_case(
    suite_id: int,
    case: TestCase,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    current_user = principal.user
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
    # Phase E: agent provenance.
    case.created_by_agent_id = principal.agent_id
    case.agent_session_id = principal.agent_session_id
    # A case whose steps contain a load-test spec is a load-executor case —
    # inferred here so authors never have to set the executor by hand. Native
    # mobile (mobile-*) steps likewise imply the mobile_appium executor.
    _step_types = {(s.get('type') if isinstance(s, dict) else getattr(s, 'type', None))
                   for s in (case.steps or [])}
    if 'load-test' in _step_types:
        case.executor = ExecutorType.LOAD
    elif any(t and str(t).startswith('mobile-') for t in _step_types):
        case.executor = ExecutorType.MOBILE_APPIUM
    session.add(case)
    await session.commit()
    await session.refresh(case)

    from app.services.case_revisions import record_revision
    await record_revision(session, case, "create", user_id=current_user.id,
                          agent_id=principal.agent_id)
    audit = AuditLog(entity_type="case", entity_id=case.id, action="create", user_id=current_user.id,
                     changes=redact_audit_changes(case.model_dump(mode='json')))
    session.add(audit)
    await session.commit()
    return case

class TestCaseSummary(BaseModel):
    """Slim listing row for GET /api/cases — steps/dataset deliberately omitted
    to keep list payloads small; fetch a single case for the full shape."""
    id: int
    name: str
    test_suite_id: Optional[int] = None
    project_id: Optional[int] = None
    executor: str
    tags: List[str] = []
    priority: Optional[str] = None
    is_ai_authored: bool = False
    code_paths: List[str] = []
    last_validated_commit: Optional[str] = None
    last_validated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TestCaseListResponse(BaseModel):
    items: List[TestCaseSummary]
    total: int
    limit: int
    offset: int


@router.get("/cases", response_model=TestCaseListResponse)
async def list_test_cases(
    project_id: int,
    test_suite_id: Optional[int] = None,
    tag: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """List test cases in a project (agent/MCP surface). Filters: suite, tag."""
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    stmt = select(TestCase).where(TestCase.project_id == project_id)
    if test_suite_id is not None:
        stmt = stmt.where(TestCase.test_suite_id == test_suite_id)
    result = await session.exec(stmt.order_by(TestCase.id))
    cases = result.all()

    # `tags` is a JSON column — filter in Python; project-sized lists are small.
    if tag:
        cases = [c for c in cases if tag in (c.tags or [])]

    total = len(cases)
    page = cases[offset:offset + limit]
    items = [
        TestCaseSummary(
            id=c.id,
            name=c.name,
            test_suite_id=c.test_suite_id,
            project_id=c.project_id,
            executor=(c.executor.value if hasattr(c.executor, "value") else str(c.executor)),
            tags=c.tags or [],
            priority=c.priority,
            is_ai_authored=c.is_ai_authored,
            code_paths=c.code_paths or [],
            last_validated_commit=c.last_validated_commit,
            last_validated_at=c.last_validated_at,
            updated_at=c.updated_at,
        )
        for c in page
    ]
    return TestCaseListResponse(items=items, total=total, limit=limit, offset=offset)


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
        _step_types = {(s.get('type') if isinstance(s, dict) else getattr(s, 'type', None))
                       for s in (db_case.steps or [])}
        has_load_step = 'load-test' in _step_types
        has_mobile_step = any(t and str(t).startswith('mobile-') for t in _step_types)
        if has_load_step and db_case.executor != ExecutorType.LOAD:
            db_case.executor = ExecutorType.LOAD
        elif has_mobile_step and db_case.executor != ExecutorType.MOBILE_APPIUM:
            db_case.executor = ExecutorType.MOBILE_APPIUM
        elif not has_load_step and db_case.executor == ExecutorType.LOAD:
            db_case.executor = ExecutorType.UI_PLAYWRIGHT
        elif not has_mobile_step and db_case.executor == ExecutorType.MOBILE_APPIUM:
            db_case.executor = ExecutorType.UI_PLAYWRIGHT

    if changes:
        db_case.updated_by_id = current_user.id
        db_case.updated_at = datetime.utcnow()
        session.add(db_case)
        from app.services.case_revisions import record_revision
        await record_revision(session, db_case, "update", user_id=current_user.id)
        audit = AuditLog(entity_type="case", entity_id=case_id, action="update", user_id=current_user.id,
                         changes=redact_audit_changes(changes))
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
