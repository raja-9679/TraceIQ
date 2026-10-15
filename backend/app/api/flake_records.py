"""FlakeRecord management — list, quarantine/release, ownership.

Quarantined records are skipped at dispatch time (see worker.py) until their
quarantine expires; the rules live in app/services/flake_policy.py. The flake
score is maintained by the result aggregator.

Surface:
    GET   /api/flakes?test_case_id=&project_id=&quarantined_only=
    GET   /api/flakes/owner-candidates?project_id=
    POST  /api/flakes/{id}/quarantine   {reason, days?, owner_user_id?}
    POST  /api/flakes/{id}/release
    PUT   /api/flakes/{id}/owner        {owner_user_id: int | null}

Quarantine, release and owner changes are written to the audit trail.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.api.jobs import api_key_allows_project
from app.models import FlakeRecord, Project, TestCase, User, UserWorkspace
from app.services import flake_policy
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


def _check_key_scope(principal: AuthPrincipal, project_id: int) -> None:
    """A project-scoped API key acts on its own project only, whatever its
    creator could reach."""
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")


class QuarantineRequest(BaseModel):
    reason: str = PydField(min_length=1, max_length=500)
    days: int = PydField(default=flake_policy.DEFAULT_QUARANTINE_DAYS,
                         ge=1, le=flake_policy.MAX_QUARANTINE_DAYS)
    # Defaults to the current owner, else the person quarantining.
    owner_user_id: Optional[int] = None


class OwnerRequest(BaseModel):
    owner_user_id: Optional[int] = None


async def _users_by_id(ids, session: AsyncSession) -> Dict[int, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await session.exec(select(User).where(User.id.in_(ids)))).all()
    return {u.id: u for u in rows}


def _person(user: Optional[User]) -> Optional[dict]:
    if user is None:
        return None
    return {"id": user.id, "email": user.email, "full_name": user.full_name}


def serialize(record: FlakeRecord, users: Dict[int, User]) -> dict:
    out = record.model_dump()
    out["quarantine_state"] = flake_policy.quarantine_state(record)
    out["owner"] = _person(users.get(record.owner_user_id))
    out["quarantined_by"] = _person(users.get(record.quarantined_by_id))
    return out


@router.get("/flakes")
async def list_flakes(
    test_case_id: Optional[int] = None,
    project_id: Optional[int] = None,
    quarantined_only: bool = False,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    # Scope the listing: a case filter checks that case's project; a project
    # filter checks the project; unfiltered listings are refused (the table is
    # cross-tenant).
    if test_case_id is not None:
        case = await session.get(TestCase, test_case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Test case not found")
        if not await access_service.has_project_access(principal.user.id, case.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
    elif project_id is not None:
        if not await access_service.has_project_access(principal.user.id, project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide test_case_id or project_id to scope the listing")

    query = select(FlakeRecord)
    if test_case_id is not None:
        query = query.where(FlakeRecord.test_case_id == test_case_id)
    if project_id is not None:
        query = (query.join(TestCase, TestCase.id == FlakeRecord.test_case_id)
                 .where(TestCase.project_id == project_id))
    if quarantined_only:
        # Includes expired quarantines: they still need a decision.
        query = query.where(FlakeRecord.is_quarantined == True)  # noqa: E712
    records = (await session.exec(query.order_by(FlakeRecord.flake_score.desc()).limit(200))).all()
    users = await _users_by_id(
        [r.owner_user_id for r in records] + [r.quarantined_by_id for r in records], session)
    return [serialize(r, users) for r in records]


async def _load(flake_id: int, principal: AuthPrincipal, session: AsyncSession):
    """The record, its case and project — editor access required."""
    record = await session.get(FlakeRecord, flake_id)
    if not record:
        raise HTTPException(status_code=404, detail="Flake record not found")
    case = await session.get(TestCase, record.test_case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(
            principal.user.id, case.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    _check_key_scope(principal, case.project_id)
    project = await session.get(Project, case.project_id)
    return record, case, project


async def _can_own(owner_id: int, project_id: int, session: AsyncSession) -> bool:
    """An owner must be an active user who can edit the project — otherwise
    they could not fix the test they are made responsible for."""
    user = await session.get(User, owner_id)
    return bool(user and user.is_active and await access_service.has_project_access(
        owner_id, project_id, session, min_role="editor"))


async def _check_owner(owner_id: int, project_id: int, session: AsyncSession) -> None:
    if not await _can_own(owner_id, project_id, session):
        raise HTTPException(status_code=422,
                            detail="Owner must be an active user with editor access to this project")


@router.get("/flakes/owner-candidates")
async def owner_candidates(
    project_id: int = Query(...),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Workspace members who could own a flaky test in this project."""
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_key_scope(principal, project_id)
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    members = (await session.exec(
        select(User).join(UserWorkspace, UserWorkspace.user_id == User.id)
        .where(UserWorkspace.workspace_id == project.workspace_id,
               User.is_active == True)  # noqa: E712
        .order_by(User.full_name))).all()
    out: List[dict] = []
    for user in members:
        if await access_service.has_project_access(user.id, project_id, session, min_role="editor"):
            out.append(_person(user))
    return out


@router.post("/flakes/{flake_id}/quarantine")
async def quarantine(
    flake_id: int,
    body: QuarantineRequest,
    request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    record, case, project = await _load(flake_id, principal, session)
    previous_owner = record.owner_user_id
    if body.owner_user_id:
        owner_id = body.owner_user_id
        await _check_owner(owner_id, case.project_id, session)
    elif previous_owner and await _can_own(previous_owner, case.project_id, session):
        owner_id = previous_owner
    else:
        # No owner, or one who has since lost access: the caller takes it.
        owner_id = principal.user.id
    flake_policy.quarantine(record, source=flake_policy.SOURCE_MANUAL, days=body.days,
                            reason=body.reason.strip(), by_user_id=principal.user.id)
    record.owner_user_id = owner_id
    session.add(record)
    await audit_record(
        session, entity_type="flake_record", entity_id=flake_id, action="quarantine",
        workspace_id=project.workspace_id if project else None,
        principal=principal, request=request,
        changes={"test_case_id": case.id, "reason": record.quarantine_reason,
                 "expires_at": record.quarantine_expires_at.isoformat(),
                 "owner_user_id": {"from": previous_owner, "to": owner_id}},
    )
    await session.commit()
    await session.refresh(record)
    users = await _users_by_id([record.owner_user_id, record.quarantined_by_id], session)
    return serialize(record, users)


@router.post("/flakes/{flake_id}/release")
async def release(
    flake_id: int,
    request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    record, case, project = await _load(flake_id, principal, session)
    was = {"state": flake_policy.quarantine_state(record), "reason": record.quarantine_reason,
           "source": record.quarantine_source}
    # Every row for the case: dispatch skips a case if ANY of its rows (case-
    # or step-level) is quarantined, so releasing one row only would leave it
    # hidden with a Release button that does nothing.
    siblings = (await session.exec(select(FlakeRecord).where(
        FlakeRecord.test_case_id == record.test_case_id,
        FlakeRecord.is_quarantined == True))).all()  # noqa: E712
    for row in {r.id: r for r in [record, *siblings]}.values():
        flake_policy.release(row)
        session.add(row)
    await audit_record(
        session, entity_type="flake_record", entity_id=flake_id, action="release",
        workspace_id=project.workspace_id if project else None,
        principal=principal, request=request,
        changes={"test_case_id": case.id, "was": was},
    )
    await session.commit()
    await session.refresh(record)
    users = await _users_by_id([record.owner_user_id], session)
    return serialize(record, users)


@router.put("/flakes/{flake_id}/owner")
async def set_owner(
    flake_id: int,
    body: OwnerRequest,
    request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    record, case, project = await _load(flake_id, principal, session)
    if body.owner_user_id is not None:
        await _check_owner(body.owner_user_id, case.project_id, session)
    previous = record.owner_user_id
    record.owner_user_id = body.owner_user_id
    session.add(record)
    if previous != body.owner_user_id:
        await audit_record(
            session, entity_type="flake_record", entity_id=flake_id, action="set_owner",
            workspace_id=project.workspace_id if project else None,
            principal=principal, request=request,
            changes={"test_case_id": case.id,
                     "owner_user_id": {"from": previous, "to": body.owner_user_id}},
        )
    await session.commit()
    await session.refresh(record)
    users = await _users_by_id([record.owner_user_id, record.quarantined_by_id], session)
    return serialize(record, users)
