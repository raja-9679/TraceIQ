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
    User, AuditLog, AuditLogRead,
    TestSuite, TestSuiteRead, TestSuiteReadWithChildren, TestSuiteUpdate, 
    TestCase, TestCaseRead, TestCaseUpdate, TestCaseResult, TestCaseResultRead,
    TestRun, TestRunRead, TestStatus, ExecutionMode, UserRead, UserSettings
)

router = APIRouter()

@router.post("/suites", response_model=TestSuiteReadWithChildren)
async def create_test_suite(suite: TestSuite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Enforce unique naming among siblings
    result = await session.exec(
        select(TestSuite).where(
            TestSuite.parent_id == suite.parent_id,
            TestSuite.name == suite.name
        )
    )
    if result.first():
        raise HTTPException(status_code=400, detail=f"A module with name '{suite.name}' already exists in this level")

    if suite.parent_id:
        parent = await session.get(TestSuite, suite.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent suite not found")
        
        # Enforce mutual exclusivity: Parent cannot have test cases if it has sub-modules
        result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite.parent_id))
        if result.first():
            raise HTTPException(status_code=400, detail="Cannot add sub-module to a suite that contains test cases")

        # Enforce Execution Mode Rule: Parent must be in 'separate' mode if it has sub-modules
        if parent.execution_mode == ExecutionMode.CONTINUOUS:
            parent.execution_mode = ExecutionMode.SEPARATE
            session.add(parent)
            # We don't need to commit here, the final commit will handle it


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
    
    # Fetch again with relationships to satisfy TestSuiteRead and avoid lazy loading errors
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
        total_cases, total_subs = await count_recursive_items(db_suite.id, session)
        effective_settings = await get_effective_settings(db_suite.id, session)
        # Convert to Read model before setting extra fields
        resp = TestSuiteReadWithChildren.model_validate(db_suite)
        resp.total_test_cases = total_cases
        resp.total_sub_modules = total_subs
        resp.effective_settings = effective_settings
        
        # Populate counts for sub-modules
        if resp.sub_modules:
            for sub in resp.sub_modules:
                sub_cases, sub_subs = await count_recursive_items(sub.id, session)
                sub.total_test_cases = sub_cases
                sub.total_sub_modules = sub_subs
                
        return resp
    return None

@router.get("/suites", response_model=List[TestSuiteReadWithChildren])
async def list_test_suites(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(
        select(TestSuite)
        .options(
            selectinload(TestSuite.test_cases),
            selectinload(TestSuite.sub_modules),
            selectinload(TestSuite.parent)
        )
    )
    suites = result.all()
    resp_suites = []
    for suite in suites:
        total_cases, total_subs = await count_recursive_items(suite.id, session)
        resp = TestSuiteReadWithChildren.model_validate(suite)
        resp.total_test_cases = total_cases
        resp.total_sub_modules = total_subs
        
        # Populate counts for sub-modules
        if resp.sub_modules:
            for sub in resp.sub_modules:
                sub_cases, sub_subs = await count_recursive_items(sub.id, session)
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
    
    total_cases, total_subs = await count_recursive_items(suite.id, session)
    effective_settings = await get_effective_settings(suite.id, session)
    resp = TestSuiteReadWithChildren.model_validate(suite)
    resp.total_test_cases = total_cases
    resp.total_sub_modules = total_subs
    resp.effective_settings = effective_settings

    # Populate counts for sub-modules
    if resp.sub_modules:
        for sub in resp.sub_modules:
            sub_cases, sub_subs = await count_recursive_items(sub.id, session)
            sub.total_test_cases = sub_cases
            sub.total_sub_modules = sub_subs

    return resp

@router.put("/suites/{suite_id}", response_model=TestSuiteReadWithChildren)
async def update_test_suite(suite_id: int, suite_update: TestSuiteUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    db_suite = await session.get(TestSuite, suite_id)
    if not db_suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Update fields if provided using exclude_unset to handle False/None correctly
    update_data = suite_update.model_dump(exclude_unset=True)
    # Track changes for audit log
    changes = {}
    for key, value in update_data.items():
        old_value = getattr(db_suite, key)
        if old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(db_suite, key, value)
    
    # Ensure JSON changes are detected
    from sqlalchemy.orm.attributes import flag_modified
    if "settings" in update_data:
        flag_modified(db_suite, "settings")

    if changes:
        db_suite.updated_by_id = current_user.id
        db_suite.updated_at = datetime.utcnow()
        session.add(db_suite)
        
        # Audit Log
        audit = AuditLog(
            entity_type="suite",
            entity_id=suite_id,
            action="update",
            user_id=current_user.id,
            changes=changes
        )
        session.add(audit)
        
        try:
            await session.commit()
        except Exception as e:
            # Log the full traceback if possible, or just return the error
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")
    
    # Fetch again with relationships to satisfy TestSuiteRead and avoid lazy loading errors
    result = await session.exec(
        select(TestSuite)
        .where(TestSuite.id == suite_id)
        .options(
            selectinload(TestSuite.test_cases),
            selectinload(TestSuite.sub_modules),
            selectinload(TestSuite.parent)
        )
    )
    db_suite = result.first()
    
    # Return with counts
    total_cases, total_subs = await count_recursive_items(db_suite.id, session)
    effective_settings = await get_effective_settings(db_suite.id, session)
    resp = TestSuiteReadWithChildren.model_validate(db_suite)
    resp.total_test_cases = total_cases
    resp.total_sub_modules = total_subs
    resp.effective_settings = effective_settings

    # Populate counts for sub-modules
    if resp.sub_modules:
        for sub in resp.sub_modules:
            sub_cases, sub_subs = await count_recursive_items(sub.id, session)
            sub.total_test_cases = sub_cases
            sub.total_sub_modules = sub_subs

    return resp

@router.delete("/suites/{suite_id}")
async def delete_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    await recursive_delete_suite(suite_id, session)
    
    # Audit Log
    audit = AuditLog(
        entity_type="suite",
        entity_id=suite_id,
        action="delete",
        user_id=current_user.id,
        changes={}
    )
    session.add(audit)
    
    await session.commit()
    return {"status": "success", "message": f"Suite {suite_id} and all its contents deleted"}

async def recursive_delete_suite(suite_id: int, session: AsyncSession):
    # Delete all TestRuns associated with this suite
    result = await session.exec(select(TestRun).where(TestRun.test_suite_id == suite_id))
    runs = result.all()
    for run in runs:
        # Delete artifacts from MinIO
        minio_client.delete_run_artifacts(run.id)
        
        # Delete associated TestCaseResults
        # We need to delete them explicitly because of the foreign key constraint
        result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
        for res in result_cases.all():
            await session.delete(res)
            
        await session.delete(run)

    # Delete all test cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    cases = result.all()
    for case in cases:
        # Delete runs associated with this case (even if they belong to another suite)
        result_runs = await session.exec(select(TestRun).where(TestRun.test_case_id == case.id))
        runs = result_runs.all()
        for run in runs:
            minio_client.delete_run_artifacts(run.id)
            # Delete associated TestCaseResults
            result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in result_cases.all():
                await session.delete(res)
            await session.delete(run)
            
        await session.delete(case)
    
    # Get sub-modules
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    sub_modules = result.all()
    for sub in sub_modules:
        await recursive_delete_suite(sub.id, session)
    
    # Delete the suite itself
    suite = await session.get(TestSuite, suite_id)
    if suite:
        await session.delete(suite)

@router.post("/suites/{suite_id}/cases", response_model=TestCaseRead)
async def create_test_case(suite_id: int, case: TestCase, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Enforce mutual exclusivity: Suite cannot have test cases if it has sub-modules
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    if result.first():
        raise HTTPException(status_code=400, detail="Cannot add test case to a suite that contains sub-modules")

    case.test_suite_id = suite_id
    case.created_by_id = current_user.id
    case.updated_by_id = current_user.id
    session.add(case)
    await session.commit()
    await session.refresh(case)
    
    # Audit Log
    audit = AuditLog(
        entity_type="case",
        entity_id=case.id,
        action="create",
        user_id=current_user.id,
        changes=case.model_dump(mode='json')
    )
    session.add(audit)
    await session.commit()
    
    return case

@router.get("/cases/{case_id}", response_model=TestCaseRead)
async def get_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    return case

@router.put("/cases/{case_id}", response_model=TestCaseRead)
async def update_test_case(case_id: int, case_update: TestCase, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    db_case = await session.get(TestCase, case_id)
    if not db_case:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    case_data = case_update.dict(exclude_unset=True)
    
    # Track changes for audit log
    changes = {}
    for key, value in case_data.items():
        if key == "id": continue # Skip ID
        old_value = getattr(db_case, key)
        # Handle list of steps comparison
        if key == "steps":
             # Simple comparison for now, could be more granular
             if old_value != value:
                 changes[key] = {"old": old_value, "new": value}
                 setattr(db_case, key, value)
        elif old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(db_case, key, value)
            
    # Ensure JSON changes are detected
    from sqlalchemy.orm.attributes import flag_modified
    if "steps" in case_data:
        flag_modified(db_case, "steps")

    if changes:
        db_case.updated_by_id = current_user.id
        db_case.updated_at = datetime.utcnow()
        session.add(db_case)
        
        # Audit Log
        audit = AuditLog(
            entity_type="case",
            entity_id=case_id,
            action="update",
            user_id=current_user.id,
            changes=changes
        )
        session.add(audit)
        
        await session.commit()
        await session.refresh(db_case)
        
    return db_case

@router.delete("/cases/{case_id}")
async def delete_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    # Delete associated TestRuns
    result_runs = await session.exec(select(TestRun).where(TestRun.test_case_id == case_id))
    runs = result_runs.all()
    for run in runs:
        minio_client.delete_run_artifacts(run.id)
        # Delete associated TestCaseResults (explicitly, though cascade should handle it now)
        result_cases = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
        for res in result_cases.all():
            await session.delete(res)
        await session.delete(run)

    await session.delete(case)
    
    # Audit Log
    audit = AuditLog(
        entity_type="case",
        entity_id=case_id,
        action="delete",
        user_id=current_user.id,
        changes={}
    )
    session.add(audit)
    
    await session.commit()
    return {"status": "success", "message": f"Test case {case_id} deleted"}

@router.get("/cases/{case_id}/export")
async def export_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    return {
        "name": case.name,
        "steps": case.steps
    }

@router.post("/suites/{suite_id}/import-case")
async def import_test_case(suite_id: int, case_data: Dict[str, Any], session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Check if suite has sub-modules
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    if result.first():
        raise HTTPException(status_code=400, detail="Cannot import test case to a suite that contains sub-modules")

    new_case = TestCase(
        name=case_data.get("name", "Imported Case"),
        steps=case_data.get("steps", []),
        test_suite_id=suite_id,
        created_by_id=current_user.id,
        updated_by_id=current_user.id
    )
    session.add(new_case)
    await session.commit()
    await session.refresh(new_case)
    
    # Audit Log
    audit = AuditLog(
        entity_type="case",
        entity_id=new_case.id,
        action="import",
        user_id=current_user.id,
        changes={"source": "import", "data": case_data}
    )
    session.add(audit)
    await session.commit()
    
    return new_case

@router.get("/suites/{suite_id}/export")
async def export_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    return await get_suite_export_data(suite_id, session)

async def get_suite_export_data(suite_id: int, session: AsyncSession):
    suite = await session.get(TestSuite, suite_id)
    
    # Get cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    cases = result.all()
    
    # Get sub-modules
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    subs = result.all()
    
    return {
        "name": suite.name,
        "description": suite.description,
        "execution_mode": suite.execution_mode,
        "settings": suite.settings,
        "inherit_settings": suite.inherit_settings,
        "test_cases": [{"name": c.name, "steps": c.steps} for c in cases],
        "sub_modules": [await get_suite_export_data(sub.id, session) for sub in subs],
        "created_by_name": suite.created_by.full_name if suite.created_by else None,
        "updated_by_name": suite.updated_by.full_name if suite.updated_by else None
    }

@router.post("/suites/import-suite")
async def import_top_level_suite(suite_data: Dict[str, Any], session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    new_suite = await create_suite_from_data(suite_data, None, session, current_user.id)
    await session.commit()
    
    # Audit Log
    audit = AuditLog(
        entity_type="suite",
        entity_id=new_suite.id,
        action="import",
        user_id=current_user.id,
        changes={"source": "import", "data": suite_data}
    )
    session.add(audit)
    await session.commit()
    
    return {"status": "success", "id": new_suite.id}

@router.post("/suites/{suite_id}/import-suite")
async def import_test_suite(suite_id: int, suite_data: Dict[str, Any], session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # If suite_id is 0, we can't import under it (unless we want top-level)
    # But for now, let's assume we import into an existing suite
    parent = await session.get(TestSuite, suite_id)
    if not parent:
         raise HTTPException(status_code=404, detail="Parent suite not found")

    # Check if parent has test cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    if result.first():
        raise HTTPException(status_code=400, detail="Cannot import sub-module to a suite that contains test cases")

    new_suite = await create_suite_from_data(suite_data, suite_id, session, current_user.id)
    await session.commit()
    
    # Audit Log
    audit = AuditLog(
        entity_type="suite",
        entity_id=new_suite.id,
        action="import",
        user_id=current_user.id,
        changes={"source": "import", "data": suite_data}
    )
    session.add(audit)
    await session.commit()
    
    return {"status": "success", "id": new_suite.id}

async def create_suite_from_data(data: Dict[str, Any], parent_id: Optional[int], session: AsyncSession, user_id: int):
    new_suite = TestSuite(
        name=data.get("name", "Imported Suite"),
        description=data.get("description"),
        execution_mode=data.get("execution_mode", ExecutionMode.CONTINUOUS),
        settings=data.get("settings", {"headers": {}, "params": {}}),
        inherit_settings=data.get("inherit_settings", True),
        parent_id=parent_id,
        created_by_id=user_id,
        updated_by_id=user_id
    )
    session.add(new_suite)
    await session.flush() # Get ID
    
    # Import cases
    for case_data in data.get("test_cases", []):
        new_case = TestCase(
            name=case_data.get("name"),
            steps=case_data.get("steps", []),
            test_suite_id=new_suite.id,
            created_by_id=user_id,
            updated_by_id=user_id
        )
        session.add(new_case)
        
    # Import sub-modules
    for sub_data in data.get("sub_modules", []):
        await create_suite_from_data(sub_data, new_suite.id, session, user_id)
        
    return new_suite

@router.get("/audit/{entity_type}/{entity_id}", response_model=List[AuditLogRead])
async def get_audit_log(entity_type: str, entity_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    query = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.timestamp.desc())
    
    if entity_type == 'suite':
        # Fetch logs for the suite AND its direct test cases
        # First, get all test case IDs for this suite
        case_ids_result = await session.exec(select(TestCase.id).where(TestCase.test_suite_id == entity_id))
        case_ids = case_ids_result.all()
        
        query = query.where(
            or_(
                and_(AuditLog.entity_type == 'suite', AuditLog.entity_id == entity_id),
                and_(AuditLog.entity_type == 'case', AuditLog.entity_id.in_(case_ids))
            )
        )
    else:
        query = query.where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)

    result = await session.exec(query)
    logs = result.all()
    return logs

async def get_suite_path(suite_id: int, session: AsyncSession) -> str:
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        return ""
    
    if suite.parent_id:
        parent_path = await get_suite_path(suite.parent_id, session)
        return f"{parent_path} > {suite.name}"
    return suite.name

async def collect_test_cases(suite_id: int, session: AsyncSession) -> List[TestCase]:
    # Get direct cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    cases = list(result.all())
    
    # Get sub-modules and recurse
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    sub_modules = result.all()
    for sub in sub_modules:
        cases.extend(await collect_test_cases(sub.id, session))
    
    return cases

async def count_recursive_items(suite_id: int, session: AsyncSession):
    # Count direct cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    direct_cases = len(result.all())
    
    # Count direct sub-modules
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    direct_subs = result.all()
    
    total_cases = direct_cases
    total_subs = len(direct_subs)
    
    for sub in direct_subs:
        sub_cases, sub_subs = await count_recursive_items(sub.id, session)
        total_cases += sub_cases
        total_subs += sub_subs
        
    return total_cases, total_subs

async def get_effective_settings(suite_id: int, session: AsyncSession) -> Dict[str, Any]:
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        return {"headers": {}, "params": {}}
    
    current_settings = suite.settings or {"headers": {}, "params": {}}
    
    if suite.inherit_settings and suite.parent_id:
        parent_settings = await get_effective_settings(suite.parent_id, session)
        
        # Merge Headers & Params: Child overrides parent
        merged_headers = {**parent_settings.get("headers", {}), **current_settings.get("headers", {})}
        merged_params = {**parent_settings.get("params", {}), **current_settings.get("params", {})}
        
        # Merge Allowed Domains: Handle both strings and dicts
        parent_domains_raw = parent_settings.get("allowed_domains", [])
        current_domains_raw = current_settings.get("allowed_domains", [])
        
        # Helper to normalize to dict
        def normalize_domain(d):
            if not d:
                return None
            if isinstance(d, str):
                return {"domain": d, "headers": True, "params": False}
            if isinstance(d, dict) and "domain" not in d:
                return None # Skip invalid dicts
            return d

        # Use a dict keyed by domain name to merge, favoring child (current) settings
        merged_domains_map = {}
        
        for d in parent_domains_raw:
            norm = normalize_domain(d)
            if norm:
                merged_domains_map[norm["domain"]] = norm
            
        for d in current_domains_raw:
            norm = normalize_domain(d)
            if norm:
                merged_domains_map[norm["domain"]] = norm # Overwrite parent
            
        merged_domains = list(merged_domains_map.values())
        
        # Merge Domain Settings: Deep merge
        parent_domain_settings = parent_settings.get("domain_settings", {})
        current_domain_settings = current_settings.get("domain_settings", {})
        merged_domain_settings = {**parent_domain_settings}
        
        for domain, settings in current_domain_settings.items():
            if domain in merged_domain_settings:
                # Merge headers/params for this domain
                merged_domain_settings[domain] = {
                    "headers": {**merged_domain_settings[domain].get("headers", {}), **settings.get("headers", {})},
                    "params": {**merged_domain_settings[domain].get("params", {}), **settings.get("params", {})}
                }
            else:
                merged_domain_settings[domain] = settings
                
        return {
            "headers": merged_headers, 
            "params": merged_params,
            "allowed_domains": merged_domains,
            "domain_settings": merged_domain_settings
        }
    
    return current_settings

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

    # Get effective settings for this suite
    effective_settings = await get_effective_settings(suite_id, session)

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
            # We can optimize by merging with parent_settings, but for now let's use the helper
            # to ensure correctness as per existing logic.
            current_effective_settings = await get_effective_settings(s_id, session)
            
            suite_path = await get_suite_path(s_id, session)

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
                            await session.commit()
                            await session.refresh(run)
                            created_runs.append(run)
                            try:
                                run_test_suite.delay(run.id)
                            except Exception as e:
                                print(f"Failed to queue run {run.id}: {e}")

                # 2. Recurse for sub-modules
                result = await session.exec(select(TestSuite).where(TestSuite.parent_id == s_id))
                sub_modules = result.all()
                for sub in sub_modules:
                    await process_suite(sub.id, current_effective_settings)

            else: # CONTINUOUS
                # 1. Create ONE run for this suite (covering direct cases and continuous descendants)
                # We only create a run if there are cases to run in this continuous block
                # But the worker will handle finding cases. We just need to define the entry point.
                
                # However, we must ensure that the worker STOPS at Separate boundaries.
                # So we create a run for this suite.
                
                for target_browser in browser:
                    for target_device in target_devices:
                        run = TestRun(
                            status=TestStatus.PENDING, 
                            test_suite_id=s_id, 
                            test_case_id=None, # Indicates run all applicable cases in this suite
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
                        await session.commit()
                        await session.refresh(run)
                        created_runs.append(run)
                        try:
                            run_test_suite.delay(run.id)
                        except Exception as e:
                            print(f"Failed to queue run {run.id}: {e}")

                # 2. Recurse for sub-modules to find SEPARATE modules
                # We need to traverse down. If a sub-module is CONTINUOUS, it's covered by the run we just created (assuming worker logic).
                # IF a sub-module is SEPARATE, we need to process it explicitly.
                
                # Wait, if we rely on the worker to pick up "Continuous descendants", then we shouldn't recurse into Continuous sub-modules here?
                # YES, correct. The worker will pick them up.
                # BUT, we DO need to find SEPARATE sub-modules that are children of this Continuous suite (or children of children).
                
                # So we need a helper to find "Boundary" Separate suites.
                
                async def find_and_process_separate_descendants(p_id):
                    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == p_id))
                    subs = result.all()
                    for sub in subs:
                        if sub.execution_mode == ExecutionMode.SEPARATE:
                            # Found a boundary! Process it as a separate suite.
                            await process_suite(sub.id, current_effective_settings)
                        else:
                            # Still Continuous, keep digging
                            await find_and_process_separate_descendants(sub.id)

                await find_and_process_separate_descendants(s_id)

        # If a specific case is requested, just run that case
        if case_id:
             for target_browser in browser:
                for target_device in target_devices:
                    suite_path = await get_suite_path(suite_id, session)
                    case = await session.get(TestCase, case_id)
                    test_case_name = case.name if case else None
                    
                    run = TestRun(
                        status=TestStatus.PENDING, 
                        test_suite_id=suite_id, 
                        test_case_id=case_id,
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
                    await session.commit()
                    await session.refresh(run)
                    created_runs.append(run)
                    try:
                        run_test_suite.delay(run.id)
                    except Exception as e:
                        print(f"Failed to queue run {run.id}: {e}")
        else:
            # Run the suite recursively
            await process_suite(suite_id, effective_settings)

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
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    status: Optional[str] = None,
    browser: Optional[str] = None,
    device: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Build query with filters
    query = select(TestRun)
    
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
