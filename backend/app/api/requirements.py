"""Requirements imported from Jira / Linear, tests generated from them, and
per-requirement coverage (WP6).

Surface (all project-scoped; viewer reads, editor writes):
    POST   /api/projects/{id}/requirements/search    {config_id, jql? | team_key?/query?/label?/state?}
    POST   /api/projects/{id}/requirements/import    {config_id, keys: [...]}
    GET    /api/projects/{id}/requirements/imported  list + coverage + totals
    POST   /api/projects/{id}/requirements/sync      re-sync every imported requirement
    GET    /api/requirements/{id}                    detail: criteria, proposals, covering cases
    POST   /api/requirements/{id}/sync
    POST   /api/requirements/{id}/generate           {test_suite_id, target_url?, criteria?, include_ambiguous?, regenerate?}
    DELETE /api/requirements/{id}

The older `GET /projects/{id}/requirements` (app/api/traceability.py) is the
free-form RequirementLink rollup and is unchanged; an imported requirement's
coverage reads the same RequirementLink rows, matched on `ref == external_key`,
so a manual link made before the import counts too.

Tracker connections are workspace-scoped (`IssueTrackerConfig`); every use
re-checks that the connection belongs to the project's workspace — the
config id comes from the caller, and without that check an editor in one
tenant could read another tenant's Jira with its stored credential.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.secrets import decrypt_secret
from app.models import (
    CaseProposal, IssueTrackerConfig, Project, Requirement, RequirementLink, TestCase,
    TestCaseResult, TestRun, TestStatus, TestSuite, UserWorkspace,
)
from app.services import acceptance_criteria, generation_proposals, tracker_import
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.run_scope import counted

router = APIRouter()

_FAIL = (TestStatus.FAILED, TestStatus.ERROR)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403,
                            detail="Editor access required" if min_role != "viewer" else "Access denied")
    return project


async def _requirement(req_id: int, principal: AuthPrincipal, session: AsyncSession,
                       min_role: str = "viewer") -> Tuple[Requirement, Project]:
    req = await session.get(Requirement, req_id)
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    project = await _project(req.project_id, principal, session, min_role)
    return req, project


async def _tracker(config_id: Optional[int], project: Project,
                   session: AsyncSession) -> Tuple[IssueTrackerConfig, str]:
    config = await session.get(IssueTrackerConfig, config_id) if config_id else None
    # Same 404 for "missing" and "another workspace's": don't confirm ids.
    if not config or config.workspace_id != project.workspace_id:
        raise HTTPException(status_code=404, detail="Tracker connection not found in this workspace")
    if config.provider not in tracker_import.IMPORT_PROVIDERS:
        raise HTTPException(status_code=400,
                            detail=f"Requirements can be imported from Jira or Linear, not {config.provider}")
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Tracker connection is disabled")
    try:
        secret = decrypt_secret(config.auth_secret_encrypted)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Stored tracker credential could not be decrypted")
    return config, secret


async def _require_workspace_member(principal: AuthPrincipal, project: Project,
                                    session: AsyncSession) -> None:
    """Tracker connections are workspace-wide credentials that can run any JQL
    the connection can see. Project access alone is not enough to wield them:
    a direct project grant can outlive the user's workspace membership."""
    member = await access_service.workspace_membership(
        principal.user.id, project.workspace_id, session, project_id=project.id)
    if member is None:
        raise HTTPException(status_code=403,
                            detail="Only members of this workspace can use its tracker connections")


def _tracker_error(exc: tracker_import.TrackerImportError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Import / sync
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split())


def apply_issue(req: Requirement, issue: tracker_import.ExternalIssue) -> None:
    """Copy a fetched issue onto the row and re-derive its criteria, keeping the
    proposal a criterion already has when the criterion text is unchanged —
    otherwise every re-sync would make the reviewer generate everything again."""
    previous = {_norm(c.get("text", "")): c for c in (req.acceptance_criteria or [])}
    criteria = acceptance_criteria.analyse(issue.description)
    if not criteria:
        # Nothing recognisable: the ticket title is the one thing it asks for.
        amb = acceptance_criteria.detect_ambiguity(issue.title or "")
        criteria = [{"index": 0, "text": issue.title or req.external_key, "kind": "title",
                     "ambiguous": amb["ambiguous"], "reasons": amb["reasons"]}]
    for c in criteria:
        prior = previous.get(_norm(c["text"]))
        if prior and prior.get("proposal_id"):
            c["proposal_id"] = prior["proposal_id"]
    req.title = (issue.title or req.external_key)[:500]
    req.url = tracker_import.safe_link(issue.url)
    req.description = issue.description
    req.external_status = issue.status
    req.external_id = issue.external_id or req.external_id
    req.external_updated_at = issue.updated_at
    req.acceptance_criteria = criteria
    req.last_synced_at = datetime.utcnow()
    req.sync_error = None


class SearchBody(BaseModel):
    config_id: int
    jql: Optional[str] = PydField(default=None, max_length=2000)
    team_key: Optional[str] = PydField(default=None, max_length=50)
    query: Optional[str] = PydField(default=None, max_length=200)
    label: Optional[str] = PydField(default=None, max_length=100)
    state: Optional[str] = PydField(default=None, max_length=100)
    max_results: int = PydField(default=25, ge=1, le=tracker_import.MAX_RESULTS)


class ImportBody(BaseModel):
    config_id: int
    keys: List[str] = PydField(min_length=1, max_length=tracker_import.MAX_RESULTS)


@router.post("/projects/{project_id}/requirements/search")
async def search_tracker(project_id: int, body: SearchBody,
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "editor")
    await _require_workspace_member(principal, project, session)
    config, secret = await _tracker(body.config_id, project, session)
    try:
        issues = await tracker_import.search(
            config.provider, config.base_url, config.auth_user, secret, jql=body.jql,
            linear={"team_key": body.team_key, "query": body.query, "label": body.label,
                    "state": body.state},
            max_results=body.max_results)
    except tracker_import.TrackerImportError as exc:
        raise _tracker_error(exc)
    imported = set((await session.exec(select(Requirement.external_key).where(
        Requirement.project_id == project_id, Requirement.source == config.provider))).all())
    return {"provider": config.provider,
            "issues": [{**i.summary(), "already_imported": i.key in imported} for i in issues]}


@router.post("/projects/{project_id}/requirements/import")
async def import_requirements(project_id: int, body: ImportBody,
                              principal: AuthPrincipal = Depends(get_current_principal),
                              session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "editor")
    await _require_workspace_member(principal, project, session)
    config, secret = await _tracker(body.config_id, project, session)
    try:
        issues = await tracker_import.fetch(config.provider, config.base_url, config.auth_user,
                                            secret, body.keys)
    except tracker_import.TrackerImportError as exc:
        raise _tracker_error(exc)

    found = {i.key for i in issues}
    created, updated = [], []
    for issue in issues:
        req = (await session.exec(select(Requirement).where(
            Requirement.project_id == project_id, Requirement.source == config.provider,
            Requirement.external_key == issue.key))).first()
        if req is None:
            req = Requirement(project_id=project_id, tracker_config_id=config.id,
                              source=config.provider, external_key=issue.key,
                              title=issue.title or issue.key, created_by_id=principal.user.id)
            created.append(req)
        else:
            req.tracker_config_id = config.id
            updated.append(req)
        apply_issue(req, issue)
        session.add(req)
    await session.flush()
    await audit_record(
        session, entity_type="project", entity_id=project_id, action="requirements_import",
        principal=principal, workspace_id=project.workspace_id,
        changes={"tracker_config_id": config.id, "provider": config.provider,
                 "created": [r.external_key for r in created],
                 "updated": [r.external_key for r in updated]})
    await session.commit()
    rows = created + updated
    cov = await coverage_for(session, project_id, rows)
    return {"created": len(created), "updated": len(updated),
            "not_found": [k for k in (x.strip().upper() for x in body.keys) if k and k not in found],
            "requirements": [serialize(r, cov.get(r.external_key)) for r in rows]}


async def _sync_one(session: AsyncSession, req: Requirement, project: Project) -> Optional[str]:
    """Re-fetch one requirement. Returns an error string (also stored on the row)."""
    if not req.tracker_config_id:
        req.sync_error = "Tracker connection was deleted; re-import to sync"
        session.add(req)
        return req.sync_error
    try:
        config, secret = await _tracker(req.tracker_config_id, project, session)
        issues = await tracker_import.fetch(config.provider, config.base_url, config.auth_user,
                                            secret, [req.external_key])
    except HTTPException as exc:
        req.sync_error = str(exc.detail)
        session.add(req)
        return req.sync_error
    except tracker_import.TrackerImportError as exc:
        req.sync_error = str(exc)
        session.add(req)
        return req.sync_error
    if not issues:
        req.sync_error = "Issue no longer exists or is not visible to this connection"
        req.last_synced_at = datetime.utcnow()
        session.add(req)
        return req.sync_error
    apply_issue(req, issues[0])
    session.add(req)
    return None


@router.post("/requirements/{req_id}/sync")
async def sync_requirement(req_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                           session: AsyncSession = Depends(get_session)):
    req, project = await _requirement(req_id, principal, session, "editor")
    await _require_workspace_member(principal, project, session)
    error = await _sync_one(session, req, project)
    await session.commit()
    await session.refresh(req)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return await _detail(session, req)


@router.post("/projects/{project_id}/requirements/sync")
async def sync_project_requirements(project_id: int,
                                    principal: AuthPrincipal = Depends(get_current_principal),
                                    session: AsyncSession = Depends(get_session)):
    project = await _project(project_id, principal, session, "editor")
    await _require_workspace_member(principal, project, session)
    reqs = (await session.exec(select(Requirement).where(
        Requirement.project_id == project_id).order_by(Requirement.id))).all()
    errors = {}
    for req in reqs:
        err = await _sync_one(session, req, project)
        if err:
            errors[req.external_key] = err
    await session.commit()
    return {"synced": len(reqs) - len(errors), "errors": errors}


@router.delete("/requirements/{req_id}")
async def delete_requirement(req_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                             session: AsyncSession = Depends(get_session)):
    req, project = await _requirement(req_id, principal, session, "editor")
    # RequirementLink rows stay: they belong to the cases, and the free-form
    # traceability rollup still uses them.
    await audit_record(session, entity_type="requirement", entity_id=req.id, action="delete",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"external_key": req.external_key, "source": req.source})
    await session.delete(req)
    await session.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

async def coverage_for(session: AsyncSession, project_id: int,
                       reqs: List[Requirement]) -> Dict[str, Dict[str, Any]]:
    """external_key → {status, cases: [{id, name, latest_status}], counts}.

    Status: `not_covered` (no linked case), `failing` (any linked case's latest
    result failed), `covered` (every linked case's latest result passed),
    `untested` (linked, but some case has never run)."""
    keys = {r.external_key for r in reqs}
    if not keys:
        return {}
    links = (await session.exec(
        select(RequirementLink.ref, TestCase.id, TestCase.name)
        .join(TestCase, TestCase.id == RequirementLink.test_case_id)
        .where(RequirementLink.project_id == project_id, RequirementLink.ref.in_(list(keys)),
               TestCase.project_id == project_id))).all()
    case_ids = {cid for _r, cid, _n in links}
    names = {name for _r, _c, name in links}
    latest_by_id: Dict[int, TestStatus] = {}
    latest_by_name: Dict[str, TestStatus] = {}
    if case_ids:
        rows = (await session.exec(
            select(TestCaseResult.test_case_id, TestCaseResult.test_name, TestCaseResult.status)
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(TestRun.project_id == project_id, counted(),
                   (TestCaseResult.test_case_id.in_(list(case_ids)))
                   | (TestCaseResult.test_name.in_(list(names))))
            .order_by(TestCaseResult.id.desc()))).all()
        for cid, name, status in rows:
            if cid is not None:
                latest_by_id.setdefault(cid, status)
            latest_by_name.setdefault(name, status)

    out: Dict[str, Dict[str, Any]] = {k: {"status": "not_covered", "cases": [], "passing": 0,
                                          "failing": 0, "untested": 0} for k in keys}
    seen = set()
    for ref, cid, name in links:
        if (ref, cid) in seen:
            continue
        seen.add((ref, cid))
        # Prefer the id-linked result; results written before the aggregator
        # stamped test_case_id only carry the name.
        st = latest_by_id.get(cid) or latest_by_name.get(name)
        b = out[ref]
        status = st.value if hasattr(st, "value") else st
        b["cases"].append({"id": cid, "name": name, "latest_status": status})
        if st == TestStatus.PASSED:
            b["passing"] += 1
        elif st in _FAIL:
            b["failing"] += 1
        else:
            b["untested"] += 1
    for b in out.values():
        if not b["cases"]:
            b["status"] = "not_covered"
        elif b["failing"]:
            b["status"] = "failing"
        elif b["untested"]:
            b["status"] = "untested"
        else:
            b["status"] = "covered"
    return out


def serialize(req: Requirement, coverage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    criteria = req.acceptance_criteria or []
    return {
        "id": req.id, "project_id": req.project_id, "tracker_config_id": req.tracker_config_id,
        "source": req.source, "external_key": req.external_key, "title": req.title,
        "url": req.url, "external_status": req.external_status,
        "external_updated_at": req.external_updated_at, "last_synced_at": req.last_synced_at,
        "sync_error": req.sync_error, "created_at": req.created_at,
        "criteria_count": len(criteria),
        "ambiguous_count": sum(1 for c in criteria if c.get("ambiguous")),
        "proposed_count": sum(1 for c in criteria if c.get("proposal_id")),
        "coverage": coverage or {"status": "not_covered", "cases": [], "passing": 0,
                                 "failing": 0, "untested": 0},
    }


@router.get("/projects/{project_id}/requirements/imported")
async def list_imported(project_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                        session: AsyncSession = Depends(get_session)):
    await _project(project_id, principal, session)
    reqs = (await session.exec(select(Requirement).where(
        Requirement.project_id == project_id).order_by(Requirement.external_key))).all()
    cov = await coverage_for(session, project_id, list(reqs))
    rows = [serialize(r, cov.get(r.external_key)) for r in reqs]
    totals = {s: sum(1 for r in rows if r["coverage"]["status"] == s)
              for s in ("covered", "failing", "untested", "not_covered")}
    return {"requirements": rows, "totals": totals}


async def _detail(session: AsyncSession, req: Requirement) -> Dict[str, Any]:
    cov = await coverage_for(session, req.project_id, [req])
    out = serialize(req, cov.get(req.external_key))
    criteria = [dict(c) for c in (req.acceptance_criteria or [])]
    pids = [c["proposal_id"] for c in criteria if c.get("proposal_id")]
    statuses = {}
    if pids:
        rows = (await session.exec(select(CaseProposal.id, CaseProposal.status, CaseProposal.payload)
                                   .where(CaseProposal.id.in_(pids),
                                          CaseProposal.project_id == req.project_id))).all()
        statuses = {pid: (st, bool((payload or {}).get("needs_authoring"))) for pid, st, payload in rows}
    for c in criteria:
        pid = c.get("proposal_id")
        if pid:
            st = statuses.get(pid)
            c["proposal_status"] = st[0] if st else "missing"
            c["needs_authoring"] = st[1] if st else False
    out["description"] = req.description
    out["acceptance_criteria"] = criteria
    return out


@router.get("/requirements/{req_id}")
async def get_requirement(req_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                          session: AsyncSession = Depends(get_session)):
    req, _project_row = await _requirement(req_id, principal, session)
    return await _detail(session, req)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class GenerateBody(BaseModel):
    test_suite_id: int
    target_url: Optional[str] = PydField(default=None, max_length=2000)
    criteria: Optional[List[int]] = None     # indexes; default = all
    include_ambiguous: bool = False          # generate flagged criteria anyway
    regenerate: bool = False                 # replace a pending/rejected proposal


@router.post("/requirements/{req_id}/generate")
async def generate_for_requirement(req_id: int, body: GenerateBody,
                                   principal: AuthPrincipal = Depends(get_current_principal),
                                   session: AsyncSession = Depends(get_session)):
    """One CREATE proposal per acceptance criterion — never a direct write.

    Criteria flagged ambiguous are reported back instead of generated unless
    `include_ambiguous`. A criterion that already has a pending or accepted
    proposal is skipped unless `regenerate` (and an accepted one is never
    regenerated: its case exists, edit that)."""
    req, project = await _requirement(req_id, principal, session, "editor")
    suite = await session.get(TestSuite, body.test_suite_id)
    if not suite or suite.project_id != req.project_id:
        raise HTTPException(status_code=404, detail="Target suite not found in this project")

    criteria = [dict(c) for c in (req.acceptance_criteria or [])]
    wanted = set(body.criteria) if body.criteria is not None else {c["index"] for c in criteria}
    existing_ids = [c["proposal_id"] for c in criteria if c.get("proposal_id")]
    existing = {}
    if existing_ids:
        existing = dict((await session.exec(select(CaseProposal.id, CaseProposal.status).where(
            CaseProposal.id.in_(existing_ids)))).all())

    generated, ambiguous, skipped = [], [], []
    for c in criteria:
        if c["index"] not in wanted:
            continue
        if c.get("ambiguous") and not body.include_ambiguous:
            ambiguous.append({"index": c["index"], "text": c["text"], "reasons": c.get("reasons", [])})
            continue
        prior = existing.get(c.get("proposal_id"))
        if prior == "accepted" or (prior == "pending" and not body.regenerate):
            skipped.append({"index": c["index"], "reason": f"proposal #{c['proposal_id']} is {prior}"})
            continue
        n = c["index"] + 1
        proposal, why_not = await generation_proposals.propose(
            session, project=project, suite_id=suite.id,
            name=generation_proposals.case_name(f"{req.external_key} AC{n}:", c["text"]),
            description=(f"Requirement {req.external_key}: {req.title}\n"
                         f"Acceptance criterion {n}:\n{c['text']}"),
            skeleton_text=c["text"], target_url=body.target_url,
            origin={"requirement_id": req.id, "criterion_index": c["index"]},
            tags=[f"req:{req.external_key}"],
            rationale=f"{req.external_key} AC{n}: {c['text']}",
            user_id=principal.user.id, feature="requirement_generation",
            agent_id=principal.agent_id, agent_session_id=principal.agent_session_id)
        c["proposal_id"] = proposal.id
        generated.append({"index": c["index"], "proposal_id": proposal.id,
                          "status": proposal.status,
                          "needs_authoring": bool((proposal.payload or {}).get("needs_authoring")),
                          "needs_authoring_reason": why_not})
    req.acceptance_criteria = criteria
    session.add(req)
    if generated:
        await audit_record(
            session, entity_type="requirement", entity_id=req.id, action="generate_proposals",
            principal=principal, workspace_id=project.workspace_id,
            changes={"external_key": req.external_key,
                     "proposal_ids": [g["proposal_id"] for g in generated],
                     "ambiguous": [a["index"] for a in ambiguous]})
    await session.commit()
    return {"generated": generated, "ambiguous": ambiguous, "skipped": skipped}
