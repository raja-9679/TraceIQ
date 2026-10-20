"""Test ownership + periodic review endpoints (WP16b, decision V5).

    GET  /api/cases/{id}/ownership                owner, backup, review status
    PUT  /api/cases/{id}/ownership                set owner / backup (editor, audited)
    POST /api/cases/{id}/reviewed                 mark reviewed (editor, humans only, audited)
    GET  /api/projects/{id}/test-review           cases with their review status
                                                  (?status=needs_review|all, ?owner=me|<id>|none)

Rules live in app/services/ownership_review.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project, TestCase, TestSuite, User
from app.services import ownership_review as svc
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

MAX_LIST = 1000


async def _project(principal: AuthPrincipal, project_id: Optional[int], session: AsyncSession,
                   min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id) if project_id else None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project.id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project.id, session, min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} access required")
    return project


async def _case(case_id: int, principal: AuthPrincipal, session: AsyncSession, min_role: str = "viewer"):
    case = await session.get(TestCase, case_id)
    if case is None or case.project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    project = await _project(principal, case.project_id, session, min_role)
    return case, project


class _People:
    """User rows + project access, looked up once per request."""

    def __init__(self, session: AsyncSession, project_id: int):
        self.session, self.project_id = session, project_id
        self._cache: Dict[int, Optional[Dict[str, Any]]] = {}

    async def get(self, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        if user_id not in self._cache:
            user = await self.session.get(User, user_id)
            access = bool(user) and await access_service.has_project_access(user_id, self.project_id, self.session)
            self._cache[user_id] = svc.person(user, access) if user else {
                "id": user_id, "name": None, "email": None, "state": "no_access"}
        return self._cache[user_id]


async def _interval_for(session: AsyncSession, project_id: int, suite_id: Optional[int]) -> int:
    suites = (await session.exec(select(TestSuite).where(TestSuite.project_id == project_id))).all()
    return svc.effective_intervals(suites).get(suite_id, svc.DEFAULT_REVIEW_INTERVAL_DAYS)


async def _view(case: TestCase, interval: int, people: _People) -> Dict[str, Any]:
    status = svc.review_status(case.last_reviewed_at, interval)
    return {
        "case_id": case.id, "name": case.name, "test_suite_id": case.test_suite_id,
        "project_id": case.project_id, "priority": case.priority,
        "owner": await people.get(case.owner_user_id),
        "backup_owner": await people.get(case.backup_owner_user_id),
        "last_reviewed_at": case.last_reviewed_at,
        "last_reviewed_by": await people.get(case.last_reviewed_by_id),
        "review_interval_days": interval,
        "review_status": status["status"], "review_due_at": status["due_at"],
    }


@router.get("/cases/{case_id}/ownership")
async def get_ownership(case_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                        session: AsyncSession = Depends(get_session)):
    case, project = await _case(case_id, principal, session)
    interval = await _interval_for(session, project.id, case.test_suite_id)
    return await _view(case, interval, _People(session, project.id))


class OwnershipUpdate(BaseModel):
    # Omitted = unchanged; null = clear.
    owner_user_id: Optional[int] = None
    backup_owner_user_id: Optional[int] = None


@router.put("/cases/{case_id}/ownership")
async def set_ownership(case_id: int, body: OwnershipUpdate,
                        principal: AuthPrincipal = Depends(get_current_principal),
                        session: AsyncSession = Depends(get_session)):
    case, project = await _case(case_id, principal, session, min_role="editor")
    from app.services.agent_controls import enforce_agent_write
    await enforce_agent_write(session, principal, project.id)
    fields = body.model_dump(exclude_unset=True)
    new = {"owner_user_id": case.owner_user_id, "backup_owner_user_id": case.backup_owner_user_id, **fields}
    for key, uid in fields.items():
        if uid is None:
            continue
        user = await session.get(User, uid)
        # Same answer for "no such user" and "not on this project": the
        # caller must not learn who exists elsewhere on the instance.
        if user is None or not user.is_active or not await access_service.has_project_access(uid, project.id, session):
            raise HTTPException(status_code=400,
                                detail=f"{key.replace('_user_id', '').replace('_', ' ')} must be an active "
                                       "user with access to this project")
    if new["owner_user_id"] and new["owner_user_id"] == new["backup_owner_user_id"]:
        raise HTTPException(status_code=400, detail="The backup owner must be a different person")
    changes = {k: {"from": getattr(case, k), "to": v} for k, v in fields.items() if getattr(case, k) != v}
    if changes:
        for k, v in fields.items():
            setattr(case, k, v)
        session.add(case)
        await audit_record(session, entity_type="case", entity_id=case.id, action="ownership_set",
                           user_id=principal.user.id, workspace_id=project.workspace_id,
                           principal=principal, changes=changes)
        await session.commit()
        await session.refresh(case)
    interval = await _interval_for(session, project.id, case.test_suite_id)
    return await _view(case, interval, _People(session, project.id))


@router.post("/cases/{case_id}/reviewed")
async def mark_reviewed(case_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                        session: AsyncSession = Depends(get_session)):
    """A person confirms the case still tests something worth testing.
    Refused for API keys: an agent marking its own (or any) test reviewed
    is exactly what a periodic human review is meant to catch."""
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="Marking a test reviewed needs a signed-in person, not an API key")
    case, project = await _case(case_id, principal, session, min_role="editor")
    previous = case.last_reviewed_at
    case.last_reviewed_at = datetime.utcnow()
    case.last_reviewed_by_id = principal.user.id
    session.add(case)
    await audit_record(session, entity_type="case", entity_id=case.id, action="reviewed",
                       user_id=principal.user.id, workspace_id=project.workspace_id, principal=principal,
                       changes={"last_reviewed_at": {"from": previous.isoformat() if previous else None,
                                                     "to": case.last_reviewed_at.isoformat()}})
    await session.commit()
    await session.refresh(case)
    interval = await _interval_for(session, project.id, case.test_suite_id)
    return await _view(case, interval, _People(session, project.id))


@router.get("/projects/{project_id}/test-review")
async def test_review_list(project_id: int,
                           status: str = Query("needs_review", pattern="^(needs_review|all)$"),
                           owner: Optional[str] = Query(None, max_length=20),
                           principal: AuthPrincipal = Depends(get_current_principal),
                           session: AsyncSession = Depends(get_session)):
    """The project's cases with review status; default only those that need
    a review (never reviewed, or overdue), most overdue first, never-reviewed
    last. `owner`: `me`, a user id, `none` (unowned) or `no_access`."""
    project = await _project(principal, project_id, session)
    suites = (await session.exec(select(TestSuite).where(TestSuite.project_id == project.id))).all()
    intervals = svc.effective_intervals(suites)
    suite_names = {s.id: s.name for s in suites}
    stmt = select(TestCase).where(TestCase.project_id == project.id)
    if owner == "me":
        stmt = stmt.where(TestCase.owner_user_id == principal.user.id)
    elif owner == "none":
        stmt = stmt.where(TestCase.owner_user_id.is_(None))
    elif owner and owner.isdigit():
        stmt = stmt.where(TestCase.owner_user_id == int(owner))
    elif owner not in (None, "", "no_access"):
        raise HTTPException(status_code=400, detail="owner must be me, none, no_access or a user id")
    cases = (await session.exec(stmt)).all()
    people = _People(session, project.id)
    rows: List[Dict[str, Any]] = []
    counts = {"never": 0, "overdue": 0, "ok": 0, "not_required": 0, "owner_no_access": 0, "unowned": 0}
    for case in cases:
        view = await _view(case, intervals.get(case.test_suite_id, svc.DEFAULT_REVIEW_INTERVAL_DAYS), people)
        view["suite_name"] = suite_names.get(case.test_suite_id)
        counts[view["review_status"]] += 1
        if view["owner"] is None:
            counts["unowned"] += 1
        elif view["owner"]["state"] == "no_access":
            counts["owner_no_access"] += 1
        if owner == "no_access" and not (view["owner"] and view["owner"]["state"] == "no_access"):
            continue
        if status == "needs_review" and view["review_status"] not in svc.NEEDS_REVIEW:
            continue
        rows.append(view)
    rows.sort(key=lambda r: (r["review_status"] == "never", r["review_due_at"] or datetime.max, r["name"] or ""))
    return {"project_id": project.id, "counts": counts, "total": len(rows),
            "truncated": len(rows) > MAX_LIST, "items": rows[:MAX_LIST],
            "default_interval_days": svc.DEFAULT_REVIEW_INTERVAL_DAYS}
