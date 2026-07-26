"""Requirements / ticket traceability (PLATFORM_VISION.md §5, item 5).

Lightweight linking of test cases to external requirement/ticket refs, plus the
rollups that answer "is requirement X tested and passing?" and "what's untested?"
Not a full RTM (matrices are rejected in the gap analysis).

Per-test status is derived from the latest TestCaseResult matching the case's
name within the project (name-based, consistent with the rest of the app).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user
from app.services.access_service import access_service
from app.models import (
    User, TestCase, TestCaseResult, TestRun, TestStatus, RequirementLink,
    RequirementLinkCreate, RequirementLinkRead, RequirementCoverage,
)

router = APIRouter()
_FAIL = (TestStatus.FAILED, TestStatus.ERROR)


async def _latest_status_by_name(session: AsyncSession, project_id: int, names: set) -> dict:
    """Most-recent result status per test name (limited to `names`)."""
    if not names:
        return {}
    rows = (await session.exec(
        select(TestCaseResult.test_name, TestCaseResult.status, TestCaseResult.id)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestCaseResult.test_name.in_(list(names)))
        .order_by(TestCaseResult.id.desc()))).all()
    latest = {}
    for name, status, _rid in rows:
        latest.setdefault(name, status)
    return latest


@router.get("/cases/{case_id}/requirements", response_model=List[RequirementLinkRead])
async def list_case_requirements(case_id: int, session: AsyncSession = Depends(get_session),
                                 current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(current_user.id, case.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return (await session.exec(
        select(RequirementLink).where(RequirementLink.test_case_id == case_id))).all()


@router.post("/cases/{case_id}/requirements", response_model=RequirementLinkRead)
async def add_case_requirement(case_id: int, body: RequirementLinkCreate,
                               session: AsyncSession = Depends(get_session),
                               current_user: User = Depends(get_current_user)):
    case = await session.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(current_user.id, case.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    ref = (body.ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="ref is required")
    existing = (await session.exec(select(RequirementLink).where(
        RequirementLink.test_case_id == case_id, RequirementLink.ref == ref))).first()
    if existing:
        return existing
    link = RequirementLink(
        test_case_id=case_id, project_id=case.project_id, ref=ref,
        source=body.source or "manual", title=body.title, url=body.url,
        created_by_id=current_user.id)
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return link


@router.delete("/cases/{case_id}/requirements/{link_id}")
async def remove_case_requirement(case_id: int, link_id: int,
                                  session: AsyncSession = Depends(get_session),
                                  current_user: User = Depends(get_current_user)):
    link = await session.get(RequirementLink, link_id)
    if not link or link.test_case_id != case_id:
        raise HTTPException(status_code=404, detail="Link not found")
    if not await access_service.has_project_access(current_user.id, link.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    await session.delete(link)
    await session.commit()
    return {"status": "deleted"}


@router.get("/projects/{project_id}/requirements", response_model=List[RequirementCoverage])
async def project_requirements(project_id: int, session: AsyncSession = Depends(get_session),
                               current_user: User = Depends(get_current_user)):
    """Coverage rollup: one row per requirement ref, with the tests that cover
    it and an overall passing/failing/mixed/unknown status."""
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    rows = (await session.exec(
        select(RequirementLink, TestCase.name)
        .join(TestCase, TestCase.id == RequirementLink.test_case_id)
        .where(RequirementLink.project_id == project_id))).all()

    names = {name for _link, name in rows}
    latest = await _latest_status_by_name(session, project_id, names)

    by_ref: dict = {}
    for link, name in rows:
        b = by_ref.setdefault(link.ref, {
            "ref": link.ref, "source": link.source, "title": link.title, "url": link.url,
            "names": [], "passing": 0, "failing": 0, "untested": 0})
        b["names"].append(name)
        st = latest.get(name)
        if st == TestStatus.PASSED:
            b["passing"] += 1
        elif st in _FAIL:
            b["failing"] += 1
        else:
            b["untested"] += 1

    out = []
    for b in by_ref.values():
        if b["failing"]:
            status = "failing"
        elif b["passing"] and not b["untested"]:
            status = "passing"
        elif b["passing"]:
            status = "mixed"
        else:
            status = "unknown"
        out.append(RequirementCoverage(
            ref=b["ref"], source=b["source"], title=b["title"], url=b["url"],
            test_count=len(b["names"]), status=status,
            passing=b["passing"], failing=b["failing"], untested=b["untested"],
            test_names=b["names"]))
    out.sort(key=lambda c: ({"failing": 0, "mixed": 1, "unknown": 2, "passing": 3}[c.status], c.ref))
    return out


@router.get("/projects/{project_id}/traceability/gaps")
async def traceability_gaps(project_id: int, session: AsyncSession = Depends(get_session),
                            current_user: User = Depends(get_current_user)):
    """Test cases in the project with no requirement link (untraced coverage)."""
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    cases = (await session.exec(
        select(TestCase.id, TestCase.name).where(TestCase.project_id == project_id))).all()
    linked_ids = set((await session.exec(
        select(RequirementLink.test_case_id).where(RequirementLink.project_id == project_id))).all())
    untraced = [{"id": cid, "name": name} for cid, name in cases if cid not in linked_ids]
    total = len(cases)
    return {
        "project_id": project_id,
        "total_cases": total,
        "traced_cases": total - len(untraced),
        "untraced_cases": untraced,
    }
