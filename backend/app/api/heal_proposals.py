"""Selector-heal proposals — list / recheck / accept / reject.

Proposals come from the worker's runtime heal (persisted by the result
aggregator) and the proactive heal beat task. Since W5 each carries a ranked
list of candidates with evidence and a reference to the redacted DOM snapshot
they were scored against; accepting re-evaluates the chosen (or reviewer-
picked) candidate against that snapshot and refuses unless it matches exactly
one element.

Surface:
    GET    /api/heal-proposals?status=pending&test_case_id=X&project_id=Y
    POST   /api/heal-proposals/{id}/recheck   {"selector": "..."}  (dry run)
    POST   /api/heal-proposals/{id}/accept    {"selector": "..."}  (optional)
    POST   /api/heal-proposals/{id}/reject
    POST   /api/heal-proposals/{id}/validate  {"selector": "..."}  (WP16c: a
           validation run of the case with this selector, that run only)
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    SelectorHealProposal,
    SelectorHealProposalRead,
    TestCase,
)
from app.services import heal_candidates
from app.services.access_service import access_service

router = APIRouter()


class HealSelectorChoice(BaseModel):
    """Which candidate to recheck/apply; omitted = the proposal's own pick."""
    selector: Optional[str] = None


def _to_read(p: SelectorHealProposal) -> SelectorHealProposalRead:
    return SelectorHealProposalRead(
        id=p.id, test_case_id=p.test_case_id, step_id=p.step_id,
        old_selector=p.old_selector, new_selector=p.new_selector,
        intent=p.intent, confidence=p.confidence, rationale=p.rationale,
        source_run_id=p.source_run_id, status=p.status,
        created_at=p.created_at,
        candidates=list(getattr(p, "candidates", None) or []),
        chosen_selector=getattr(p, "chosen_selector", None) or p.new_selector,
        has_dom_snapshot=bool(getattr(p, "dom_snapshot_key", None)),
        recheck=getattr(p, "recheck", None),
        validation_status=getattr(p, "validation_status", None),
        validation_run_id=getattr(p, "validation_run_id", None),
        validation_conditions=getattr(p, "validation_conditions", None),
        validation_detail=getattr(p, "validation_detail", None),
        validated_at=getattr(p, "validated_at", None),
    )


async def _may_see_project(principal: AuthPrincipal, project_id: Optional[int],
                           session: AsyncSession, min_role: str = "viewer") -> bool:
    if project_id is None:
        return False
    if principal.api_key is not None:
        from app.api.jobs import api_key_allows_project
        if not api_key_allows_project(principal.api_key, project_id):
            return False
    return await access_service.has_project_access(principal.user.id, project_id, session, min_role=min_role)


@router.get("/heal-proposals", response_model=List[SelectorHealProposalRead])
async def list_proposals(
    status: Optional[str] = "pending",
    test_case_id: Optional[int] = None,
    project_id: Optional[int] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[SelectorHealProposalRead]:
    query = select(SelectorHealProposal, TestCase.project_id).join(
        TestCase, TestCase.id == SelectorHealProposal.test_case_id)
    if status:
        query = query.where(SelectorHealProposal.status == status)
    if test_case_id is not None:
        query = query.where(SelectorHealProposal.test_case_id == test_case_id)
    if project_id is not None:
        query = query.where(TestCase.project_id == project_id)
    res = await session.exec(query.order_by(SelectorHealProposal.created_at.desc()).limit(200))
    # Proposals carry selectors and DOM-derived text from the case's pages:
    # only show those on projects the caller can read (checked per project,
    # the same rule that gates the case itself).
    allowed: Dict[int, bool] = {}
    out = []
    from app.services import repair_validation
    for p, pid in res.all():
        if pid not in allowed:
            allowed[pid] = await _may_see_project(principal, pid, session)
        if allowed[pid]:
            out.append(await repair_validation.annotate(session, repair_validation.HEAL, p, _to_read(p)))
    return out


async def _check_editor_on_case(case_id: int, user_id: int, session: AsyncSession,
                                principal: Optional[AuthPrincipal] = None,
                                min_role: str = "editor") -> TestCase:
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if principal is not None and principal.api_key is not None:
        from app.api.jobs import api_key_allows_project
        if not api_key_allows_project(principal.api_key, case.project_id):
            raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(user_id, case.project_id, session, min_role=min_role):
        raise HTTPException(status_code=403, detail="Editor access required" if min_role == "editor"
                            else "Access denied")
    return case


def _pick_selector(p: SelectorHealProposal, choice: Optional[HealSelectorChoice]) -> str:
    """The selector to recheck/apply. A reviewer may only pick one of the
    proposal's own candidates — this is not a free-form selector editor."""
    default = getattr(p, "chosen_selector", None) or p.new_selector
    # isinstance, not truthiness: called directly (tests, other modules) the
    # parameter holds FastAPI's Body() marker rather than None.
    wanted = (choice.selector if isinstance(choice, HealSelectorChoice) else None) or default
    allowed = {p.new_selector, default} | {
        c.get("selector") for c in (getattr(p, "candidates", None) or []) if isinstance(c, dict)}
    if wanted not in allowed:
        raise HTTPException(status_code=400, detail="Pick one of this proposal's candidates.")
    return wanted


async def _recheck(p: SelectorHealProposal, selector: str) -> Dict[str, Any]:
    key = getattr(p, "dom_snapshot_key", None)
    if not key:
        return heal_candidates.recheck(None, selector)
    html = await asyncio.to_thread(heal_candidates.load_snapshot, key)
    if html is None:
        out = heal_candidates.recheck(None, selector)
        out["detail"] = "The stored DOM snapshot is no longer available; uniqueness was not rechecked."
        return out
    return await asyncio.to_thread(heal_candidates.recheck, html, selector)


#: Recheck outcomes that block an accept. `unavailable` (legacy proposals and
#: snapshots that were purged with their run) does not: there is nothing to
#: check against, and the run-time count was already recorded.
_BLOCKING_RECHECK = {"not_unique", "no_match", "unsupported"}


@router.post("/heal-proposals/{proposal_id}/recheck")
async def recheck_proposal(
    proposal_id: int,
    choice: Optional[HealSelectorChoice] = Body(default=None),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Dry run of the accept-time uniqueness check for one candidate."""
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    await _check_editor_on_case(p.test_case_id, principal.user.id, session, principal, min_role="viewer")
    return await _recheck(p, _pick_selector(p, choice))


@router.post("/heal-proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: int,
    choice: Optional[HealSelectorChoice] = Body(default=None),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    case = await _check_editor_on_case(p.test_case_id, principal.user.id, session, principal)
    if p.status != "pending":
        raise HTTPException(status_code=409, detail=f"Proposal is already {p.status}")
    selector = _pick_selector(p, choice)

    # Apply the heal: rewrite the matching step's selector inside testcase.steps.
    steps = [s if isinstance(s, dict) else s.dict() for s in (case.steps or [])]
    idx = next((i for i, s in enumerate(steps) if s.get("id") == p.step_id), None)
    refusal = heal_refusal(steps[idx] if idx is not None else None, p.old_selector)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    # W5: the candidate must still match exactly one element of the DOM it
    # was scored against — a heal that matches two elements clicks whichever
    # Playwright finds first, which is how a heal silently tests the wrong thing.
    check = await _recheck(p, selector)
    if check["status"] in _BLOCKING_RECHECK:
        raise HTTPException(status_code=409, detail=f"Recheck failed for {selector}: {check['detail']}")

    # WP16c: when the workspace / instance / a protected suite requires it, a
    # passing validation run of THIS selector (policy engine,
    # proposal.validation_required).
    from app.services import repair_validation
    await repair_validation.enforce_accept(session, repair_validation.HEAL, p, principal.user.id,
                                           selector)

    steps[idx] = {**steps[idx], "selector": selector}
    updated = True
    # A new list object: mutating the stored JSON in place is not reliably
    # detected as a change, so the heal could silently never persist.
    case.steps = steps
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(case, "steps")
    p.status = "accepted"
    p.decided_at = datetime.utcnow()
    p.decided_by_id = principal.user.id
    p.chosen_selector = selector
    p.recheck = check
    session.add(case)
    session.add(p)
    if updated:
        from app.services.case_revisions import record_revision
        await record_revision(session, case, "heal", user_id=principal.user.id,
                              agent_id=principal.agent_id)
    await repair_validation.record_decision(session, repair_validation.HEAL, p, "accepted",
                                            case.project_id, principal.user.id)
    await session.commit()
    return {"status": "accepted", "applied": updated, "selector": selector, "recheck": check}


@router.post("/heal-proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    case = await _check_editor_on_case(p.test_case_id, principal.user.id, session, principal)
    p.status = "rejected"
    p.decided_at = datetime.utcnow()
    p.decided_by_id = principal.user.id
    session.add(p)
    from app.services import repair_validation
    await repair_validation.record_decision(session, repair_validation.HEAL, p, "rejected",
                                            case.project_id, principal.user.id)
    await session.commit()
    return {"status": "rejected"}


@router.post("/heal-proposals/{proposal_id}/validate")
async def validate_proposal(
    proposal_id: int,
    choice: Optional[HealSelectorChoice] = Body(default=None),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Run the case once with this heal applied to that run only (WP16c).
    Same conditions as the failing run; the stored case is untouched."""
    p = await session.get(SelectorHealProposal, proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    case = await _check_editor_on_case(p.test_case_id, principal.user.id, session, principal)
    if p.status != "pending":
        raise HTTPException(status_code=409, detail=f"Proposal is already {p.status}")
    selector = _pick_selector(p, choice)
    steps = [s if isinstance(s, dict) else s.dict() for s in (case.steps or [])]
    step = next((s for s in steps if s.get("id") == p.step_id), None)
    refusal = heal_refusal(step, p.old_selector)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    from app.services import repair_validation
    return await repair_validation.launch(session, principal, repair_validation.HEAL, p, selector)


# Steps whose selector IS the assertion. Re-pointing one at "whatever matches
# now" can turn a check that should fail into one that passes, which is the
# one thing a heal must never do. These get fixed by a human in the builder.
_ASSERTION_PREFIXES = ("expect-", "assert", "mobile-expect-")
_ASSERTION_TYPES = {"count-children", "verify-nth-child", "check-accessibility"}


def heal_refusal(step, old_selector):
    """Why this heal must not be applied, or None if it is safe to apply."""
    if step is None:
        return "The step this proposal targets no longer exists in the case."
    step_type = str(step.get("type") or "")
    if step_type.startswith(_ASSERTION_PREFIXES) or step_type in _ASSERTION_TYPES:
        return (f"'{step_type}' is an assertion step; changing its selector could "
                "make a failing check pass. Edit it in the test builder instead.")
    if old_selector and step.get("selector") != old_selector:
        return ("The step's selector has changed since this proposal was made; "
                "it is stale. Reject it and let the next run re-propose.")
    return None
