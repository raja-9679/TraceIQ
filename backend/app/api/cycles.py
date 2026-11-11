"""Test cycles: a round of testing for a release. Rules: app/services/cycles.py.

  GET    /projects/{project_id}/test-cycles          list with progress (viewer)
  POST   /projects/{project_id}/test-cycles          create (editor)
  GET    /test-cycles/assigned-to-me                 my open work, by cycle (a person)
  GET    /test-cycles/{id}                           cases, assignees, runs, sign-off (viewer)
  PATCH  /test-cycles/{id}                           rename, dates, release (editor)
  POST   /test-cycles/{id}/close | /reopen           (editor)
  DELETE /test-cycles/{id}                           (project admin; runs are kept)
  POST   /test-cycles/{id}/items                     add cases / a suite's cases (editor)
  POST   /test-cycles/{id}/items/remove              (editor)
  PUT    /test-cycles/{id}/assignments               assign or unassign cases (editor)
  POST   /test-cycles/{id}/manual-runs               start manual testing (editor)

Automated cases run through the ordinary POST /api/runs with `cycle_id` in
the body; a manual run started elsewhere joins with `cycle_id` on
POST /manual-runs. The go / no-go is POST /projects/{id}/signoffs with
`cycle_id`. A cycle stores no results of its own.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, func, update as sa_update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.manual_testing import _names, _project_access, create_manual_run
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    ExecutorType, Project, ReleaseSignoff, TestCase, TestCaseResult, TestCycle, TestCycleItem,
    TestRun, TestStatus, TestSuite, User,
)
from app.services import cycles as cy
from app.services import manual_testing as mt
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.run_scope import counted

router = APIRouter()

_LIVE = (TestStatus.PENDING, TestStatus.RUNNING)
MAX_RUNS_SHOWN = 200


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

class Person(BaseModel):
    id: int
    name: str


class Counts(BaseModel):
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    not_run: int = 0


class Progress(BaseModel):
    total: int
    executed: int
    counts: Counts
    percent_executed: Optional[int] = None
    percent_passed: Optional[int] = None


class AssigneeProgress(Progress):
    assignee: Optional[Person] = None


class SignoffOut(BaseModel):
    id: int
    verdict: str
    note: Optional[str] = None
    signed_by: Optional[Person] = None
    signed_at: datetime


class CycleOut(BaseModel):
    id: int
    project_id: int
    name: str
    release_label: Optional[str] = None
    git_commit: Optional[str] = None
    description: Optional[str] = None
    status: str
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    overdue: bool = False
    created_by: Optional[Person] = None
    created_at: datetime
    closed_by: Optional[Person] = None
    closed_at: Optional[datetime] = None
    progress: Progress
    manual_cases: int = 0
    automated_cases: int = 0
    signoff: Optional[SignoffOut] = None


class ItemOut(BaseModel):
    case_id: int
    name: str
    kind: str                       # manual | automated
    priority: Optional[str] = None
    suite_id: Optional[int] = None
    suite_name: Optional[str] = None
    # Top-level suite: automated cases are run per top-level suite.
    root_suite_id: Optional[int] = None
    assignee: Optional[Person] = None
    status: str
    run_id: Optional[int] = None
    result_id: Optional[int] = None
    tested_at: Optional[datetime] = None
    tested_by: Optional[Person] = None
    configurations: int = 0


class RunOut(BaseModel):
    id: int
    kind: str
    status: str
    live: bool
    suite_name: Optional[str] = None
    browser: Optional[str] = None
    device: Optional[str] = None
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None
    created_at: Optional[datetime] = None
    started_by: Optional[Person] = None


class CycleDetail(CycleOut):
    items: List[ItemOut] = []
    assignees: List[AssigneeProgress] = []
    runs: List[RunOut] = []
    runs_total: int = 0
    can_edit: bool = False


class MyCycle(BaseModel):
    cycle_id: int
    cycle_name: str
    project_id: int
    project_name: str
    due_date: Optional[date] = None
    assigned: int
    remaining: int


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

async def _cycle(session: AsyncSession, principal: AuthPrincipal, cycle_id: int,
                 min_role: str = "viewer") -> TestCycle:
    cycle = await session.get(TestCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Test cycle not found")
    await _project_access(session, principal, cycle.project_id, min_role)
    return cycle


async def _audit(session: AsyncSession, cycle_id: int, project_id: int, action: str,
                 principal: AuthPrincipal, request: Request, changes: Dict[str, Any]) -> None:
    # The workspace is named explicitly: "test_cycle" is not an entity the
    # audit service can resolve to one, and a row without it is invisible in
    # the workspace's audit log.
    project = await session.get(Project, project_id)
    await audit_record(session, entity_type="test_cycle", entity_id=cycle_id, action=action,
                       principal=principal, request=request,
                       workspace_id=project.workspace_id if project else None, changes=changes)


def _open(cycle: TestCycle) -> None:
    if cycle.status != cy.OPEN:
        raise HTTPException(status_code=409, detail="This test cycle is closed — reopen it to change it")


def _parse_at(recorded_at: Optional[str], fallback: Optional[datetime]) -> Optional[datetime]:
    if recorded_at:
        try:
            return datetime.fromisoformat(recorded_at)
        except ValueError:
            pass
    return fallback


async def _evidence(session: AsyncSession, project_id: int, cycle_ids: Sequence[int],
                    case_ids: Optional[Iterable[int]] = None) -> Dict[int, Dict[int, List[cy.Evidence]]]:
    """{cycle: {case: results}} from the cycles' own runs. Only the runs of
    this project that are not validation runs; the manual fields are pulled
    out of result_payload in SQL so step evidence never leaves Postgres."""
    out: Dict[int, Dict[int, List[cy.Evidence]]] = defaultdict(lambda: defaultdict(list))
    if not cycle_ids:
        return out
    payload = TestCaseResult.result_payload
    stmt = (select(TestRun.cycle_id, TestCaseResult.id, TestCaseResult.test_case_id,
                   TestCaseResult.status, TestRun.id, TestRun.browser, TestRun.device,
                   func.coalesce(TestRun.finalized_at, TestRun.created_at),
                   TestCaseResult.result_kind, payload["outcome"].as_string(),
                   payload["tester_id"].as_string(), payload["recorded_at"].as_string())
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(TestRun.cycle_id.in_(list(cycle_ids)), TestRun.project_id == project_id,
                   counted(), TestCaseResult.test_case_id.is_not(None)))
    if case_ids is not None:
        ids = list(case_ids)
        if not ids:
            return out
        stmt = stmt.where(TestCaseResult.test_case_id.in_(ids))
    for (cid, rid, case_id, status, run_id, browser, device, at, kind, outcome,
         tester, recorded) in (await session.exec(stmt)).all():
        manual = kind == mt.RESULT_KIND
        out[cid][case_id].append(cy.Evidence(
            result_id=rid, case_id=case_id, status=getattr(status, "value", status) or "",
            run_id=run_id, browser=browser, device=device,
            at=_parse_at(recorded, at) if manual else at, manual=manual,
            outcome=outcome if manual else None,
            tester_id=int(tester) if manual and tester and tester.isdigit() else None))
    return out


async def _items(session: AsyncSession, cycle_ids: Sequence[int]) -> Dict[int, List[tuple]]:
    """{cycle: [(item, case name, executor, priority, suite id, suite name)]}."""
    out: Dict[int, List[tuple]] = defaultdict(list)
    if not cycle_ids:
        return out
    rows = (await session.exec(
        select(TestCycleItem, TestCase.name, TestCase.executor, TestCase.priority,
               TestCase.test_suite_id, TestSuite.name)
        .join(TestCase, TestCase.id == TestCycleItem.test_case_id)
        .join(TestSuite, TestSuite.id == TestCase.test_suite_id, isouter=True)
        .where(TestCycleItem.cycle_id.in_(list(cycle_ids)))
        .order_by(TestSuite.name, TestCase.name, TestCase.id))).all()
    for row in rows:
        out[row[0].cycle_id].append(tuple(row))
    return out


def _kind(executor: Any) -> str:
    return "manual" if (getattr(executor, "value", executor) or "") == ExecutorType.MANUAL.value \
        else "automated"


def _person(names: Dict[int, str], uid: Optional[int]) -> Optional[Person]:
    return Person(id=uid, name=names[uid]) if uid in names else None


async def _latest_signoffs(session: AsyncSession, project_id: int,
                           cycle_ids: Sequence[int]) -> Dict[int, ReleaseSignoff]:
    out: Dict[int, ReleaseSignoff] = {}
    if not cycle_ids:
        return out
    rows = (await session.exec(
        select(ReleaseSignoff).where(ReleaseSignoff.project_id == project_id,
                                     ReleaseSignoff.cycle_id.in_(list(cycle_ids)))
        .order_by(ReleaseSignoff.signed_at, ReleaseSignoff.id))).all()
    for s in rows:
        out[s.cycle_id] = s          # ascending, so the newest wins
    return out


def _cycle_out(cycle: TestCycle, states: Sequence[str], kinds: Sequence[str],
               names: Dict[int, str], signoff: Optional[ReleaseSignoff]) -> Dict[str, Any]:
    progress = cy.summarize(states)
    remaining = sum(1 for s in states if cy.is_remaining(s))
    return {
        "id": cycle.id, "project_id": cycle.project_id, "name": cycle.name,
        "release_label": cycle.release_label, "git_commit": cycle.git_commit,
        "description": cycle.description, "status": cycle.status,
        "start_date": cycle.start_date, "due_date": cycle.due_date,
        "overdue": bool(cycle.status == cy.OPEN and cycle.due_date and remaining
                        and cycle.due_date < datetime.utcnow().date()),
        "created_by": _person(names, cycle.created_by_id), "created_at": cycle.created_at,
        "closed_by": _person(names, cycle.closed_by_id), "closed_at": cycle.closed_at,
        "progress": progress,
        "manual_cases": sum(1 for k in kinds if k == "manual"),
        "automated_cases": sum(1 for k in kinds if k == "automated"),
        "signoff": None if signoff is None else {
            "id": signoff.id, "verdict": signoff.verdict, "note": signoff.note,
            "signed_by": _person(names, signoff.signed_by_id), "signed_at": signoff.signed_at},
    }


async def evidence_run_ids(session: AsyncSession, cycle: TestCycle) -> List[int]:
    """Every run holding a counted result of a case of the cycle (the newest
    per browser/device, for each case), newest first — what a sign-off of the
    cycle covers. Never truncated: the sign-off's checks (runs finished,
    separate approver) must see all of them."""
    items = (await _items(session, [cycle.id])).get(cycle.id, [])
    case_ids = [row[0].test_case_id for row in items]
    evidence = (await _evidence(session, cycle.project_id, [cycle.id], case_ids)).get(cycle.id, {})
    run_ids: Set[int] = set()
    for cid in case_ids:
        run_ids.update(cy.classify(evidence.get(cid, []))["run_ids"])
    return sorted(run_ids, reverse=True)


async def _detail(session: AsyncSession, principal: AuthPrincipal, cycle: TestCycle) -> Dict[str, Any]:
    rows = (await _items(session, [cycle.id])).get(cycle.id, [])
    case_ids = [r[0].test_case_id for r in rows]
    evidence = (await _evidence(session, cycle.project_id, [cycle.id], case_ids)).get(cycle.id, {})
    parents = {sid: pid for sid, pid in (await session.exec(
        select(TestSuite.id, TestSuite.parent_id)
        .where(TestSuite.project_id == cycle.project_id))).all()}
    roots = cy.root_suites(parents)

    runs = list((await session.exec(
        select(TestRun).where(TestRun.cycle_id == cycle.id, TestRun.project_id == cycle.project_id,
                              counted())
        .order_by(TestRun.created_at.desc(), TestRun.id.desc()).limit(MAX_RUNS_SHOWN))).all())
    runs_total = (await session.exec(
        select(func.count()).select_from(TestRun)
        .where(TestRun.cycle_id == cycle.id, TestRun.project_id == cycle.project_id,
               counted()))).one()
    signoff = (await _latest_signoffs(session, cycle.project_id, [cycle.id])).get(cycle.id)

    classified = {cid: cy.classify(evidence.get(cid, [])) for cid in case_ids}
    names = await _names(session, [cycle.created_by_id, cycle.closed_by_id,
                                   signoff.signed_by_id if signoff else None]
                         + [r[0].assignee_id for r in rows]
                         + [c["tester_id"] for c in classified.values()]
                         + [r.user_id for r in runs])
    items: List[Dict[str, Any]] = []
    for item, name, executor, priority, suite_id, suite_name in rows:
        c = classified[item.test_case_id]
        items.append({
            "case_id": item.test_case_id, "name": name, "kind": _kind(executor),
            "priority": priority, "suite_id": suite_id, "suite_name": suite_name,
            "root_suite_id": roots.get(suite_id, suite_id),
            "assignee": _person(names, item.assignee_id), "assignee_id": item.assignee_id,
            "status": c["status"], "run_id": c["run_id"], "result_id": c["result_id"],
            "tested_at": c["at"], "tested_by": _person(names, c["tester_id"]),
            "configurations": c["configurations"],
        })
    assignees = [{**g, "assignee": _person(names, g["assignee_id"])} for g in cy.by_assignee(items)]
    can_edit = principal.api_key is None and await access_service.has_project_access(
        principal.user.id, cycle.project_id, session, min_role="editor")
    return {
        **_cycle_out(cycle, [i["status"] for i in items], [i["kind"] for i in items], names, signoff),
        "items": items, "assignees": assignees, "runs_total": runs_total, "can_edit": can_edit,
        "runs": [{
            "id": r.id, "kind": _kind(r.executor), "status": getattr(r.status, "value", r.status),
            "live": r.status in _LIVE, "suite_name": r.suite_name, "browser": r.browser,
            "device": r.device, "total_tests": r.total_tests, "passed_tests": r.passed_tests,
            "failed_tests": r.failed_tests, "created_at": r.created_at,
            "started_by": _person(names, r.user_id)} for r in runs],
    }


# ---------------------------------------------------------------------------
# Choosing cases and people
# ---------------------------------------------------------------------------

async def _cases_in_project(session: AsyncSession, project_id: int, case_ids: Optional[Sequence[int]],
                            suite_id: Optional[int]) -> List[int]:
    """Case ids to add, in the order asked for. Anything not in this project
    is refused as "not found" — whether the id exists elsewhere is not the
    caller's business."""
    wanted: List[int] = []
    if suite_id is not None:
        suite = await session.get(TestSuite, suite_id)
        if not suite or suite.project_id != project_id:
            raise HTTPException(status_code=400, detail="Suite not found in this project")
        from app.services.test_service import test_service
        in_tree = await test_service.subtree_cases(suite.id, session)
        wanted.extend(c.id for c in sorted(in_tree, key=lambda c: (c.test_suite_id or 0, c.id)))
    ids = list(dict.fromkeys(case_ids or []))
    if ids:
        found = set((await session.exec(
            select(TestCase.id).join(TestSuite, TestSuite.id == TestCase.test_suite_id)
            .where(TestCase.id.in_(ids), TestSuite.project_id == project_id))).all())
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"Cases not found in this project: {missing[:10]}")
        wanted.extend(ids)
    return list(dict.fromkeys(wanted))


async def _assignable(session: AsyncSession, project_id: int, user_id: int) -> User:
    """One answer for every "can't" (no such user, deactivated, no editor
    access), so this is not a way to test which accounts exist."""
    user = await session.get(User, user_id)
    if (user is None or not user.is_active
            or not await access_service.has_project_access(user.id, project_id, session,
                                                           min_role="editor")):
        raise HTTPException(status_code=400, detail=(
            "That person cannot be assigned: they need to be an active member who can edit "
            "tests in this project"))
    return user


async def _add_items(session: AsyncSession, cycle: TestCycle, case_ids: Sequence[int],
                     assignee_id: Optional[int], added_by: int) -> int:
    existing = set((await session.exec(
        select(TestCycleItem.test_case_id).where(TestCycleItem.cycle_id == cycle.id))).all())
    new = [cid for cid in case_ids if cid not in existing]
    if len(existing) + len(new) > cy.MAX_ITEMS:
        raise HTTPException(status_code=400, detail=(
            f"A cycle holds at most {cy.MAX_ITEMS} cases — split the release into several cycles"))
    for cid in new:
        session.add(TestCycleItem(cycle_id=cycle.id, project_id=cycle.project_id,
                                  test_case_id=cid, assignee_id=assignee_id, added_by_id=added_by))
    return len(new)


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

class CycleCreate(BaseModel):
    name: str = Field(max_length=cy.MAX_NAME)
    release_label: Optional[str] = Field(default=None, max_length=mt.MAX_RELEASE_LABEL)
    git_commit: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=cy.MAX_DESCRIPTION)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    case_ids: Optional[List[int]] = Field(default=None, max_length=cy.MAX_ITEMS)
    # Every case in this suite and the suites under it.
    suite_id: Optional[int] = None


class CyclePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=cy.MAX_NAME)
    release_label: Optional[str] = Field(default=None, max_length=mt.MAX_RELEASE_LABEL)
    git_commit: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=cy.MAX_DESCRIPTION)
    start_date: Optional[date] = None
    due_date: Optional[date] = None


def _text(value: Optional[str]) -> Optional[str]:
    return (value or "").strip() or None


@router.get("/projects/{project_id}/test-cycles", response_model=List[CycleOut])
async def list_cycles(project_id: int, status: Optional[str] = None,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_access(session, principal, project_id)
    stmt = select(TestCycle).where(TestCycle.project_id == project_id)
    if status in (cy.OPEN, cy.CLOSED):
        stmt = stmt.where(TestCycle.status == status)
    cycles = list((await session.exec(
        stmt.order_by(TestCycle.status.desc(), TestCycle.created_at.desc(), TestCycle.id.desc())
        .limit(200))).all())
    ids = [c.id for c in cycles]
    items = await _items(session, ids)
    evidence = await _evidence(session, project_id, ids)
    signoffs = await _latest_signoffs(session, project_id, ids)
    names = await _names(session, [u for c in cycles for u in (c.created_by_id, c.closed_by_id)]
                         + [s.signed_by_id for s in signoffs.values()])
    out = []
    for c in cycles:
        rows = items.get(c.id, [])
        ev = evidence.get(c.id, {})
        states = [cy.classify(ev.get(r[0].test_case_id, []))["status"] for r in rows]
        out.append(_cycle_out(c, states, [_kind(r[2]) for r in rows], names, signoffs.get(c.id)))
    return out


@router.post("/projects/{project_id}/test-cycles", response_model=CycleDetail)
async def create_cycle(project_id: int, body: CycleCreate, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_access(session, principal, project_id, "editor")
    try:
        name = cy.clean_name(body.name)
        cy.check_dates(body.start_date, body.due_date)
    except cy.CycleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    count = (await session.exec(select(func.count()).select_from(TestCycle)
                                .where(TestCycle.project_id == project.id))).one()
    if count >= cy.MAX_CYCLES_PER_PROJECT:
        raise HTTPException(status_code=400, detail=(
            f"This project already has {cy.MAX_CYCLES_PER_PROJECT} test cycles — delete old ones first"))
    case_ids = await _cases_in_project(session, project.id, body.case_ids, body.suite_id)
    cycle = TestCycle(project_id=project.id, name=name, release_label=_text(body.release_label),
                      git_commit=_text(body.git_commit), description=_text(body.description),
                      start_date=body.start_date, due_date=body.due_date,
                      created_by_id=principal.user.id)
    session.add(cycle)
    await session.flush()
    added = await _add_items(session, cycle, case_ids, None, principal.user.id)
    await _audit(session, cycle.id, cycle.project_id, "create", principal, request,
                           changes={"project_id": project.id, "name": name, "cases": added,
                                "release_label": cycle.release_label})
    await session.commit()
    return await _detail(session, principal, cycle)


@router.get("/test-cycles/assigned-to-me", response_model=List[MyCycle])
async def my_cycles(session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    """Open cycles in which the caller has cases assigned, with how many are
    still to do. Project access is re-checked: losing it hides the cycle."""
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="Assignments belong to a signed-in person, not an API key.")
    me = principal.user.id
    rows = (await session.exec(
        select(TestCycle, TestCycleItem.test_case_id)
        .join(TestCycleItem, TestCycleItem.cycle_id == TestCycle.id)
        .where(TestCycleItem.assignee_id == me, TestCycle.status == cy.OPEN)
        .order_by(TestCycle.due_date, TestCycle.id).limit(5000))).all()
    mine: Dict[int, List[int]] = defaultdict(list)
    cycles: Dict[int, TestCycle] = {}
    for cycle, case_id in rows:
        cycles[cycle.id] = cycle
        mine[cycle.id].append(case_id)
    projects: Dict[int, Optional[Project]] = {}
    out: List[Dict[str, Any]] = []
    for cid, cycle in cycles.items():
        if cycle.project_id not in projects:
            ok = await access_service.has_project_access(me, cycle.project_id, session)
            projects[cycle.project_id] = await session.get(Project, cycle.project_id) if ok else None
        project = projects[cycle.project_id]
        if project is None:
            continue
        ev = (await _evidence(session, cycle.project_id, [cid], mine[cid])).get(cid, {})
        remaining = sum(1 for case_id in mine[cid]
                        if cy.is_remaining(cy.classify(ev.get(case_id, []))["status"]))
        out.append({"cycle_id": cid, "cycle_name": cycle.name, "project_id": project.id,
                    "project_name": project.name, "due_date": cycle.due_date,
                    "assigned": len(mine[cid]), "remaining": remaining})
    return out


@router.get("/test-cycles/{cycle_id}", response_model=CycleDetail)
async def get_cycle(cycle_id: int, session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    cycle = await _cycle(session, principal, cycle_id)
    return await _detail(session, principal, cycle)


@router.patch("/test-cycles/{cycle_id}", response_model=CycleDetail)
async def update_cycle(cycle_id: int, body: CyclePatch, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    cycle = await _cycle(session, principal, cycle_id, "editor")
    _open(cycle)
    sent = body.model_fields_set
    changes: Dict[str, Any] = {}
    try:
        if "name" in sent:
            changes["name"] = cy.clean_name(body.name)
        for field in ("release_label", "git_commit", "description"):
            if field in sent:
                changes[field] = _text(getattr(body, field))
        for field in ("start_date", "due_date"):
            if field in sent:
                changes[field] = getattr(body, field)
        cy.check_dates(changes.get("start_date", cycle.start_date),
                       changes.get("due_date", cycle.due_date))
    except cy.CycleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    changed = {k: v for k, v in changes.items() if getattr(cycle, k) != v}
    if changed:
        for k, v in changed.items():
            setattr(cycle, k, v)
        cycle.updated_at = datetime.utcnow()
        session.add(cycle)
        await _audit(session, cycle.id, cycle.project_id, "update", principal, request,
                           changes={k: ("changed" if k == "description" else
                                        v.isoformat() if isinstance(v, date) else v)
                                    for k, v in changed.items()})
        await session.commit()
    return await _detail(session, principal, cycle)


async def _set_status(cycle_id: int, status: str, request: Request, session: AsyncSession,
                      principal: AuthPrincipal) -> Dict[str, Any]:
    cycle = await _cycle(session, principal, cycle_id, "editor")
    if cycle.status == status:
        raise HTTPException(status_code=409, detail=f"This test cycle is already {status}")
    now = datetime.utcnow()
    cycle.status = status
    cycle.updated_at = now
    cycle.closed_at = now if status == cy.CLOSED else None
    cycle.closed_by_id = principal.user.id if status == cy.CLOSED else None
    session.add(cycle)
    await _audit(session, cycle.id, cycle.project_id,
                 "close" if status == cy.CLOSED else "reopen", principal, request,
                           changes={"name": cycle.name})
    await session.commit()
    return await _detail(session, principal, cycle)


@router.post("/test-cycles/{cycle_id}/close", response_model=CycleDetail)
async def close_cycle(cycle_id: int, request: Request, session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    return await _set_status(cycle_id, cy.CLOSED, request, session, principal)


@router.post("/test-cycles/{cycle_id}/reopen", response_model=CycleDetail)
async def reopen_cycle(cycle_id: int, request: Request, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    return await _set_status(cycle_id, cy.OPEN, request, session, principal)


@router.delete("/test-cycles/{cycle_id}")
async def delete_cycle(cycle_id: int, request: Request, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    """Removes the cycle and its case list. Its runs and their results stay
    (unlinked), and so do sign-offs made from it — they are evidence."""
    cycle = await _cycle(session, principal, cycle_id, "admin")
    name, project_id = cycle.name, cycle.project_id
    await session.exec(sa_delete(TestCycleItem).where(TestCycleItem.cycle_id == cycle.id))
    await session.exec(sa_update(TestRun).where(TestRun.cycle_id == cycle.id).values(cycle_id=None))
    await session.delete(cycle)
    await _audit(session, cycle_id, project_id, "delete", principal, request,
                           changes={"name": name, "project_id": project_id})
    await session.commit()
    return {"deleted": cycle_id}


# ---------------------------------------------------------------------------
# Cases and assignees
# ---------------------------------------------------------------------------

class ItemsAdd(BaseModel):
    case_ids: Optional[List[int]] = Field(default=None, max_length=cy.MAX_ITEMS)
    suite_id: Optional[int] = None
    assignee_user_id: Optional[int] = None


class ItemsRemove(BaseModel):
    case_ids: List[int] = Field(min_length=1, max_length=cy.MAX_ITEMS)


class Assign(BaseModel):
    case_ids: List[int] = Field(min_length=1, max_length=cy.MAX_ITEMS)
    # None clears the assignee.
    assignee_user_id: Optional[int] = None


@router.post("/test-cycles/{cycle_id}/items", response_model=CycleDetail)
async def add_items(cycle_id: int, body: ItemsAdd, request: Request,
                    session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    cycle = await _cycle(session, principal, cycle_id, "editor")
    _open(cycle)
    if not body.case_ids and body.suite_id is None:
        raise HTTPException(status_code=400, detail="Give case_ids or a suite_id")
    case_ids = await _cases_in_project(session, cycle.project_id, body.case_ids, body.suite_id)
    if body.assignee_user_id is not None:
        await _assignable(session, cycle.project_id, body.assignee_user_id)
    added = await _add_items(session, cycle, case_ids, body.assignee_user_id, principal.user.id)
    if added:
        await _audit(session, cycle.id, cycle.project_id, "items_added", principal, request,
                           changes={"cases": added, "suite_id": body.suite_id,
                                    "assignee_user_id": body.assignee_user_id})
        await session.commit()
    return await _detail(session, principal, cycle)


@router.post("/test-cycles/{cycle_id}/items/remove", response_model=CycleDetail)
async def remove_items(cycle_id: int, body: ItemsRemove, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    cycle = await _cycle(session, principal, cycle_id, "editor")
    _open(cycle)
    result = await session.exec(sa_delete(TestCycleItem).where(
        TestCycleItem.cycle_id == cycle.id, TestCycleItem.test_case_id.in_(body.case_ids)))
    if result.rowcount:
        await _audit(session, cycle.id, cycle.project_id, "items_removed", principal, request,
                           changes={"cases": result.rowcount})
        await session.commit()
    return await _detail(session, principal, cycle)


@router.put("/test-cycles/{cycle_id}/assignments", response_model=CycleDetail)
async def assign(cycle_id: int, body: Assign, request: Request,
                 session: AsyncSession = Depends(get_session),
                 principal: AuthPrincipal = Depends(get_current_principal)):
    cycle = await _cycle(session, principal, cycle_id, "editor")
    _open(cycle)
    if body.assignee_user_id is not None:
        await _assignable(session, cycle.project_id, body.assignee_user_id)
    result = await session.exec(
        sa_update(TestCycleItem)
        .where(TestCycleItem.cycle_id == cycle.id, TestCycleItem.test_case_id.in_(body.case_ids))
        .values(assignee_id=body.assignee_user_id))
    if result.rowcount:
        await _audit(session, cycle.id, cycle.project_id, "assigned", principal, request,
                           changes={"cases": result.rowcount,
                                    "assignee_user_id": body.assignee_user_id})
        await session.commit()
    return await _detail(session, principal, cycle)


# ---------------------------------------------------------------------------
# Manual testing for a cycle
# ---------------------------------------------------------------------------

class ManualStart(BaseModel):
    # "remaining": cases that have not passed in this cycle; "all": every manual case.
    scope: str = "remaining"
    # Only the cases assigned to the caller.
    mine_only: bool = False


class StartedRun(BaseModel):
    id: int
    suite_name: Optional[str] = None
    cases: int


class ManualStarted(BaseModel):
    runs: List[StartedRun]
    # Cases left out because an open manual run of this cycle already holds them.
    already_open: int = 0
    open_run_ids: List[int] = []


@router.post("/test-cycles/{cycle_id}/manual-runs", response_model=ManualStarted)
async def start_manual(cycle_id: int, request: Request, body: ManualStart = ManualStart(),
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    """One manual run per top-level suite for the cycle's manual cases (a
    manual run belongs to a suite). Cases already waiting in an open manual
    run of this cycle are left where they are, so pressing the button twice
    does not hand the same case to two testers."""
    cycle = await _cycle(session, principal, cycle_id, "editor")
    _open(cycle)
    if body.scope not in ("remaining", "all"):
        raise HTTPException(status_code=400, detail="scope must be remaining or all")
    rows = (await session.exec(
        select(TestCycleItem, TestCase)
        .join(TestCase, TestCase.id == TestCycleItem.test_case_id)
        .where(TestCycleItem.cycle_id == cycle.id))).all()
    manual = [(item, case) for item, case in rows if mt.is_manual_case(case)]
    if not manual:
        raise HTTPException(status_code=400, detail="This cycle has no manual test cases")
    chosen = [(i, c) for i, c in manual
              if not body.mine_only or i.assignee_id == principal.user.id]
    if body.scope == "remaining":
        ev = (await _evidence(session, cycle.project_id, [cycle.id],
                              [c.id for _, c in chosen])).get(cycle.id, {})
        chosen = [(i, c) for i, c in chosen
                  if cy.is_remaining(cy.classify(ev.get(c.id, []))["status"])]

    waiting = (await session.exec(
        select(TestCaseResult.test_case_id, TestRun.id)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.cycle_id == cycle.id, TestRun.project_id == cycle.project_id,
               TestRun.executor == ExecutorType.MANUAL.value, TestRun.status.in_(_LIVE),
               TestCaseResult.status == TestStatus.PENDING,
               TestCaseResult.result_payload["outcome"].as_string().is_(None)))).all()
    held = {case_id: run_id for case_id, run_id in waiting}
    already = [c.id for _, c in chosen if c.id in held]
    chosen = [(i, c) for i, c in chosen if c.id not in held]
    open_run_ids = sorted({held[cid] for cid in already})
    if not chosen:
        if already:
            return {"runs": [], "already_open": len(already), "open_run_ids": open_run_ids}
        raise HTTPException(status_code=409, detail=(
            "Nothing to test: no manual case assigned to you is left in this cycle"
            if body.mine_only else "Nothing to test: every manual case in this cycle has passed"))

    suites = {s.id: s for s in (await session.exec(
        select(TestSuite).where(TestSuite.project_id == cycle.project_id))).all()}
    roots = cy.root_suites({sid: s.parent_id for sid, s in suites.items()})
    groups: Dict[int, List[TestCase]] = defaultdict(list)
    for _, case in chosen:
        root = roots.get(case.test_suite_id)
        if root is not None:
            groups[root].append(case)
    totals: Dict[int, int] = defaultdict(int)
    for _, case in manual:
        totals[roots.get(case.test_suite_id)] += 1
    started = []
    for root_id in sorted(groups):
        cases = sorted(groups[root_id], key=lambda c: (c.test_suite_id or 0, c.id))
        run = await create_manual_run(
            session, principal, request, suites[root_id], cases, targeted=True,
            selection_total=totals[root_id], release_label=cycle.release_label or cycle.name,
            git_commit=cycle.git_commit, cycle_id=cycle.id)
        started.append({"id": run.id, "suite_name": run.suite_name, "cases": len(cases)})
    await session.commit()
    return {"runs": started, "already_open": len(already), "open_run_ids": open_run_ids}
