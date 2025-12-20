from typing import List, Optional, Union, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.models import TestRun, TestStatus, TestSuite, TestCase, TestSuiteRead, ExecutionMode, TestCaseRead, TestRunRead, TestSuiteReadWithChildren, TestSuiteUpdate

from app.worker import run_test_suite
from app.core.storage import minio_client
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.models import User

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

    session.add(suite)
    await session.commit()
    await session.refresh(suite)
    
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
    return resp

@router.put("/suites/{suite_id}", response_model=TestSuiteReadWithChildren)
async def update_test_suite(suite_id: int, suite_update: TestSuiteUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    db_suite = await session.get(TestSuite, suite_id)
    if not db_suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    # Update fields if provided using exclude_unset to handle False/None correctly
    update_data = suite_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_suite, key, value)
    
    # Ensure JSON changes are detected
    from sqlalchemy.orm.attributes import flag_modified
    if "settings" in update_data:
        flag_modified(db_suite, "settings")
    
    session.add(db_suite)
    await session.commit()
    
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
    return resp

@router.delete("/suites/{suite_id}")
async def delete_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    
    await recursive_delete_suite(suite_id, session)
    await session.commit()
    return {"status": "success", "message": f"Suite {suite_id} and all its contents deleted"}

async def recursive_delete_suite(suite_id: int, session: AsyncSession):
    # Delete all test cases
    result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
    cases = result.all()
    for case in cases:
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
    session.add(case)
    await session.commit()
    await session.refresh(case)
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
    for key, value in case_data.items():
        setattr(db_case, key, value)
        
    session.add(db_case)
    await session.commit()
    await session.refresh(db_case)
    return db_case

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
async def create_run(suite_id: int, case_id: Optional[int] = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")

    # Get effective settings for this suite
    effective_settings = await get_effective_settings(suite_id, session)

    # Check if we need to run multiple cases (either SEPARATE mode or has sub-modules)
    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
    has_sub_modules = result.first() is not None

    if suite.execution_mode == ExecutionMode.SEPARATE and case_id is None:
        # Collect all test cases recursively
        cases = await collect_test_cases(suite_id, session)
        
        if not cases:
            raise HTTPException(status_code=400, detail="No test cases found in suite or its sub-modules")
            
        runs = []
        for case in cases:
            # Get effective settings for the specific suite this case belongs to
            case_settings = await get_effective_settings(case.test_suite_id, session)
            # Get the path for this specific case's suite
            case_suite_path = await get_suite_path(case.test_suite_id, session)
            run = TestRun(
                status=TestStatus.PENDING, 
                test_suite_id=suite_id, 
                test_case_id=case.id,
                suite_name=case_suite_path,
                test_case_name=case.name,
                request_headers=case_settings.get("headers", {}),
                request_params=case_settings.get("params", {}),
                allowed_domains=case_settings.get("allowed_domains", []),
                domain_settings=case_settings.get("domain_settings", {})
                # We can store params in a new field or handle them in the worker
            )
            session.add(run)
            runs.append(run)
        
        await session.commit()
        for run in runs:
            await session.refresh(run)
            try:
                run_test_suite.delay(run.id)
            except Exception as e:
                print(f"Failed to queue run {run.id}: {e}")
                # Continue queuing others? Or fail all?
                # For now, just log. The user will see them as PENDING forever if not picked up.
                pass
        return runs
    else:
        suite_path = await get_suite_path(suite_id, session)
        test_case_name = None
        if case_id:
            case = await session.get(TestCase, case_id)
            if case:
                test_case_name = case.name

        run = TestRun(
            status=TestStatus.PENDING, 
            test_suite_id=suite_id, 
            test_case_id=case_id,
            suite_name=suite_path,
            test_case_name=test_case_name,
            request_headers=effective_settings.get("headers", {}),
            request_params=effective_settings.get("params", {}),
            allowed_domains=effective_settings.get("allowed_domains", []),
            domain_settings=effective_settings.get("domain_settings", {})
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        
        # Trigger Celery task
        try:
            run_test_suite.delay(run.id)
        except Exception as e:
            # If we can't queue the task, we should probably let the user know
            # but since we already committed the run, we might want to update its status to ERROR
            # However, for the API response, a 500 is appropriate if the infrastructure is down.
            # Let's log it and re-raise or handle gracefully.
            # For now, let's raise a 500 with a clear message.
            raise HTTPException(status_code=500, detail=f"Failed to queue test execution: {str(e)}")
        
        return run

@router.get("/runs", response_model=List[TestRunRead])
async def list_runs(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(select(TestRun).order_by(TestRun.created_at.desc()))
    return result.all()

@router.get("/runs/{run_id}", response_model=TestRunRead)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run

@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    
    # Delete artifacts from MinIO
    minio_client.delete_run_artifacts(run_id)
    
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
            await session.delete(run)
        await session.commit()
        return {"status": "success", "message": f"All {len(runs)} runs deleted"}
    
    if run_ids:
        # Delete specific runs
        result = await session.exec(select(TestRun).where(TestRun.id.in_(run_ids)))
        runs = result.all()
        for run in runs:
            minio_client.delete_run_artifacts(run.id)
            await session.delete(run)
        await session.commit()
        return {"status": "success", "message": f"{len(runs)} runs deleted"}
        
    raise HTTPException(status_code=400, detail="Must specify run_ids or all=true")

@router.get("/artifacts/{object_name:path}")
async def get_artifact_url(object_name: str, current_user: User = Depends(get_current_user)):
    url = minio_client.get_presigned_url(object_name)
    return {"url": url}
