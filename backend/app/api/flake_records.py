"""FlakeRecord management — list, quarantine/un-quarantine flaky tests.

Quarantined records are skipped at dispatch time (see worker.py). The flake
score is maintained by the result-aggregator pipeline (Phase B precursor:
today the score is updated by hand or by future heuristics; the column
exists so it can be wired without a migration).

Surface:
    GET   /api/flakes?test_case_id=&quarantined_only=
    POST  /api/flakes/{id}/quarantine
    POST  /api/flakes/{id}/release
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import FlakeRecord, TestCase
from app.services.access_service import access_service

router = APIRouter()


@router.get("/flakes")
async def list_flakes(
    test_case_id: Optional[int] = None,
    quarantined_only: bool = False,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    query = select(FlakeRecord)
    if test_case_id is not None:
        query = query.where(FlakeRecord.test_case_id == test_case_id)
    if quarantined_only:
        query = query.where(FlakeRecord.is_quarantined == True)  # noqa: E712
    res = await session.exec(query.order_by(FlakeRecord.flake_score.desc()).limit(200))
    return res.all()


async def _check_editor_for_case(case_id: int, user_id: int, session: AsyncSession):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(user_id, case.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")


@router.post("/flakes/{flake_id}/quarantine")
async def quarantine(
    flake_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await session.get(FlakeRecord, flake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Flake record not found")
    await _check_editor_for_case(record.test_case_id, principal.user.id, session)
    record.is_quarantined = True
    record.last_observed_at = datetime.utcnow()
    session.add(record)
    await session.commit()
    return {"status": "quarantined", "id": flake_id}


@router.post("/flakes/{flake_id}/release")
async def release(
    flake_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await session.get(FlakeRecord, flake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Flake record not found")
    await _check_editor_for_case(record.test_case_id, principal.user.id, session)
    record.is_quarantined = False
    session.add(record)
    await session.commit()
    return {"status": "released", "id": flake_id}
