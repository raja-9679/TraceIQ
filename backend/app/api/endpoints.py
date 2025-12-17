from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.models import TestRun, TestStatus, TestSuite, TestCase
from app.worker import run_test_suite
from app.core.storage import minio_client
from sqlalchemy.orm import selectinload

router = APIRouter()

@router.post("/suites", response_model=TestSuite)
async def create_test_suite(suite: TestSuite, session: AsyncSession = Depends(get_session)):
    session.add(suite)
    await session.commit()
    await session.refresh(suite)
    return suite

@router.get("/suites", response_model=List[TestSuite])
async def list_test_suites(session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(TestSuite).options(selectinload(TestSuite.test_cases)))
    return result.all()

@router.get("/suites/{suite_id}", response_model=TestSuite)
async def get_test_suite(suite_id: int, session: AsyncSession = Depends(get_session)):
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
async def create_test_case(suite_id: int, case: TestCase, session: AsyncSession = Depends(get_session)):
    case.test_suite_id = suite_id
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case

@router.post("/runs", response_model=TestRun)
async def create_run(session: AsyncSession = Depends(get_session)):
    run = TestRun(status=TestStatus.PENDING)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    
    # Trigger Celery task
    run_test_suite.delay(run.id)
    
    return run

@router.get("/runs", response_model=list[TestRun])
async def list_runs(session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(TestRun).order_by(TestRun.created_at.desc()))
    return result.all()

@router.get("/runs/{run_id}", response_model=TestRun)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run

@router.get("/artifacts/{object_name:path}")
async def get_artifact_url(object_name: str):
    url = minio_client.get_presigned_url(object_name)
    return {"url": url}
