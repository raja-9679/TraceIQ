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
from pydantic import BaseModel
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    AuditLog,
    CaseProposal,
    CaseProposalAction,
    CaseProposalCreate,
    CaseProposalRead,
    FlakeRecord,
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    ImpactedCase,
    ImpactFlakeInfo,
    ImpactLastResult,
    ImpactMatch,
    Project,
    TestCase,
    TestCaseResult,
    TestRun,
    TestStatus,
    TestSuite,
)
from app.services.access_service import access_service
from app.core.secrets import decrypt_json, encrypt_json

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

    suites_res = await session.exec(
        select(TestSuite.id, TestSuite.name).where(
            TestSuite.project_id == body.project_id))
    suite_names: Dict[int, str] = {sid: name for sid, name in suites_res.all()}

    matched: List[ImpactedCase] = []
    cases_without_paths = 0
    files_matched: set = set()

    for case in cases:
        code_paths = case.code_paths or []
        if not code_paths:
            cases_without_paths += 1
            if body.include_no_code_paths:
                entry = await _enrich_impacted_case(
                    session, case, suite_names, hits=[], pairs=[])
                entry.suggested_action = "review"
                entry.reasons.append(
                    "no code_paths mapping — add one with set_code_paths")
                matched.append(entry)
            continue
        hits: List[str] = []
        pairs: List[ImpactMatch] = []
        for f in body.changed_files:
            for p in code_paths:
                if _path_matches(f, p):
                    hits.append(f)
                    pairs.append(ImpactMatch(file=f, pattern=p))
                    files_matched.add(f)
                    break
        if hits:
            matched.append(await _enrich_impacted_case(
                session, case, suite_names, hits=hits, pairs=pairs))

    unmatched = [f for f in body.changed_files if f not in files_matched]
    return ImpactAnalysisResponse(
        matched_cases=matched,
        cases_without_code_paths=cases_without_paths,
        unmatched_files=unmatched,
    )


# A single bare-prefix pattern swallowing this many changed files suggests the
# mapping is too coarse to be trusted — run, but re-derive code_paths too.
_BROAD_PREFIX_THRESHOLD = 5


async def _enrich_impacted_case(
    session: AsyncSession,
    case: TestCase,
    suite_names: Dict[int, str],
    hits: List[str],
    pairs: List[ImpactMatch],
) -> ImpactedCase:
    """Build the v2 ImpactedCase: match detail, last result, flake state, and
    a deterministic run/review suggestion with human-readable reasons."""
    last_result: Optional[ImpactLastResult] = None
    row = (await session.exec(
        select(TestCaseResult, TestRun)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestCaseResult.test_case_id == case.id)
        .order_by(TestCaseResult.id.desc())
        .limit(1)
    )).first()
    if row is None:
        # Pre-migration rows: fall back to name matching within the project.
        row = (await session.exec(
            select(TestCaseResult, TestRun)
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(
                TestCaseResult.test_name == case.name,
                TestRun.project_id == case.project_id,
            )
            .order_by(TestCaseResult.id.desc())
            .limit(1)
        )).first()
    if row is not None:
        result_row, run = row
        last_result = ImpactLastResult(
            status=(result_row.status.value
                    if hasattr(result_row.status, "value") else str(result_row.status)),
            run_id=run.id,
            at=run.created_at,
            git_commit=run.git_commit,
        )

    flake: Optional[ImpactFlakeInfo] = None
    flake_row = (await session.exec(
        select(FlakeRecord).where(
            FlakeRecord.test_case_id == case.id,
            FlakeRecord.step_id.is_(None))
    )).first()
    if flake_row is not None:
        flake = ImpactFlakeInfo(
            flake_score=flake_row.flake_score or 0.0,
            is_quarantined=flake_row.is_quarantined,
        )

    reasons: List[str] = []
    action = "run"
    if last_result and last_result.status in (
            TestStatus.FAILED.value, TestStatus.ERROR.value, "failed", "error"):
        action = "review"
        reasons.append("last recorded result failed — the case may need "
                       "updating before its result means anything")
    if flake and flake.is_quarantined:
        action = "review"
        reasons.append("case is quarantined as flaky")
    if case.is_ai_authored and case.last_human_reviewed_at is None:
        action = "review"
        reasons.append("AI-authored and never human-reviewed")
    if action == "run":
        prefix_hits: Dict[str, int] = defaultdict(int)
        for m in pairs:
            if not any(ch in m.pattern for ch in "*?["):
                prefix_hits[m.pattern] += 1
        broad = [p for p, n in prefix_hits.items() if n >= _BROAD_PREFIX_THRESHOLD]
        if broad:
            action = "run_then_review"
            reasons.append(
                f"bare prefix {broad[0]!r} matched {prefix_hits[broad[0]]} "
                "changed files — the code_paths mapping may be too coarse; "
                "re-derive it after running")

    return ImpactedCase(
        id=case.id,
        name=case.name,
        test_suite_id=case.test_suite_id,
        is_ai_authored=case.is_ai_authored,
        matched_paths=hits,
        suite_name=suite_names.get(case.test_suite_id),
        matched=pairs,
        tags=case.tags or [],
        priority=case.priority,
        ai_confidence=case.ai_confidence,
        last_human_reviewed_at=case.last_human_reviewed_at,
        last_validated_commit=case.last_validated_commit,
        last_validated_at=case.last_validated_at,
        last_result=last_result,
        flake=flake,
        suggested_action=action,
        reasons=reasons,
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


@router.post("/apps/{project_id}/discover")
async def crawl_app_surface(
    project_id: int,
    body: Dict[str, Any] = Body(...),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Mode-2 (URL-only) discovery: crawl a live app the agent has no source
    for and return its interactable surface (forms, buttons, internal links).

    Body: {"base_url": "https://app...", "max_pages": 10}. Dispatches a
    discovery job to the worker pool and long-polls for the result. If the
    project has a fresh auth session, the crawl runs authenticated.
    """
    import asyncio
    import json
    import uuid

    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(principal.user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")

    base_url = (body or {}).get("base_url")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")
    # SSRF guard: a worker crawls this URL from inside the compose network and
    # long-polls the result back to the caller, so validate it the same way
    # case_generation does before dispatch.
    from app.core.net_guard import validate_outbound_url, UnsafeUrlError
    try:
        await validate_outbound_url(base_url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Refusing to crawl this URL: {exc}")
    max_pages = int((body or {}).get("max_pages", 10))

    import redis as _redis
    from app.core.config import settings as _settings
    from app.models import AuthSession

    rc = _redis.from_url(_settings.CELERY_BROKER_URL, decode_responses=True)

    # Authenticated crawl when a fresh session exists.
    storage_state = None
    auth_res = await session.exec(select(AuthSession).where(AuthSession.project_id == project_id))
    auth = auth_res.first()
    if auth and auth.storage_state:
        age_min = (datetime.utcnow() - auth.captured_at).total_seconds() / 60
        if age_min < auth.max_age_minutes:
            storage_state = decrypt_json(auth.storage_state)

    discovery_id = str(uuid.uuid4())
    job = {
        "job_id": discovery_id,
        "discovery_id": discovery_id,
        "job_type": "discovery",
        "run_id": 0,
        "base_url": base_url,
        "max_pages": max_pages,
        "browser": "chromium",
        "settings": {"storage_state": storage_state} if storage_state else {},
    }
    try:
        rc.xgroup_create("jobs:pending", "execution-workers", id="0", mkstream=True)
    except _redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    rc.xadd("jobs:pending", {"job_id": discovery_id, "run_id": "0", "payload": json.dumps(job)})

    # Long-poll for the worker result (cap ~90s).
    result_key = f"discovery:result:{discovery_id}"
    for _ in range(90):
        raw = rc.get(result_key)
        if raw:
            rc.delete(result_key)
            return json.loads(raw)
        await asyncio.sleep(1)

    return {
        "status": "pending",
        "discovery_id": discovery_id,
        "detail": "Crawl still running; retry get with this discovery_id or increase max_pages budget.",
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

    history: List[Dict[str, Any]] = []
    covered_run_ids: set = set()

    # Precise path: results stamped with this case's id (aggregator writes the
    # link since the impact-analysis-v2 migration; single-case runs backfilled).
    id_rows = await session.exec(
        select(TestCaseResult, TestRun)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestCaseResult.test_case_id == case_id)
        .order_by(TestCaseResult.id.desc())
        .limit(limit)
    )
    for result_row, run in id_rows.all():
        covered_run_ids.add(run.id)
        history.append({
            "run_id": run.id,
            "status": str(result_row.status.value) if hasattr(result_row.status, "value") else str(result_row.status),
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "duration_ms": result_row.duration_ms,
            "git_commit": run.git_commit,
            "triggered_by": str(run.triggered_by.value) if hasattr(run.triggered_by, "value") else str(run.triggered_by),
            "matched_by": "id",
        })

    # Fallback for pre-migration rows: run-level status of single-case runs,
    # then name matching on unstamped result rows. Can conflate same-named
    # cases — hence the explicit matched_by flag.
    if len(history) < limit:
        runs_res = await session.exec(
            select(TestRun)
            .where(
                (TestRun.test_case_id == case_id)
                | (TestRun.project_id == case.project_id)
            )
            .order_by(TestRun.created_at.desc())
            .limit(200)
        )
        for run in runs_res.all():
            if len(history) >= limit:
                break
            if run.id in covered_run_ids:
                continue
            if run.test_case_id == case_id:
                history.append({
                    "run_id": run.id,
                    "status": str(run.status.value) if hasattr(run.status, "value") else str(run.status),
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "duration_ms": run.duration_ms,
                    "git_commit": run.git_commit,
                    "triggered_by": str(run.triggered_by.value) if hasattr(run.triggered_by, "value") else str(run.triggered_by),
                    "matched_by": "id",
                })
                continue
            res = await session.exec(
                select(TestCaseResult).where(
                    TestCaseResult.test_run_id == run.id,
                    TestCaseResult.test_name == case.name,
                    TestCaseResult.test_case_id.is_(None),
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
                    "matched_by": "name",
                })
        history.sort(key=lambda h: h["created_at"] or "", reverse=True)

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
    # Tenant isolation: every caller-supplied object id must resolve inside the
    # project we just access-checked, otherwise a proposal against project X can
    # mutate/read another tenant's case or suite once accepted (the accept path
    # authorizes against proposal.project_id, i.e. the attacker's own project).
    if body.test_suite_id is not None:
        _suite = await session.get(TestSuite, body.test_suite_id)
        if not _suite or _suite.project_id != body.project_id:
            raise HTTPException(status_code=404, detail="Target suite not found in this project")
    if body.target_case_id is not None:
        _case = await session.get(TestCase, body.target_case_id)
        if not _case or _case.project_id != body.project_id:
            raise HTTPException(status_code=404, detail="Target case not found in this project")
    if body.action == CaseProposalAction.MOVE:
        _new_suite_id = (body.payload or {}).get("new_test_suite_id")
        if _new_suite_id is None:
            raise HTTPException(status_code=400, detail="MOVE requires payload.new_test_suite_id")
        _new_suite = await session.get(TestSuite, _new_suite_id)
        if not _new_suite or _new_suite.project_id != body.project_id:
            raise HTTPException(status_code=404, detail="Destination suite not found in this project")
    if body.action == CaseProposalAction.UPDATE_SUITE_SETTINGS:
        if body.test_suite_id is None:
            raise HTTPException(status_code=400, detail="UPDATE_SUITE_SETTINGS requires test_suite_id")
        if not isinstance((body.payload or {}).get("settings"), dict) and \
                (body.payload or {}).get("inherit_settings") is None:
            raise HTTPException(
                status_code=400,
                detail="UPDATE_SUITE_SETTINGS payload needs a settings object and/or inherit_settings")
        suite = await session.get(TestSuite, body.test_suite_id)
        if not suite or suite.project_id != body.project_id:
            raise HTTPException(status_code=404, detail="Target suite not found in this project")

    proposal = CaseProposal(
        project_id=body.project_id,
        test_suite_id=body.test_suite_id,
        target_case_id=body.target_case_id,
        action=body.action,
        payload=body.payload or {},
        rationale=body.rationale,
        ai_confidence=body.ai_confidence,
        agent_id=principal.agent_id,
        # Separation of duties (F4): the human behind this call, whether they
        # filed it in the UI or their API key did. An agent acting under a
        # developer's key IS that developer for review purposes — otherwise
        # "propose via key, accept in the UI" is a trivial way around the queue.
        created_by_id=principal.user.id if principal.user else None,
        # Phase E: provenance — sets together so the reviewer can later
        # auto-approve "same agent, same session" delete proposals.
        created_by_agent_id=principal.agent_id,
        agent_session_id=principal.agent_session_id,
    )
    session.add(proposal)
    await session.flush()
    # Workspace auto-apply policy: high-confidence CREATE/UPDATE proposals
    # can merge immediately (snapshotted in TestCaseRevision either way).
    await maybe_auto_apply(proposal, principal.user.id, session)
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
    rows = res.all()
    if project_id is not None:
        # Access to the single requested project was already verified above.
        return [_proposal_read(p) for p in rows]
    # No project filter: never return proposals for projects the caller can't
    # access. Filter each row by access, caching the per-project decision so a
    # long result set doesn't fan out into a query per row.
    access_cache: dict[int, bool] = {}
    visible: List[CaseProposal] = []
    for p in rows:
        allowed = access_cache.get(p.project_id)
        if allowed is None:
            allowed = await access_service.has_project_access(
                principal.user.id, p.project_id, session)
            access_cache[p.project_id] = allowed
        if allowed:
            visible.append(p)
    return [_proposal_read(p) for p in visible]


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

    # Separation of duties (F4). Blocking API keys was never enough on its own:
    # the same human could file a proposal with their key and accept it in the
    # UI a second later, which is a review queue in name only.
    await _enforce_separation(proposal, principal.user.id, session)

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


async def _enforce_separation(proposal: CaseProposal, approver_id: int,
                              session: AsyncSession) -> None:
    """Refuse self-approval when the workspace or the instance requires a second
    person. Rejection is deliberately NOT gated — withdrawing your own proposal
    is harmless, and blocking it would leave proposals stuck with nobody able to
    clear them."""
    from app.models import Project, Workspace
    from app.services.proposal_policy import approver_conflict, separation_required

    project = await session.get(Project, proposal.project_id)
    workspace = await session.get(Workspace, project.workspace_id) if project else None
    required = separation_required(
        workspace_flag=bool(getattr(workspace, "require_separate_approver", False)))
    if approver_conflict(created_by_id=proposal.created_by_id,
                         approver_id=approver_id, required=required):
        raise HTTPException(
            status_code=403,
            detail="You filed this proposal, so you cannot accept it. This "
                   "workspace requires a second person to approve agent changes.")


async def maybe_auto_apply(proposal: CaseProposal, user_id: int, session: AsyncSession) -> bool:
    """Auto-apply policy: merge a fresh proposal immediately when the
    workspace opted in (auto_apply_threshold) and the proposal's confidence
    clears it. CREATE/UPDATE only — DELETE/MOVE/UPDATE_SUITE_SETTINGS always
    wait for a human (suite settings carry headers/auth sent to the app under
    test, so they are never applied on confidence alone).
    Safe because every applied change is snapshotted in TestCaseRevision.
    Caller commits. Returns True iff the proposal was applied."""
    try:
        if proposal.status != "pending" or proposal.ai_confidence is None:
            return False
        if proposal.action not in (CaseProposalAction.CREATE, CaseProposalAction.UPDATE):
            return False
        # Instance-wide kill switch (F4). An auto-applied change has no human
        # reviewer at any point, so an operator has to be able to switch that off
        # for the whole instance and demonstrate that it is off — which is why it
        # is an instance setting rather than an environment variable.
        from app.services.proposal_policy import auto_apply_disabled
        if auto_apply_disabled():
            return False
        from app.models import Project, Workspace
        project = await session.get(Project, proposal.project_id)
        if not project:
            return False
        workspace = await session.get(Workspace, project.workspace_id)
        threshold = getattr(workspace, "auto_apply_threshold", None) or 0
        if threshold <= 0 or proposal.ai_confidence < threshold:
            return False

        await _apply_proposal(proposal, user_id, session)
        proposal.status = "accepted"
        proposal.decided_at = datetime.utcnow()
        proposal.decided_by_id = None  # policy decision, not a human reviewer
        proposal.decision_note = (
            f"auto-applied: confidence {proposal.ai_confidence:.2f} >= "
            f"workspace threshold {threshold:.2f}")
        session.add(proposal)
        print(f"[AutoApply] proposal {proposal.id} ({proposal.action}) auto-applied "
              f"(confidence {proposal.ai_confidence:.2f} >= {threshold:.2f})")
        return True
    except Exception as exc:  # noqa: BLE001
        # Policy failures degrade to the normal review queue, never a 500.
        print(f"[AutoApply] policy check failed for proposal {proposal.id}: {exc}")
        return False


class ProposalPolicyBody(BaseModel):
    # None or 0 disables auto-apply.
    auto_apply_threshold: Optional[float] = None
    # F4: the proposer may not accept their own proposal. Optional so a caller
    # that only wants to change the threshold does not silently reset it.
    require_separate_approver: Optional[bool] = None


@router.get("/workspaces/{workspace_id}/proposal-policy")
async def get_proposal_policy(
    workspace_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    from app.models import UserWorkspace, Workspace
    membership = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.user_id == principal.user.id,
        ))
    if not membership.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    workspace = await session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    from app.services.proposal_policy import auto_apply_disabled, separation_required
    workspace_flag = bool(workspace.require_separate_approver)
    return {"workspace_id": workspace_id,
            "auto_apply_threshold": workspace.auto_apply_threshold,
            "require_separate_approver": workspace_flag,
            # Both instance policies are floors this workspace cannot lower, so
            # the UI has to be able to show that the effective answer differs
            # from the stored one.
            "separation_enforced": separation_required(workspace_flag=workspace_flag),
            "auto_apply_disabled_by_instance": auto_apply_disabled()}


@router.put("/workspaces/{workspace_id}/proposal-policy")
async def set_proposal_policy(
    workspace_id: int,
    body: ProposalPolicyBody,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    # Humans only — an agent must not be able to raise the bar it is
    # measured against (same rule as accepting proposals).
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot change the auto-apply policy")
    from app.models import Workspace
    from app.services.rbac_service import rbac_service
    # Raising the auto-apply bar governs whether agent proposals merge without
    # human review — a workspace-settings decision, not something any member may
    # flip. Require the manage-settings permission (workspace admin).
    if not await rbac_service.has_permission(
        session, principal.user.id, "workspace:manage_settings", workspace_id=workspace_id
    ):
        raise HTTPException(status_code=403, detail="Workspace admin required")
    workspace = await session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    threshold = body.auto_apply_threshold
    if threshold is not None and not (0 <= threshold <= 1):
        raise HTTPException(status_code=400, detail="auto_apply_threshold must be between 0 and 1")
    workspace.auto_apply_threshold = threshold or None
    if body.require_separate_approver is not None:
        workspace.require_separate_approver = bool(body.require_separate_approver)
    session.add(workspace)
    await session.commit()

    from app.services.proposal_policy import auto_apply_disabled, separation_required
    workspace_flag = bool(workspace.require_separate_approver)
    return {"workspace_id": workspace_id,
            "auto_apply_threshold": workspace.auto_apply_threshold,
            "require_separate_approver": workspace_flag,
            "separation_enforced": separation_required(workspace_flag=workspace_flag),
            "auto_apply_disabled_by_instance": auto_apply_disabled()}


def _validated_executor(payload: dict) -> str:
    """Executor for a proposed case: explicit value, else inferred from steps.

    Inference matters: agent-proposed mobile cases used to default to
    ui_playwright, land on web workers, and 'pass' with every mobile-* step
    skipped — a fake green. A case whose steps are mobile-* IS a mobile case
    regardless of what the payload forgot to say."""
    from app.models import ExecutorType

    explicit = payload.get("executor")
    if explicit:
        try:
            return ExecutorType(explicit).value
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown executor '{explicit}' — one of: "
                       f"{[e.value for e in ExecutorType]}")
    steps = payload.get("steps") or []
    step_types = [s.get("type", "") for s in steps if isinstance(s, dict)]
    if step_types and all(t.startswith("mobile-") for t in step_types):
        return ExecutorType.MOBILE_APPIUM.value
    return ExecutorType.UI_PLAYWRIGHT.value


async def _apply_proposal(proposal: CaseProposal, user_id: int, session: AsyncSession) -> None:
    """Apply an accepted proposal: create/update/delete/move the test case,
    or update the target suite's settings blob."""
    from app.services.test_service import normalize_steps

    payload = proposal.payload or {}
    if proposal.action == CaseProposalAction.CREATE:
        # Re-assert the destination suite still belongs to the proposal's
        # project at apply time (it could have been moved since propose).
        _dest = await session.get(TestSuite, proposal.test_suite_id)
        if not _dest or _dest.project_id != proposal.project_id:
            raise HTTPException(status_code=404, detail="Target suite not found in this project")
        case = TestCase(
            name=payload.get("name") or "Proposed case",
            steps=normalize_steps(payload.get("steps")),
            executor=_validated_executor(payload),
            test_suite_id=proposal.test_suite_id,
            project_id=proposal.project_id,
            created_by_id=user_id,
            updated_by_id=user_id,
            code_paths=payload.get("code_paths") or [],
            tags=payload.get("tags") or [],
            priority=payload.get("priority"),
            is_ai_authored=True,
            ai_confidence=proposal.ai_confidence,
            last_human_reviewed_at=datetime.utcnow(),
            last_human_reviewed_by_id=user_id,
            # Phase E: inherit provenance from the proposal so an accepted
            # CREATE keeps the agent_session_id chain unbroken.
            created_by_agent_id=proposal.created_by_agent_id or proposal.agent_id,
            agent_session_id=proposal.agent_session_id,
        )
        session.add(case)
        await session.flush()  # assign case.id so the revision can reference it
        from app.services.case_revisions import record_revision
        await record_revision(session, case, "proposal", user_id=user_id,
                              agent_id=proposal.agent_id)
        return

    if proposal.action == CaseProposalAction.UPDATE_SUITE_SETTINGS:
        suite = await session.get(TestSuite, proposal.test_suite_id)
        if not suite or suite.project_id != proposal.project_id:
            raise HTTPException(status_code=404, detail="Target suite not found in this project")
        if isinstance(payload.get("settings"), dict):
            if payload.get("merge", True):
                # Shallow-merge per settings key (proposed wins) — same
                # semantics the suite chain uses for parent→child overrides.
                from app.services.test_service import _merge_settings
                suite.settings = _merge_settings(suite.settings or {}, payload["settings"])
            else:
                suite.settings = payload["settings"]
            flag_modified(suite, "settings")
        if payload.get("inherit_settings") is not None:
            suite.inherit_settings = bool(payload["inherit_settings"])
        suite.updated_at = datetime.utcnow()
        suite.updated_by_id = user_id
        session.add(suite)
        session.add(AuditLog(
            entity_type="suite", entity_id=suite.id, action="update_settings",
            user_id=user_id,
            changes={"proposal_id": proposal.id, "payload": payload,
                     "agent_id": proposal.agent_id},
        ))
        return

    case = await session.get(TestCase, proposal.target_case_id)
    if not case or case.project_id != proposal.project_id:
        raise HTTPException(status_code=404, detail="Target case not found in this project")

    if proposal.action == CaseProposalAction.UPDATE:
        if "name" in payload:
            case.name = payload["name"]
        if "steps" in payload:
            case.steps = normalize_steps(payload["steps"])
        if "code_paths" in payload:
            case.code_paths = payload["code_paths"]
        if "tags" in payload:
            case.tags = payload["tags"]
        if "priority" in payload:
            case.priority = payload["priority"]
        if payload.get("executor"):
            case.executor = _validated_executor(payload)
        case.updated_at = datetime.utcnow()
        case.updated_by_id = user_id
        case.last_human_reviewed_at = datetime.utcnow()
        case.last_human_reviewed_by_id = user_id
        session.add(case)
        from app.services.case_revisions import record_revision
        await record_revision(session, case, "proposal", user_id=user_id,
                              agent_id=proposal.agent_id)
    elif proposal.action == CaseProposalAction.DELETE:
        await session.delete(case)
    elif proposal.action == CaseProposalAction.MOVE:
        new_suite_id = payload.get("new_test_suite_id")
        if not new_suite_id:
            raise HTTPException(status_code=400, detail="MOVE payload missing new_test_suite_id")
        _move_dest = await session.get(TestSuite, new_suite_id)
        if not _move_dest or _move_dest.project_id != proposal.project_id:
            raise HTTPException(status_code=404, detail="Destination suite not found in this project")
        case.test_suite_id = new_suite_id
        case.updated_at = datetime.utcnow()
        case.updated_by_id = user_id
        session.add(case)
        from app.services.case_revisions import record_revision
        await record_revision(session, case, "proposal", user_id=user_id,
                              agent_id=proposal.agent_id)


def _proposal_read(p: CaseProposal) -> CaseProposalRead:
    return CaseProposalRead(
        id=p.id, project_id=p.project_id, test_suite_id=p.test_suite_id,
        target_case_id=p.target_case_id,
        action=str(p.action.value) if hasattr(p.action, "value") else str(p.action),
        payload=p.payload, rationale=p.rationale, ai_confidence=p.ai_confidence,
        agent_id=p.agent_id, source_run_id=p.source_run_id, status=p.status,
        created_at=p.created_at, decided_at=p.decided_at,
    )
