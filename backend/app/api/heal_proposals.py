"""Selector-heal proposals — accept / reject / list.

The beat task `propose_selector_heals_for_run` creates rows after every
successful run by diffing stored selectors against the run's captured DOM.
Reviewers (or an auto-apply policy) decide what to do.

Surface:
    GET    /api/heal-proposals?status=pending&test_case_id=X
    POST   /api/heal-proposals/{id}/accept
    POST   /api/heal-proposals/{id}/reject
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    SelectorHealProposal,
    SelectorHealProposalRead,
    TestCase,
)
from app.services.access_service import access_service

router = APIRouter()


def _to_read(p: SelectorHealProposal) -> SelectorHealProposalRead:
    return SelectorHealProposalRead(
        id=p.id, test_case_id=p.test_case_id, step_id=p.step_id,
        old_selector=p.old_selector, new_selector=p.new_selector,
        intent=p.intent, confidence=p.confidence, rationale=p.rationale,
        source_run_id=p.source_run_id, status=p.status,
        created_at=p.created_at,
    )


@router.get("/heal-proposals", response_model=List[SelectorHealProposalRead])
async def list_proposals(
    status: Optional[str] = "pending",
    test_case_id: Optional[int] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[SelectorHealProposalRead]:
    query = select(SelectorHealProposal)
    if status:
        query = query.where(SelectorHealProposal.status == status)
    if test_case_id is not None:
        query = query.where(SelectorHealProposal.test_case_id == test_case_id)
    res = await session.exec(query.order_by(SelectorHealProposal.created_at.desc()).limit(200))
    return [_to_read(p) for p in res.all()]


async def _check_editor_on_case(case_id: int, user_id: int, session: AsyncSession) -> TestCase:
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(user_id, case.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    return case


@router.post("/heal-proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    case = await _check_editor_on_case(p.test_case_id, principal.user.id, session)

    # Apply the heal: rewrite the matching step's selector inside testcase.steps.
    steps = case.steps or []
    updated = False
    for idx, step in enumerate(steps):
        sid = step.get("id") if isinstance(step, dict) else getattr(step, "id", None)
        if sid == p.step_id:
            if isinstance(step, dict):
                step["selector"] = p.new_selector
            else:
                step.selector = p.new_selector
            steps[idx] = step
            updated = True
            break
    case.steps = steps
    p.status = "accepted"
    p.decided_at = datetime.utcnow()
    p.decided_by_id = principal.user.id
    session.add(case)
    session.add(p)
    await session.commit()
    return {"status": "accepted", "applied": updated}


@router.post("/heal-proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    p.status = "rejected"
    p.decided_at = datetime.utcnow()
    p.decided_by_id = principal.user.id
    session.add(p)
    await session.commit()
    return {"status": "rejected"}
