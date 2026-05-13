"""Phase D — Agent owns the test suite.

Endpoints exposed here let an AI coding agent:
  • Map a PR diff to the subset of tests that should run (impact analysis).
  • Inspect a project's tested surface (suites, route coverage, gaps).
  • Look at a case's recent run history before deciding to modify it.
  • Propose case creates / updates / deletes via a review queue (mirrors
    the SelectorHealProposal pattern; nothing is auto-applied unless an
    operator explicitly accepts it).

The MCP server (integrations/mcp-server/) wraps these as agent tools.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    CaseProposal,
    CaseProposalAction,
    CaseProposalCreate,
    CaseProposalRead,
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    ImpactedCase,
    Project,
    TestCase,
    TestCaseResult,
    TestRun,
    TestSuite,
)
from app.services.access_service import access_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------

@router.post("/runs/impact-analysis", response_model=ImpactAnalysisResponse)
async def impact_analysis(
    body: ImpactAnalysisRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> ImpactAnalysisResponse:
    """Given a set of changed file paths, return the tests that exercise them.

    Matching is **path-prefix or glob**. Each TestCase.code_paths entry can
    be either:
      • a bare prefix:  `frontend/src/Checkout/`  → matches any file under that path
      • a glob:         `backend/app/api/**/*.py` → matches via fnmatch
    """
    project = await session.get(Project, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    res = await session.exec(
        select(TestCase).where(TestCase.project_id == body.project_id)
    )
    cases: List[TestCase] = list(res.all())

    matched: List[ImpactedCase] = []
    cases_without_paths = 0
    files_matched: set = set()

    for case in cases:
        code_paths = case.code_paths or []
        if not code_paths:
            cases_without_paths += 1
            if body.include_no_code_paths:
                matched.append(ImpactedCase(
                    id=case.id, name=case.name, test_suite_id=case.test_suite_id,
                    is_ai_authored=case.is_ai_authored, matched_paths=[],
                ))
            continue
        hits: List[str] = []
        for f in body.changed_files:
            for p in code_paths:
                if _path_matches(f, p):
                    hits.append(f)
                    files_matched.add(f)
                    break
        if hits:
            matched.append(ImpactedCase(
                id=case.id, name=case.name, test_suite_id=case.test_suite_id,
                is_ai_authored=case.is_ai_authored, matched_paths=hits,
            ))

    unmatched = [f for f in body.changed_files if f not in files_matched]
    return ImpactAnalysisResponse(
        matched_cases=matched,
        cases_without_code_paths=cases_without_paths,
        unmatched_files=unmatched,
    )


def _path_matches(file_path: str, pattern: str) -> bool:
    if not pattern:
        return False
    if any(ch in pattern for ch in "*?["):
        return fnmatch(file_path, pattern)
    # Bare prefix
    return file_path == pattern or file_path.startswith(pattern.rstrip("/") + "/")


# ---------------------------------------------------------------------------
# App surface — what's tested, what isn't (heuristic)
# ---------------------------------------------------------------------------

@router.get("/apps/{project_id}/surface")
async def app_surface(
    project_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Summarize the project's tested surface for an agent.

    Returned shape (informational, may evolve):
      {
        "project": { id, name },
        "suite_tree": [{id, name, parent_id, case_count, ai_authored_count}, ...],
        "routes_covered": ["/checkout", "/login", ...],   # distinct goto URLs
        "code_paths_covered": ["frontend/src/...", ...],  # union of TestCase.code_paths
        "recent_runs": [{id, status, suite_name, created_at}, ...],
        "case_counts": { total, ai_authored, human_reviewed, with_code_paths }
      }
    """
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
        principal.user.id, project_id, session
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    suites_res = await session.exec(
        select(TestSuite).where(TestSuite.project_id == project_id)
    )
    suites = list(suites_res.all())

    cases_res = await session.exec(
        select(TestCase).where(TestCase.project_id == project_id)
    )
    cases = list(cases_res.all())

    case_count_by_suite: Dict[int, int] = defaultdict(int)
    ai_count_by_suite: Dict[int, int] = defaultdict(int)
    routes_covered: set = set()
    code_paths_covered: set = set()

    total = len(cases)
    ai_authored = 0
    human_reviewed = 0
    with_code_paths = 0

    for case in cases:
        if case.test_suite_id is not None:
            case_count_by_suite[case.test_suite_id] += 1
            if case.is_ai_authored:
                ai_count_by_suite[case.test_suite_id] += 1
        if case.is_ai_authored:
            ai_authored += 1
        if case.last_human_reviewed_at is not None:
            human_reviewed += 1
        if case.code_paths:
            with_code_paths += 1
            for p in case.code_paths:
                code_paths_covered.add(p)
        for step in case.steps or []:
            if isinstance(step, dict) and step.get("type") == "goto" and step.get("value"):
                routes_covered.add(step["value"])

    suite_tree = [
        {
            "id": s.id,
            "name": s.name,
            "parent_id": s.parent_id,
            "case_count": case_count_by_suite.get(s.id, 0),
            "ai_authored_count": ai_count_by_suite.get(s.id, 0),
        }
        for s in suites
    ]

    runs_res = await session.exec(
        select(TestRun)
        .where(TestRun.project_id == project_id)
        .order_by(TestRun.created_at.desc())
        .limit(15)
    )
    recent = [
        {
            "id": r.id,
            "status": str(r.status.value) if hasattr(r.status, "value") else str(r.status),
            "suite_name": r.suite_name,
            "test_case_name": r.test_case_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "triggered_by": str(r.triggered_by.value) if hasattr(r.triggered_by, "value") else str(r.triggered_by),
            "git_commit": r.git_commit,
        }
        for r in runs_res.all()
    ]

    return {
        "project": {"id": project.id, "name": project.name},
        "suite_tree": suite_tree,
        "routes_covered": sorted(routes_covered),
        "code_paths_covered": sorted(code_paths_covered),
        "recent_runs": recent,
        "case_counts": {
            "total": total,
            "ai_authored": ai_authored,
            "human_reviewed": human_reviewed,
            "with_code_paths": with_code_paths,
        },
    }


# ---------------------------------------------------------------------------
# Case run history — last N runs touching a given case
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}/run-history")
async def case_run_history(
    case_id: int,
    limit: int = 30,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(
        principal.user.id, case.project_id, session
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    # The matching is by case name within results. A more precise lookup would
    # join through TestRun.test_case_id but that field is only set for
    # single-case runs; suite runs surface results by name. We honor both.
    runs_res = await session.exec(
        select(TestRun)
        .where(
            (TestRun.test_case_id == case_id)
            | (TestRun.project_id == case.project_id)
        )
        .order_by(TestRun.created_at.desc())
        .limit(200)
    )
    runs = list(runs_res.all())

    history: List[Dict[str, Any]] = []
    seen = 0
    for run in runs:
        if seen >= limit:
            break
        if run.test_case_id == case_id:
            history.append({
                "run_id": run.id,
                "status": str(run.status.value) if hasattr(run.status, "value") else str(run.status),
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "duration_ms": run.duration_ms,
                "git_commit": run.git_commit,
                "triggered_by": str(run.triggered_by.value) if hasattr(run.triggered_by, "value") else str(run.triggered_by),
            })
            seen += 1
            continue
        # Try to match by name in TestCaseResult rows.
        res = await session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run.id,
                TestCaseResult.test_name == case.name,
            )
        )
        result_row = res.first()
        if result_row:
            history.append({
                "run_id": run.id,
                "status": str(result_row.status.value) if hasattr(result_row.status, "value") else str(result_row.status),
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "duration_ms": result_row.duration_ms,
                "git_commit": run.git_commit,
                "triggered_by": str(run.triggered_by.value) if hasattr(run.triggered_by, "value") else str(run.triggered_by),
                "via_suite_run": True,
            })
            seen += 1

    # Summarize: pass/fail counts, last-failure timestamp.
    passes = sum(1 for h in history if h["status"] == "passed")
    fails = sum(1 for h in history if h["status"] in ("failed", "error"))
    last_failure = next((h["created_at"] for h in history if h["status"] in ("failed", "error")), None)

    return {
        "case": {
            "id": case.id, "name": case.name,
            "is_ai_authored": case.is_ai_authored,
            "ai_confidence": case.ai_confidence,
            "last_human_reviewed_at": case.last_human_reviewed_at.isoformat() if case.last_human_reviewed_at else None,
        },
        "history": history,
        "summary": {
            "sample_size": len(history),
            "passes": passes,
            "failures": fails,
            "last_failure_at": last_failure,
        },
    }


# ---------------------------------------------------------------------------
# Case proposal queue
# ---------------------------------------------------------------------------

@router.post("/case-proposals", response_model=CaseProposalRead)
async def create_proposal(
    body: CaseProposalCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> CaseProposalRead:
    """Agents call this to propose a change; humans (or auto-approval policy)
    later decide. Editor role on the project is required so anonymous agents
    can't flood the queue.
    """
    project = await session.get(Project, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    if body.action == CaseProposalAction.CREATE and body.test_suite_id is None:
        raise HTTPException(status_code=400, detail="CREATE requires test_suite_id")
    if body.action in (CaseProposalAction.UPDATE, CaseProposalAction.DELETE, CaseProposalAction.MOVE):
        if body.target_case_id is None:
            raise HTTPException(status_code=400, detail=f"{body.action.value.upper()} requires target_case_id")

    proposal = CaseProposal(
        project_id=body.project_id,
        test_suite_id=body.test_suite_id,
        target_case_id=body.target_case_id,
        action=body.action,
        payload=body.payload or {},
        rationale=body.rationale,
        ai_confidence=body.ai_confidence,
        agent_id=principal.agent_id,
    )
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return _proposal_read(proposal)


@router.get("/case-proposals", response_model=List[CaseProposalRead])
async def list_proposals(
    project_id: Optional[int] = None,
    status: Optional[str] = "pending",
    limit: int = 100,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[CaseProposalRead]:
    query = select(CaseProposal)
    if project_id is not None:
        if not await access_service.has_project_access(
            principal.user.id, project_id, session
        ):
            raise HTTPException(status_code=403, detail="Access denied")
        query = query.where(CaseProposal.project_id == project_id)
    if status:
        query = query.where(CaseProposal.status == status)
    res = await session.exec(query.order_by(CaseProposal.created_at.desc()).limit(limit))
    return [_proposal_read(p) for p in res.all()]


@router.post("/case-proposals/{proposal_id}/accept", response_model=CaseProposalRead)
async def accept_proposal(
    proposal_id: int,
    note: Optional[str] = Body(None, embed=True),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> CaseProposalRead:
    proposal = await session.get(CaseProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")
    if not await access_service.has_project_access(
        principal.user.id, proposal.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    # API keys cannot accept proposals — that's the whole point of the queue.
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot accept proposals")

    await _apply_proposal(proposal, principal.user.id, session)
    proposal.status = "accepted"
    proposal.decided_at = datetime.utcnow()
    proposal.decided_by_id = principal.user.id
    proposal.decision_note = note
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return _proposal_read(proposal)


@router.post("/case-proposals/{proposal_id}/reject", response_model=CaseProposalRead)
async def reject_proposal(
    proposal_id: int,
    note: Optional[str] = Body(None, embed=True),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> CaseProposalRead:
    proposal = await session.get(CaseProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")
    if not await access_service.has_project_access(
        principal.user.id, proposal.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot reject proposals")

    proposal.status = "rejected"
    proposal.decided_at = datetime.utcnow()
    proposal.decided_by_id = principal.user.id
    proposal.decision_note = note
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return _proposal_read(proposal)


async def _apply_proposal(proposal: CaseProposal, user_id: int, session: AsyncSession) -> None:
    """Apply an accepted proposal: create/update/delete/move the test case."""
    payload = proposal.payload or {}
    if proposal.action == CaseProposalAction.CREATE:
        case = TestCase(
            name=payload.get("name") or "Proposed case",
            steps=payload.get("steps") or [],
            test_suite_id=proposal.test_suite_id,
            project_id=proposal.project_id,
            created_by_id=user_id,
            updated_by_id=user_id,
            code_paths=payload.get("code_paths") or [],
            is_ai_authored=True,
            ai_confidence=proposal.ai_confidence,
            last_human_reviewed_at=datetime.utcnow(),
            last_human_reviewed_by_id=user_id,
        )
        session.add(case)
        return

    case = await session.get(TestCase, proposal.target_case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Target case missing")

    if proposal.action == CaseProposalAction.UPDATE:
        if "name" in payload:
            case.name = payload["name"]
        if "steps" in payload:
            case.steps = payload["steps"]
        if "code_paths" in payload:
            case.code_paths = payload["code_paths"]
        case.updated_at = datetime.utcnow()
        case.updated_by_id = user_id
        case.last_human_reviewed_at = datetime.utcnow()
        case.last_human_reviewed_by_id = user_id
        session.add(case)
    elif proposal.action == CaseProposalAction.DELETE:
        await session.delete(case)
    elif proposal.action == CaseProposalAction.MOVE:
        new_suite_id = payload.get("new_test_suite_id")
        if not new_suite_id:
            raise HTTPException(status_code=400, detail="MOVE payload missing new_test_suite_id")
        case.test_suite_id = new_suite_id
        case.updated_at = datetime.utcnow()
        case.updated_by_id = user_id
        session.add(case)


def _proposal_read(p: CaseProposal) -> CaseProposalRead:
    return CaseProposalRead(
        id=p.id, project_id=p.project_id, test_suite_id=p.test_suite_id,
        target_case_id=p.target_case_id,
        action=str(p.action.value) if hasattr(p.action, "value") else str(p.action),
        payload=p.payload, rationale=p.rationale, ai_confidence=p.ai_confidence,
        agent_id=p.agent_id, source_run_id=p.source_run_id, status=p.status,
        created_at=p.created_at, decided_at=p.decided_at,
    )
