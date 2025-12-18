from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.models import TestRun, TestStatus, TestSuite, TestCase, TestSuiteRead

from app.worker import run_test_suite
from app.core.storage import minio_client
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.models import User

router = APIRouter()

@router.post("/suites", response_model=TestSuite)
async def create_test_suite(suite: TestSuite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    session.add(suite)
    await session.commit()
    await session.refresh(suite)
    return suite

@router.get("/suites", response_model=List[TestSuiteRead])
async def list_test_suites(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(select(TestSuite).options(selectinload(TestSuite.test_cases)))
    return result.all()

@router.get("/suites/{suite_id}", response_model=TestSuiteRead)
async def get_test_suite(suite_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(
        select(TestSuite)
        .where(TestSuite.id == suite_id)
        .options(selectinload(TestSuite.test_cases))
    )
    suite = result.first()
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    return suite

@router.post("/suites/{suite_id}/cases", response_model=TestCase)
async def create_test_case(suite_id: int, case: TestCase, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case.test_suite_id = suite_id
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case

@router.get("/cases/{case_id}", response_model=TestCase)
async def get_test_case(case_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    return case

@router.put("/cases/{case_id}", response_model=TestCase)
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

@router.post("/runs", response_model=TestRun)
async def create_run(suite_id: int, case_id: Optional[int] = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    run = TestRun(status=TestStatus.PENDING, test_suite_id=suite_id, test_case_id=case_id)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    
    # Trigger Celery task
    run_test_suite.delay(run.id)
    
    return run

@router.get("/runs", response_model=list[TestRun])
async def list_runs(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    result = await session.exec(select(TestRun).order_by(TestRun.created_at.desc()))
    return result.all()

@router.get("/runs/{run_id}", response_model=TestRun)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run

@router.get("/artifacts/{object_name:path}")
async def get_artifact_url(object_name: str, current_user: User = Depends(get_current_user)):
    url = minio_client.get_presigned_url(object_name)
    return {"url": url}
