"""Test-case revision history — list, inspect, restore.

Every case mutation (create / update / heal-accept / proposal-accept /
restore) appends an immutable snapshot via
`app.services.case_revisions.record_revision`. Restore copies a snapshot
back onto the live case and appends a new 'restore' revision — history is
append-only.

Surface:
    GET  /api/cases/{case_id}/revisions                    list (metadata)
    GET  /api/cases/{case_id}/revisions/{number}           full snapshot
    POST /api/cases/{case_id}/revisions/{number}/restore   roll back
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import AuditLog, TestCase, TestCaseRevision, TestCaseRevisionRead
from app.services.access_service import access_service
from app.services.case_revisions import apply_snapshot, record_revision

router = APIRouter()


async def _case_with_access(
    case_id: int, principal: AuthPrincipal, session: AsyncSession, min_role: str = "viewer",
) -> TestCase:
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role=min_role
    ):
        raise HTTPException(status_code=403, detail=f"{min_role.capitalize()} access required")
    return case


def _to_read(rev: TestCaseRevision, with_snapshot: bool = False) -> TestCaseRevisionRead:
    snapshot = rev.snapshot or {}
    return TestCaseRevisionRead(
        id=rev.id,
        test_case_id=rev.test_case_id,
        revision_number=rev.revision_number,
        change_source=rev.change_source,
        changed_by_id=rev.changed_by_id,
        changed_by_agent_id=rev.changed_by_agent_id,
        created_at=rev.created_at,
        name=snapshot.get("name"),
        step_count=len(snapshot.get("steps") or []),
        snapshot=snapshot if with_snapshot else None,
    )


@router.get("/cases/{case_id}/revisions", response_model=List[TestCaseRevisionRead])
async def list_revisions(
    case_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[TestCaseRevisionRead]:
    await _case_with_access(case_id, principal, session)
    result = await session.exec(
        select(TestCaseRevision)
        .where(TestCaseRevision.test_case_id == case_id)
        .order_by(TestCaseRevision.revision_number.desc())
    )
    return [_to_read(r) for r in result.all()]


@router.get("/cases/{case_id}/revisions/{number}", response_model=TestCaseRevisionRead)
async def get_revision(
    case_id: int,
    number: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseRevisionRead:
    await _case_with_access(case_id, principal, session)
    result = await session.exec(
        select(TestCaseRevision).where(
            TestCaseRevision.test_case_id == case_id,
            TestCaseRevision.revision_number == number,
        )
    )
    rev = result.first()
    if not rev:
        raise HTTPException(status_code=404, detail="Revision not found")
    return _to_read(rev, with_snapshot=True)


@router.post("/cases/{case_id}/revisions/{number}/restore", response_model=TestCaseRevisionRead)
async def restore_revision(
    case_id: int,
    number: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseRevisionRead:
    case = await _case_with_access(case_id, principal, session, min_role="editor")
    result = await session.exec(
        select(TestCaseRevision).where(
            TestCaseRevision.test_case_id == case_id,
            TestCaseRevision.revision_number == number,
        )
    )
    rev = result.first()
    if not rev:
        raise HTTPException(status_code=404, detail="Revision not found")

    apply_snapshot(case, rev.snapshot or {})
    case.updated_by_id = principal.user.id
    session.add(case)
    # The restore is itself a revision — history is append-only.
    new_rev = await record_revision(
        session, case, "restore", user_id=principal.user.id, agent_id=principal.agent_id
    )
    session.add(AuditLog(
        entity_type="case", entity_id=case_id, action="restore",
        user_id=principal.user.id,
        changes={"restored_from_revision": number},
    ))
    await session.commit()
    if new_rev:
        await session.refresh(new_rev)
        return _to_read(new_rev, with_snapshot=True)
    return _to_read(rev, with_snapshot=True)
