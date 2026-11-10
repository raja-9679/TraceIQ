"""Working on a failure together: comments, assignment, notifications.

    GET    /api/runs/{run}/results/{result}/discussion          (viewer)
    POST   /api/runs/{run}/results/{result}/comments            (editor)  {body}
    DELETE /api/runs/{run}/results/{result}/comments/{id}       (author, or project admin)
    PUT    /api/runs/{run}/results/{result}/assignment          (editor)  {assignee_user_id}
    DELETE /api/runs/{run}/results/{result}/assignment          (editor)
    POST   /api/runs/{run}/results/{result}/assignment/done     (editor or the assignee)  {done}
    GET    /api/runs/{run}/discussion                           (viewer)  per-result counts for the list
    GET    /api/projects/{id}/assignee-candidates               (viewer)
    GET    /api/assignments/mine                                (signed-in person)
    GET    /api/notifications            ?limit=                (signed-in person)
    GET    /api/notifications/unread-count
    POST   /api/notifications/read                              {ids?, all?}

Rules worth knowing before changing anything here:

* **Comments are plain text** (control characters stripped, 4000 max) and are
  rendered escaped. The audit trail records who commented where and how long
  the comment was, never the text.
* **An assignment carries forward on read.** When the same test fails again
  in a later run, that result shows the newest OPEN assignment for the test
  (`carried`), so it does not look unowned. Nothing is copied.
* **A notification stores no text and no test name** — a kind, an actor and
  ids. Names are looked up when the list is read, only for projects the
  reader can still see, so losing access hides the notification with its
  subject. One unread notification per (person, result, kind): a busy thread
  does not fill the bell.
* **Notifications are a person's own**: API keys are refused, and there is no
  way to read anyone else's.
* People are only notified about, and can only be assigned, results in
  projects they can open.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.api.reliability_reports import require_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import (
    Notification, Project, ResultAssignment, ResultComment, TestCaseResult, TestRun,
    TestStatus, TestSuite, User, UserWorkspace,
)
from app.services import audit
from app.services.access_service import access_service

router = APIRouter()

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_COMMENT = 4000
MAX_THREAD = 500
KINDS = ("assigned", "comment", "assignment_done")


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

class Person(BaseModel):
    id: int
    name: str


class CommentOut(BaseModel):
    id: int
    body: str
    created_at: datetime
    author: Optional[Person] = None
    can_delete: bool = False


class AssignmentOut(BaseModel):
    assignee: Person
    assigned_by: Optional[Person] = None
    status: str                      # open | done
    created_at: datetime
    done_at: Optional[datetime] = None
    #: Where the assignment was made. Differs from the result being looked at
    #: when it is carried forward from an earlier failure of the same test.
    run_id: int
    result_id: int
    carried: bool = False


class Discussion(BaseModel):
    comments: List[CommentOut]
    assignment: Optional[AssignmentOut] = None
    can_write: bool


class ResultSummary(BaseModel):
    comments: int = 0
    assignee: Optional[Person] = None
    status: Optional[str] = None
    carried: bool = False


class RunDiscussion(BaseModel):
    results: Dict[int, ResultSummary]


class CommentCreate(BaseModel):
    body: str = PydField(min_length=1, max_length=20000)


class AssignRequest(BaseModel):
    assignee_user_id: int


class DoneRequest(BaseModel):
    done: bool = True


class NotificationOut(BaseModel):
    id: int
    kind: str
    created_at: datetime
    read: bool
    actor: Optional[Person] = None
    project_id: int
    project_name: str
    run_id: int
    result_id: int
    test_name: str


class NotificationList(BaseModel):
    items: List[NotificationOut]
    unread: int


class ReadRequest(BaseModel):
    ids: List[int] = PydField(default_factory=list, max_length=200)
    all: bool = False


class MyAssignment(BaseModel):
    project_id: int
    project_name: str
    run_id: int
    result_id: int
    test_name: str
    status: str
    assigned_by: Optional[Person] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_body(text: str) -> str:
    """Plain text: control characters out, nothing interpreted."""
    return _CONTROL_RE.sub("", text).strip()[:MAX_COMMENT]


def _person(user: Optional[User]) -> Optional[Person]:
    if user is None:
        return None
    return Person(id=user.id, name=user.full_name or user.email)


async def _users(ids: Iterable[Optional[int]], session: AsyncSession) -> Dict[int, User]:
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    return {u.id: u for u in (await session.exec(select(User).where(User.id.in_(wanted)))).all()}


async def _project_id_of(run: TestRun, session: AsyncSession) -> Optional[int]:
    if run.project_id:
        return run.project_id
    if run.test_suite_id:
        suite = await session.get(TestSuite, run.test_suite_id)
        return suite.project_id if suite else None
    return None


async def _result(run_id: int, result_id: int, principal: AuthPrincipal, session: AsyncSession,
                  min_role: str = "viewer"):
    """The run, its result and their project, after the access check."""
    run = await session.get(TestRun, run_id)
    project_id = await _project_id_of(run, session) if run else None
    if not run or not project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    project = await require_project(project_id, principal, session, min_role)
    result = await session.get(TestCaseResult, result_id)
    if not result or result.test_run_id != run.id:
        raise HTTPException(status_code=404, detail="Result not found in this run")
    return run, result, project


async def _can(principal: AuthPrincipal, project_id: int, session: AsyncSession, role: str) -> bool:
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        return False
    return await access_service.has_project_access(principal.user.id, project_id, session, min_role=role)


async def _assignment_for(result: TestCaseResult, project_id: int, session: AsyncSession):
    """(assignment, carried): this result's own, else the newest OPEN one for
    the same test in this project."""
    own = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.result_id == result.id,
        ResultAssignment.project_id == project_id))).first()
    if own is not None:
        return own, False
    # Only a result that did not pass inherits one: "Sam is on it" under a
    # green result would be noise.
    if result.test_case_id is None or result.status == TestStatus.PASSED:
        return None, False
    carried = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.project_id == project_id,
        ResultAssignment.test_case_id == result.test_case_id,
        ResultAssignment.status == "open",
        ResultAssignment.result_id != result.id,
    ).order_by(ResultAssignment.id.desc()).limit(1))).first()
    return carried, carried is not None


def _assignment_out(a: ResultAssignment, users: Dict[int, User], carried: bool) -> Optional[AssignmentOut]:
    assignee = _person(users.get(a.assignee_id))
    if assignee is None:
        return None
    return AssignmentOut(
        assignee=assignee, assigned_by=_person(users.get(a.assigned_by_id)),
        status=a.status, created_at=a.created_at, done_at=a.done_at,
        run_id=a.test_run_id, result_id=a.result_id, carried=carried)


async def _notify(session: AsyncSession, *, user_ids: Iterable[int], kind: str, actor_id: int,
                  project_id: int, run_id: int, result_id: int) -> int:
    """Queue a notification for each person who is not the actor, is active,
    and can open the project. One UNREAD row per (person, result, kind)."""
    targets = sorted({u for u in user_ids if u and u != actor_id})
    if not targets:
        return 0
    users = await _users(targets, session)
    already = set((await session.exec(select(Notification.user_id).where(
        Notification.user_id.in_(targets), Notification.result_id == result_id,
        Notification.kind == kind, Notification.read_at.is_(None)))).all())
    made = 0
    for uid in targets:
        user = users.get(uid)
        if user is None or not user.is_active or uid in already:
            continue
        if not await access_service.has_project_access(uid, project_id, session):
            continue
        session.add(Notification(user_id=uid, project_id=project_id, kind=kind, actor_id=actor_id,
                                 run_id=run_id, result_id=result_id))
        made += 1
    return made


def _human(principal: AuthPrincipal) -> User:
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="Notifications belong to a signed-in person, not an API key.")
    return principal.user


# ---------------------------------------------------------------------------
# Discussion on a result
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/results/{result_id}/discussion", response_model=Discussion)
async def get_discussion(run_id: int, result_id: int,
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)) -> Discussion:
    run, result, project = await _result(run_id, result_id, principal, session)
    rows = (await session.exec(select(ResultComment).where(
        ResultComment.result_id == result.id, ResultComment.project_id == project.id,
    ).order_by(ResultComment.id).limit(MAX_THREAD))).all()
    assignment, carried = await _assignment_for(result, project.id, session)
    users = await _users(
        [r.author_id for r in rows]
        + ([assignment.assignee_id, assignment.assigned_by_id] if assignment else []), session)
    is_admin = await _can(principal, project.id, session, "admin")
    return Discussion(
        comments=[CommentOut(id=r.id, body=r.body, created_at=r.created_at,
                             author=_person(users.get(r.author_id)),
                             can_delete=is_admin or r.author_id == principal.user.id)
                  for r in rows],
        assignment=_assignment_out(assignment, users, carried) if assignment else None,
        can_write=await _can(principal, project.id, session, "editor"))


@router.post("/runs/{run_id}/results/{result_id}/comments", response_model=CommentOut)
@limiter.limit("60/minute")
async def add_comment(request: Request, run_id: int, result_id: int, body: CommentCreate,
                      principal: AuthPrincipal = Depends(get_current_principal),
                      session: AsyncSession = Depends(get_session)) -> CommentOut:
    run, result, project = await _result(run_id, result_id, principal, session, "editor")
    text = clean_body(body.body)
    if not text:
        raise HTTPException(status_code=422, detail="Comment is empty")
    count = (await session.exec(select(func.count(ResultComment.id)).where(
        ResultComment.result_id == result.id))).one()
    if count >= MAX_THREAD:
        raise HTTPException(status_code=409, detail=f"This thread has reached {MAX_THREAD} comments.")

    # Who should hear about it: whoever is assigned (here, or carried from an
    # earlier failure of this test) and everyone already in the thread.
    earlier = (await session.exec(select(ResultComment.author_id).where(
        ResultComment.result_id == result.id))).all()
    assignment, _ = await _assignment_for(result, project.id, session)

    comment = ResultComment(project_id=project.id, test_run_id=run.id, result_id=result.id,
                            author_id=principal.user.id, body=text)
    session.add(comment)
    await session.flush()
    await _notify(session, user_ids=[*earlier, assignment.assignee_id if assignment else None],
                  kind="comment", actor_id=principal.user.id, project_id=project.id,
                  run_id=run.id, result_id=result.id)
    await audit.record(
        session, entity_type="result_comment", entity_id=comment.id, action="result_comment.create",
        principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
        # Where and how long — the text itself stays out of the audit trail.
        changes={"project_id": project.id, "run_id": run.id, "result_id": result.id, "length": len(text)})
    await session.commit()
    await session.refresh(comment)
    return CommentOut(id=comment.id, body=comment.body, created_at=comment.created_at,
                      author=_person(principal.user), can_delete=True)


@router.delete("/runs/{run_id}/results/{result_id}/comments/{comment_id}")
async def delete_comment(run_id: int, result_id: int, comment_id: int,
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)):
    run, result, project = await _result(run_id, result_id, principal, session)
    comment = await session.get(ResultComment, comment_id)
    if not comment or comment.result_id != result.id or comment.project_id != project.id:
        raise HTTPException(status_code=404, detail="Comment not found")
    mine = comment.author_id == principal.user.id and await _can(principal, project.id, session, "editor")
    if not mine and not await _can(principal, project.id, session, "admin"):
        raise HTTPException(status_code=403, detail="Only the author or a project admin can delete a comment.")
    await audit.record(
        session, entity_type="result_comment", entity_id=comment.id, action="result_comment.delete",
        principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
        changes={"project_id": project.id, "run_id": run.id, "result_id": result.id,
                 "author_id": comment.author_id})
    await session.delete(comment)
    await session.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

@router.put("/runs/{run_id}/results/{result_id}/assignment", response_model=AssignmentOut)
async def assign(run_id: int, result_id: int, body: AssignRequest,
                 principal: AuthPrincipal = Depends(get_current_principal),
                 session: AsyncSession = Depends(get_session)) -> AssignmentOut:
    run, result, project = await _result(run_id, result_id, principal, session, "editor")
    assignee = await session.get(User, body.assignee_user_id)
    # One answer for "no such person" and "not in this project": the caller
    # must not learn which accounts exist.
    if (assignee is None or not assignee.is_active
            or not await access_service.has_project_access(assignee.id, project.id, session, min_role="editor")):
        raise HTTPException(status_code=400, detail="Choose someone who can edit tests in this project.")

    row = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.result_id == result.id))).first()
    previous = row.assignee_id if row else None
    now = datetime.utcnow()
    if row is None:
        row = ResultAssignment(project_id=project.id, test_run_id=run.id, result_id=result.id,
                               test_case_id=result.test_case_id, assignee_id=assignee.id,
                               assigned_by_id=principal.user.id)
    else:
        row.assignee_id = assignee.id
        row.assigned_by_id = principal.user.id
        row.status = "open"
        row.done_at = None
        row.done_by_id = None
        row.updated_at = now
    session.add(row)
    if previous != assignee.id:
        await _notify(session, user_ids=[assignee.id], kind="assigned", actor_id=principal.user.id,
                      project_id=project.id, run_id=run.id, result_id=result.id)
    await audit.record(
        session, entity_type="result_assignment", entity_id=result.id, action="result_assignment.set",
        principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
        changes={"project_id": project.id, "run_id": run.id, "result_id": result.id,
                 "assignee_id": assignee.id, "previous_assignee_id": previous})
    await session.commit()
    await session.refresh(row)
    users = {assignee.id: assignee, principal.user.id: principal.user}
    return _assignment_out(row, users, False)


@router.delete("/runs/{run_id}/results/{result_id}/assignment")
async def unassign(run_id: int, result_id: int,
                   principal: AuthPrincipal = Depends(get_current_principal),
                   session: AsyncSession = Depends(get_session)):
    run, result, project = await _result(run_id, result_id, principal, session, "editor")
    row = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.result_id == result.id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="This result is not assigned here.")
    await audit.record(
        session, entity_type="result_assignment", entity_id=result.id, action="result_assignment.clear",
        principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
        changes={"project_id": project.id, "run_id": run.id, "result_id": result.id,
                 "assignee_id": row.assignee_id})
    await session.delete(row)
    await session.commit()
    return {"status": "unassigned"}


@router.post("/runs/{run_id}/results/{result_id}/assignment/done", response_model=AssignmentOut)
async def mark_done(run_id: int, result_id: int, body: DoneRequest,
                    principal: AuthPrincipal = Depends(get_current_principal),
                    session: AsyncSession = Depends(get_session)) -> AssignmentOut:
    run, result, project = await _result(run_id, result_id, principal, session)
    row = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.result_id == result.id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="This result is not assigned here.")
    # The assignee may close their own work even as a viewer-turned-assignee;
    # anyone else needs to be an editor. API keys never act as "the assignee".
    is_assignee = principal.api_key is None and row.assignee_id == principal.user.id
    if not is_assignee and not await _can(principal, project.id, session, "editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    changed = (row.status == "done") != body.done
    now = datetime.utcnow()
    row.status = "done" if body.done else "open"
    row.done_at = now if body.done else None
    row.done_by_id = principal.user.id if body.done else None
    row.updated_at = now
    session.add(row)
    if changed and body.done:
        await _notify(session, user_ids=[row.assigned_by_id], kind="assignment_done",
                      actor_id=principal.user.id, project_id=project.id, run_id=run.id, result_id=result.id)
    if changed:
        await audit.record(
            session, entity_type="result_assignment", entity_id=result.id,
            action="result_assignment.done" if body.done else "result_assignment.reopen",
            principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
            changes={"project_id": project.id, "run_id": run.id, "result_id": result.id,
                     "assignee_id": row.assignee_id})
    await session.commit()
    await session.refresh(row)
    users = await _users([row.assignee_id, row.assigned_by_id], session)
    return _assignment_out(row, users, False)


@router.get("/runs/{run_id}/discussion", response_model=RunDiscussion)
async def run_discussion(run_id: int,
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)) -> RunDiscussion:
    """Comment counts and who is assigned, per result — for the result list."""
    run = await session.get(TestRun, run_id)
    project_id = await _project_id_of(run, session) if run else None
    if not run or not project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    await require_project(project_id, principal, session)

    out: Dict[int, ResultSummary] = {}
    counts = (await session.exec(select(ResultComment.result_id, func.count(ResultComment.id)).where(
        ResultComment.test_run_id == run.id, ResultComment.project_id == project_id,
    ).group_by(ResultComment.result_id))).all()
    for result_id, n in counts:
        out[result_id] = ResultSummary(comments=n)

    own = (await session.exec(select(ResultAssignment).where(
        ResultAssignment.test_run_id == run.id, ResultAssignment.project_id == project_id))).all()
    own_by_result = {a.result_id: a for a in own}

    # Carried: results of this run with a test id and no assignment of their
    # own, whose test has an open assignment elsewhere in the project.
    results = (await session.exec(select(
        TestCaseResult.id, TestCaseResult.test_case_id, TestCaseResult.status,
    ).where(TestCaseResult.test_run_id == run.id))).all()
    need = {case_id for rid, case_id, status in results
            if case_id is not None and rid not in own_by_result and status != TestStatus.PASSED}
    carried_by_case: Dict[int, ResultAssignment] = {}
    if need:
        rows = (await session.exec(select(ResultAssignment).where(
            ResultAssignment.project_id == project_id, ResultAssignment.status == "open",
            ResultAssignment.test_case_id.in_(need), ResultAssignment.test_run_id != run.id,
        ).order_by(ResultAssignment.id))).all()
        for a in rows:           # ascending, so the newest wins
            carried_by_case[a.test_case_id] = a

    users = await _users([a.assignee_id for a in own] + [a.assignee_id for a in carried_by_case.values()], session)
    for rid, case_id, status in results:
        a = own_by_result.get(rid)
        carried = False
        if a is None and status != TestStatus.PASSED and case_id in carried_by_case:
            a, carried = carried_by_case[case_id], True
        if a is None:
            continue
        who = _person(users.get(a.assignee_id))
        if who is None:
            continue
        summary = out.setdefault(rid, ResultSummary())
        summary.assignee, summary.status, summary.carried = who, a.status, carried
    return RunDiscussion(results=out)


@router.get("/projects/{project_id}/assignee-candidates", response_model=List[Person])
async def assignee_candidates(project_id: int,
                              principal: AuthPrincipal = Depends(get_current_principal),
                              session: AsyncSession = Depends(get_session)) -> List[Person]:
    """Active workspace members who can edit tests in the project."""
    project = await require_project(project_id, principal, session)
    members = (await session.exec(
        select(User).join(UserWorkspace, UserWorkspace.user_id == User.id)
        .where(UserWorkspace.workspace_id == project.workspace_id, User.is_active == True)  # noqa: E712
        .order_by(User.full_name))).all()
    return [_person(u) for u in members
            if await access_service.has_project_access(u.id, project.id, session, min_role="editor")]


# ---------------------------------------------------------------------------
# A person's own lists
# ---------------------------------------------------------------------------

async def _visible_projects(user_id: int, project_ids: Set[int], session: AsyncSession) -> Dict[int, Project]:
    out: Dict[int, Project] = {}
    for pid in sorted(project_ids):
        if await access_service.has_project_access(user_id, pid, session):
            project = await session.get(Project, pid)
            if project is not None:
                out[pid] = project
    return out


@router.get("/assignments/mine", response_model=List[MyAssignment])
async def my_assignments(status: str = Query("open", pattern="^(open|done|all)$"),
                         principal: AuthPrincipal = Depends(get_current_principal),
                         session: AsyncSession = Depends(get_session)) -> List[MyAssignment]:
    user = _human(principal)
    q = select(ResultAssignment).where(ResultAssignment.assignee_id == user.id)
    if status != "all":
        q = q.where(ResultAssignment.status == status)
    rows = (await session.exec(q.order_by(ResultAssignment.id.desc()).limit(100))).all()
    projects = await _visible_projects(user.id, {r.project_id for r in rows}, session)
    rows = [r for r in rows if r.project_id in projects]
    names = dict((await session.exec(select(TestCaseResult.id, TestCaseResult.test_name).where(
        TestCaseResult.id.in_([r.result_id for r in rows] or [0])))).all())
    users = await _users([r.assigned_by_id for r in rows], session)
    return [MyAssignment(project_id=r.project_id, project_name=projects[r.project_id].name,
                         run_id=r.test_run_id, result_id=r.result_id, test_name=names[r.result_id],
                         status=r.status, assigned_by=_person(users.get(r.assigned_by_id)),
                         created_at=r.created_at)
            for r in rows if r.result_id in names]


async def _my_notifications(user: User, session: AsyncSession, limit: int):
    """Newest first, already narrowed to projects the person can still open
    and results that still exist."""
    rows = (await session.exec(select(Notification).where(Notification.user_id == user.id)
                               .order_by(Notification.id.desc()).limit(limit))).all()
    projects = await _visible_projects(user.id, {r.project_id for r in rows}, session)
    rows = [r for r in rows if r.project_id in projects]
    names = dict((await session.exec(select(TestCaseResult.id, TestCaseResult.test_name).where(
        TestCaseResult.id.in_([r.result_id for r in rows] or [0])))).all())
    return [r for r in rows if r.result_id in names], projects, names


@router.get("/notifications", response_model=NotificationList)
async def list_notifications(limit: int = Query(30, ge=1, le=100),
                             principal: AuthPrincipal = Depends(get_current_principal),
                             session: AsyncSession = Depends(get_session)) -> NotificationList:
    user = _human(principal)
    rows, projects, names = await _my_notifications(user, session, limit)
    users = await _users([r.actor_id for r in rows], session)
    return NotificationList(
        items=[NotificationOut(id=r.id, kind=r.kind, created_at=r.created_at, read=r.read_at is not None,
                               actor=_person(users.get(r.actor_id)), project_id=r.project_id,
                               project_name=projects[r.project_id].name, run_id=r.run_id,
                               result_id=r.result_id, test_name=names[r.result_id])
               for r in rows],
        unread=sum(1 for r in rows if r.read_at is None))


@router.get("/notifications/unread-count")
async def unread_count(principal: AuthPrincipal = Depends(get_current_principal),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, int]:
    """For the bell. Counts what the list would show (same access rule), over
    the newest 100."""
    user = _human(principal)
    rows, _, _ = await _my_notifications(user, session, 100)
    return {"unread": sum(1 for r in rows if r.read_at is None)}


@router.post("/notifications/read")
async def mark_read(body: ReadRequest,
                    principal: AuthPrincipal = Depends(get_current_principal),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, int]:
    user = _human(principal)
    if not body.all and not body.ids:
        return {"marked": 0}
    stmt = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if not body.all:
        stmt = stmt.where(Notification.id.in_(body.ids))
    result = await session.exec(stmt.values(read_at=datetime.utcnow()))
    await session.commit()
    return {"marked": result.rowcount or 0}
