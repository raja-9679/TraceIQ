"""Turn a requirement criterion or an incident into a CaseProposal.

Always PROPOSE, never create: these proposals come from text a third party
wrote (a ticket, a pager alert), so a human reviews them in the existing queue.
The workspace auto-apply policy is honoured exactly as for /cases/generate.

The LLM is optional. With the Null provider, a workspace over its monthly
token cap (the provider returns "") or over its daily generation cap, the
proposal is a *skeleton*: one step per Given/When/Then clause with an empty
selector and the clause as its intent, flagged `needs_authoring` and given
confidence 0 so no auto-apply threshold can ever merge it. Empty selectors make
the case fail when run, which is the point — an unauthored skeleton must show
red, not pass vacuously.

The proposal's payload carries its origin (`requirement_id` +
`criterion_index`, or `incident_id`). When it is accepted,
`link_accepted_case` turns that into a RequirementLink / the incident's
`regression_case_id` — re-checking that the origin is in the proposal's own
project, because the generic `POST /case-proposals` lets a caller write any
payload it likes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CaseProposal, CaseProposalAction, Incident, Project, Requirement, RequirementLink

GENERATED_CONFIDENCE = 0.7
_CLAUSE = re.compile(r"^(given|when|then|and|but)\b\s*(.*)$", re.I)


def skeleton_steps(text: str, target_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Placeholder steps for a human to finish. Clause → step type is a
    starting point only: Given = precondition check, When = action,
    Then/And-after-Then = assertion."""
    steps: List[Dict[str, Any]] = []
    if target_url:
        steps.append({"type": "goto", "value": target_url, "intent": "Open the page under test"})
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    phase = None
    any_clause = False
    for ln in lines:
        m = _CLAUSE.match(ln)
        if not m:
            continue
        any_clause = True
        kw = m.group(1).lower()
        if kw in ("given", "when", "then"):
            phase = kw
        kind = {"given": "expect-visible", "when": "click", "then": "expect-text"}.get(phase or "then")
        steps.append({"type": kind, "selector": "", "value": "",
                      "intent": f"TODO: {ln}"})
    if not any_clause:
        steps.append({"type": "expect-text", "selector": "", "value": "",
                      "intent": f"TODO: verify — {' '.join(lines)[:400]}"})
    return steps


def case_name(prefix: str, text: str) -> str:
    first = next((ln.strip() for ln in (text or "").split("\n") if ln.strip()), "")
    first = re.sub(r"^(scenario(?: outline)?|example)\s*:\s*", "", first, flags=re.I)
    name = f"{prefix} {first}".strip()
    return name[:120]


async def _llm_steps(session: AsyncSession, project: Project, description: str,
                     target_url: Optional[str], feature: str) -> Tuple[List[dict], Optional[str]]:
    """(steps, why-not). Empty steps + a reason when the LLM path is unavailable."""
    from app.ai.providers import provider as llm_provider
    from app.api import case_generation

    if llm_provider.name == "null":
        return [], "no LLM provider configured"
    try:
        await case_generation._check_and_consume_ai_budget(project.workspace_id, session)
    except HTTPException as exc:
        if exc.status_code == 429:
            return [], "daily AI generation cap reached"
        raise
    try:
        steps = await case_generation.generate_steps(
            description, target_url, workspace_id=project.workspace_id,
            project_id=project.id, feature=feature)
    except Exception as exc:  # noqa: BLE001 — generation is best-effort; fall back
        print(f"[generation-proposals] LLM generation failed: {exc}")
        return [], "LLM call failed"
    if not steps:
        return [], "LLM returned no usable steps (provider unavailable or over quota)"
    return steps, None


async def propose(session: AsyncSession, *, project: Project, suite_id: int,
                  name: str, description: str, skeleton_text: str,
                  target_url: Optional[str], origin: Dict[str, Any], tags: List[str],
                  rationale: str, user_id: Optional[int], feature: str,
                  agent_id: Optional[str] = None,
                  agent_session_id: Optional[str] = None) -> Tuple[CaseProposal, Optional[str]]:
    """Create (and flush) one CREATE proposal. Returns (proposal, needs_authoring_reason).
    Caller commits."""
    steps, why_not = await _llm_steps(session, project, description, target_url, feature)
    needs_authoring = not steps
    if needs_authoring:
        steps = skeleton_steps(skeleton_text, target_url)
    payload = {
        "name": name,
        "steps": steps,
        "code_paths": [],
        "tags": sorted(set(tags + (["needs-authoring"] if needs_authoring else []))),
        "needs_authoring": needs_authoring,
        **origin,
    }
    proposal = CaseProposal(
        project_id=project.id, test_suite_id=suite_id, action=CaseProposalAction.CREATE,
        payload=payload, rationale=rationale[:500],
        ai_confidence=0.0 if needs_authoring else GENERATED_CONFIDENCE,
        agent_id=agent_id, created_by_id=user_id,
        created_by_agent_id=agent_id, agent_session_id=agent_session_id,
    )
    session.add(proposal)
    await session.flush()
    if not needs_authoring and user_id is not None:
        from app.api.agent_ownership import maybe_auto_apply
        await maybe_auto_apply(proposal, user_id, session)
    return proposal, why_not


async def link_accepted_case(proposal: CaseProposal, case_id: int, user_id: Optional[int],
                             session: AsyncSession) -> None:
    """Called from `_apply_proposal` after a CREATE lands. No-op for ordinary
    proposals."""
    payload = proposal.payload or {}
    req_id = payload.get("requirement_id")
    if isinstance(req_id, int):
        req = await session.get(Requirement, req_id)
        if req is not None and req.project_id == proposal.project_id:
            from sqlmodel import select
            exists = (await session.exec(select(RequirementLink).where(
                RequirementLink.test_case_id == case_id,
                RequirementLink.ref == req.external_key))).first()
            if not exists:
                session.add(RequirementLink(
                    test_case_id=case_id, project_id=req.project_id, ref=req.external_key,
                    source=req.source, title=req.title, url=req.url, created_by_id=user_id))
    inc_id = payload.get("incident_id")
    if isinstance(inc_id, int):
        inc = await session.get(Incident, inc_id)
        if inc is not None and inc.project_id == proposal.project_id:
            inc.regression_case_id = case_id
            inc.regression_status = "accepted"
            inc.updated_at = datetime.utcnow()
            session.add(inc)
