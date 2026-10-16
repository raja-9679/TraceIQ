"""Manual runs and release sign-off (WP12d). Rules: app/services/manual_testing.py.

  POST /manual-runs                                      start a manual run (editor)
  GET  /manual-runs/{run_id}                             tester screen data (viewer)
  PUT  /manual-runs/{run_id}/results/{result_id}         record one case (editor)
  POST /manual-runs/{run_id}/results/{result_id}/attachments   screenshot (editor)
  POST /manual-runs/{run_id}/complete                    close the run (editor)
  GET  /projects/{project_id}/manual-runs                recent manual runs (viewer)
  GET  /projects/{project_id}/signoffs                   sign-off history (viewer)
  POST /projects/{project_id}/signoffs                   approve / reject a release (editor)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    ExecutorType, Project, ReleaseSignoff, RunTrigger, TestCase, TestCaseResult, TestRun,
    TestStatus, TestSuite, User, Workspace,
)
from app.services import manual_testing as mt
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()

_LIVE = (TestStatus.PENDING, TestStatus.RUNNING)


async def _project_access(session: AsyncSession, principal: AuthPrincipal, project_id: Optional[int],
                          min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id) if project_id else None
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None:
        from app.api.jobs import api_key_allows_project
        if not api_key_allows_project(principal.api_key, project.id):
            raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project.id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail=(
            "Editor access required" if min_role != "viewer" else "Access denied"))
    return project


async def _manual_run(session: AsyncSession, principal: AuthPrincipal, run_id: int,
                      min_role: str = "viewer") -> TestRun:
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await _project_access(session, principal, run.project_id, min_role)
    if getattr(run.executor, "value", run.executor) != ExecutorType.MANUAL.value:
        raise HTTPException(status_code=400, detail="Not a manual run")
    return run


def _steps_snapshot(case: TestCase) -> List[Dict[str, Any]]:
    out = []
    for s in case.steps or []:
        s = s if isinstance(s, dict) else s.model_dump()
        out.append({"instruction": s.get("value") or "",
                    "expected": ((s.get("params") or {}).get("expected") or "")})
    return out


def _result_view(r: TestCaseResult, names: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    # Tester names are looked up, never stored in the payload, so an erased
    # account stops being named here (app/services/erasure.py).
    p = r.result_payload or {}
    return {
        "tester_name": (names or {}).get(p.get("tester_id")),
        "id": r.id, "test_case_id": r.test_case_id, "test_name": r.test_name,
        "status": getattr(r.status, "value", r.status), "outcome": p.get("outcome"),
        "notes": p.get("notes"), "steps": p.get("steps") or [],
        "step_results": p.get("step_results") or [], "tester_id": p.get("tester_id"),
        "recorded_at": p.get("recorded_at"),
        "duration_ms": r.duration_ms, "attachments": p.get("attachments") or [],
    }


def _run_view(run: TestRun, results: List[TestCaseResult],
              names: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    outcomes = [(r.result_payload or {}).get("outcome") for r in results]
    return {
        "id": run.id, "project_id": run.project_id, "test_suite_id": run.test_suite_id,
        "suite_name": run.suite_name, "status": getattr(run.status, "value", run.status),
        "executor": "manual", "user_id": run.user_id, "created_at": run.created_at,
        "finalized_at": run.finalized_at, "git_commit": run.git_commit,
        "git_branch": run.git_branch, "release_label": (run.domain_settings or {}).get("release_label"),
        "summary": mt.summarize(outcomes),
        "results": [_result_view(r, names) for r in results],
    }


async def _view(session: AsyncSession, run: TestRun, results: List[TestCaseResult]) -> Dict[str, Any]:
    names = await _names(session, [run.user_id] + [
        (r.result_payload or {}).get("tester_id") for r in results])
    return {**_run_view(run, results, names), "user_name": names.get(run.user_id)}


async def _result_out(session: AsyncSession, r: TestCaseResult) -> Dict[str, Any]:
    return _result_view(r, await _names(session, [(r.result_payload or {}).get("tester_id")]))


async def _results(session: AsyncSession, run_id: int) -> List[TestCaseResult]:
    return list((await session.exec(
        select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)
        .order_by(TestCaseResult.id))).all())


# ---------------------------------------------------------------------------
# Manual runs
# ---------------------------------------------------------------------------


class ManualRunCreate(BaseModel):
    suite_id: int
    # Restrict to these manual cases (all must lie in the suite's subtree).
    case_ids: Optional[List[int]] = Field(default=None, max_length=500)
    release_label: Optional[str] = Field(default=None, max_length=mt.MAX_RELEASE_LABEL)
    git_commit: Optional[str] = Field(default=None, max_length=64)
    git_branch: Optional[str] = Field(default=None, max_length=255)


@router.post("/manual-runs")
async def start_manual_run(body: ManualRunCreate, request: Request,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    from app.services.test_service import test_service
    suite = await session.get(TestSuite, body.suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    await _project_access(session, principal, suite.project_id, "editor")

    in_tree = await test_service.subtree_cases(suite.id, session)
    manual = [c for c in in_tree if mt.is_manual_case(c)]
    if body.case_ids:
        wanted = list(dict.fromkeys(body.case_ids))
        by_id = {c.id: c for c in in_tree}
        outside = [cid for cid in wanted if cid not in by_id]
        if outside:
            raise HTTPException(status_code=400, detail=f"Cases not in this suite: {outside[:10]}")
        automated = [cid for cid in wanted if not mt.is_manual_case(by_id[cid])]
        if automated:
            raise HTTPException(status_code=400, detail=(
                f"Not manual cases: {automated[:10]} — automated cases run through POST /api/runs"))
        chosen = [by_id[cid] for cid in wanted]
    else:
        chosen = sorted(manual, key=lambda c: (c.test_suite_id or 0, c.id))
    if not chosen:
        raise HTTPException(status_code=400, detail="This suite has no manual test cases")

    single = chosen[0] if len(chosen) == 1 else None
    run = TestRun(
        test_suite_id=suite.id, project_id=suite.project_id, suite_name=suite.name,
        test_case_id=single.id if single and body.case_ids else None,
        test_case_name=single.name if single and body.case_ids else None,
        executor=ExecutorType.MANUAL, status=TestStatus.PENDING, browser="manual",
        total_tests=len(chosen), user_id=principal.user.id,
        triggered_by=RunTrigger.API_AGENT if principal.is_api_caller else RunTrigger.HUMAN,
        api_key_id=principal.api_key.id if principal.api_key else None,
        selection_mode="targeted" if body.case_ids else "full", selection_total=len(manual),
        git_commit=(body.git_commit or "").strip() or None,
        git_branch=(body.git_branch or "").strip() or None,
        # domain_settings is free-form run metadata the UI already shows; the
        # release label rides there rather than in a new column.
        domain_settings={"release_label": (body.release_label or "").strip()} if body.release_label else {},
    )
    session.add(run)
    await session.flush()
    for case in chosen:
        session.add(TestCaseResult(
            test_run_id=run.id, test_case_id=case.id, test_name=case.name,
            status=TestStatus.PENDING, duration_ms=0, result_kind=mt.RESULT_KIND,
            # Snapshot the steps: the case may be edited while (or after) it is
            # being tested, and the record must show what the tester followed.
            result_payload={"outcome": None, "steps": _steps_snapshot(case)}))
    await audit_record(session, entity_type="manual_run", entity_id=run.id, action="start",
                       principal=principal, request=request,
                       changes={"suite_id": suite.id, "cases": len(chosen),
                                "release_label": body.release_label})
    await session.commit()
    return await _view(session, run, await _results(session, run.id))


@router.get("/manual-runs/{run_id}")
async def get_manual_run(run_id: int, session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    run = await _manual_run(session, principal, run_id)
    return await _view(session, run, await _results(session, run.id))


class StepResultIn(BaseModel):
    outcome: Optional[str] = None
    note: Optional[str] = None


class ManualResultIn(BaseModel):
    outcome: str
    notes: Optional[str] = None
    step_results: Optional[List[StepResultIn]] = Field(default=None, max_length=mt.MAX_STEPS)
    # Time the tester spent on the case, if they want it recorded.
    duration_ms: Optional[int] = None


async def _open_result(session: AsyncSession, principal: AuthPrincipal, run_id: int,
                       result_id: int) -> tuple:
    run = await _manual_run(session, principal, run_id, "editor")
    if run.status not in _LIVE:
        raise HTTPException(status_code=409, detail="This manual run is completed")
    res = await session.get(TestCaseResult, result_id)
    if not res or res.test_run_id != run.id:
        raise HTTPException(status_code=404, detail="Result not found")
    return run, res


@router.put("/manual-runs/{run_id}/results/{result_id}")
async def record_manual_result(run_id: int, result_id: int, body: ManualResultIn, request: Request,
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    run, res = await _open_result(session, principal, run_id, result_id)
    payload = dict(res.result_payload or {})
    try:
        steps = mt.validate_record(body.outcome, body.notes,
                                   [s.model_dump() for s in body.step_results or []],
                                   len(payload.get("steps") or []), body.duration_ms)
    except mt.ManualTestingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user: User = principal.user
    payload.update({
        "outcome": body.outcome, "notes": (body.notes or "").strip() or None,
        "step_results": steps, "tester_id": user.id,
        "recorded_at": datetime.utcnow().isoformat(),
    })
    res.result_payload = payload
    flag_modified(res, "result_payload")
    res.status = TestStatus(mt.outcome_status(body.outcome))
    # Failure views read error_message; a tester's note is the failure text.
    res.error_message = (payload["notes"] or f"Marked {body.outcome} by tester")[:2000] \
        if body.outcome in ("failed", "blocked") else None
    if body.duration_ms is not None:
        res.duration_ms = float(body.duration_ms)
    session.add(res)
    await audit_record(session, entity_type="manual_run", entity_id=run.id, action="record",
                       principal=principal, request=request,
                       changes={"result_id": res.id, "case_id": res.test_case_id,
                                "outcome": body.outcome})
    await session.commit()
    return await _result_out(session, res)


@router.post("/manual-runs/{run_id}/results/{result_id}/attachments")
async def add_manual_attachment(run_id: int, result_id: int, request: Request,
                                file: UploadFile = File(...),
                                session: AsyncSession = Depends(get_session),
                                principal: AuthPrincipal = Depends(get_current_principal)):
    run, res = await _open_result(session, principal, run_id, result_id)
    payload = dict(res.result_payload or {})
    attachments = list(payload.get("attachments") or [])
    if len(attachments) >= mt.MAX_ATTACHMENTS_PER_RESULT:
        raise HTTPException(status_code=400, detail=(
            f"At most {mt.MAX_ATTACHMENTS_PER_RESULT} attachments per case"))
    data = await file.read(mt.MAX_ATTACHMENT_BYTES + 1)
    if len(data) > mt.MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail=(
            f"Attachment larger than {mt.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"))
    kind = mt.sniff_image(data[:16])
    if not kind:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG, GIF or WebP screenshots")
    ctype, ext = kind
    # Under the run's prefix: the artifact endpoint's access check, run
    # deletion, retention and the workspace purge all already cover runs/<id>/.
    key = f"runs/{run.id}/manual/{res.id}/{uuid.uuid4().hex}.{ext}"
    import io
    from app.core.storage import object_store
    try:
        object_store.upload_fileobj(io.BytesIO(data), key, content_type=ctype)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not store the attachment: {exc}")
    name = (file.filename or f"screenshot.{ext}").replace("/", "_")[:120]
    attachments.append({"key": key, "name": name, "content_type": ctype, "size": len(data),
                        "uploaded_by": principal.user.id,
                        "uploaded_at": datetime.utcnow().isoformat()})
    payload["attachments"] = attachments
    res.result_payload = payload
    flag_modified(res, "result_payload")
    # Also a screenshot of the result, so the ordinary run view shows it.
    res.screenshots = list(res.screenshots or []) + [key]
    flag_modified(res, "screenshots")
    session.add(res)
    await audit_record(session, entity_type="manual_run", entity_id=run.id, action="attach",
                       principal=principal, request=request,
                       changes={"result_id": res.id, "size": len(data), "content_type": ctype})
    await session.commit()
    return await _result_out(session, res)


class CompleteIn(BaseModel):
    # Record every case not yet recorded as skipped instead of refusing.
    skip_unrecorded: bool = False


@router.post("/manual-runs/{run_id}/complete")
async def complete_manual_run(run_id: int, request: Request, body: CompleteIn = CompleteIn(),
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    run = await _manual_run(session, principal, run_id, "editor")
    if run.status not in _LIVE:
        raise HTTPException(status_code=409, detail="This manual run is already completed")
    results = await _results(session, run.id)
    open_ = [r for r in results if not (r.result_payload or {}).get("outcome")]
    if open_ and not body.skip_unrecorded:
        raise HTTPException(status_code=409, detail=(
            f"{len(open_)} case(s) have no result yet — record them, or complete with "
            "skip_unrecorded to mark them skipped"))
    now = datetime.utcnow()
    for r in open_:
        p = dict(r.result_payload or {})
        p.update({"outcome": "skipped", "tester_id": principal.user.id,
                  "recorded_at": now.isoformat(), "auto_skipped": True})
        r.result_payload = p
        flag_modified(r, "result_payload")
        session.add(r)
    summary = mt.summarize([(r.result_payload or {}).get("outcome") for r in results])
    run.status = TestStatus(summary["status"])
    run.passed_tests = summary["passed"]
    run.failed_tests = summary["failed"]
    run.total_tests = len(results)
    run.duration_ms = float(sum(r.duration_ms or 0 for r in results))
    run.finalized_at = now
    session.add(run)
    await audit_record(session, entity_type="manual_run", entity_id=run.id, action="complete",
                       principal=principal, request=request,
                       changes={"status": summary["status"], **summary["counts"]})
    await session.commit()
    return await _view(session, run, results)


@router.get("/projects/{project_id}/manual-runs")
async def list_manual_runs(project_id: int, limit: int = 50,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_access(session, principal, project_id)
    runs = (await session.exec(
        select(TestRun).where(TestRun.project_id == project_id,
                              TestRun.executor == ExecutorType.MANUAL.value)
        .order_by(TestRun.created_at.desc()).limit(max(1, min(limit, 200))))).all()
    return [{
        "id": r.id, "suite_name": r.suite_name, "status": getattr(r.status, "value", r.status),
        "created_at": r.created_at, "finalized_at": r.finalized_at, "user_id": r.user_id,
        "total_tests": r.total_tests, "passed_tests": r.passed_tests,
        "failed_tests": r.failed_tests, "git_commit": r.git_commit,
        "release_label": (r.domain_settings or {}).get("release_label"),
    } for r in runs]


# ---------------------------------------------------------------------------
# Release sign-off
# ---------------------------------------------------------------------------


class SignoffIn(BaseModel):
    release_label: str = Field(min_length=1, max_length=mt.MAX_RELEASE_LABEL)
    run_ids: List[int] = Field(min_length=1, max_length=mt.MAX_SIGNOFF_RUNS)
    case_ids: Optional[List[int]] = Field(default=None, max_length=1000)
    verdict: str
    note: Optional[str] = Field(default=None, max_length=mt.MAX_NOTES)
    git_commit: Optional[str] = Field(default=None, max_length=64)


def _signoff_view(s: ReleaseSignoff, names: Dict[int, str]) -> Dict[str, Any]:
    return {"id": s.id, "project_id": s.project_id, "release_label": s.release_label,
            "git_commit": s.git_commit, "run_ids": s.run_ids or [], "case_ids": s.case_ids,
            "verdict": s.verdict, "note": s.note, "signed_by_id": s.signed_by_id,
            "signed_by_name": names.get(s.signed_by_id), "signed_at": s.signed_at}


async def _names(session: AsyncSession, ids) -> Dict[int, str]:
    ids = [i for i in set(ids) if isinstance(i, int)]
    if not ids:
        return {}
    rows = (await session.exec(select(User.id, User.full_name, User.email).where(User.id.in_(ids)))).all()
    return {r[0]: r[1] or r[2] for r in rows}


@router.get("/projects/{project_id}/signoffs")
async def list_signoffs(project_id: int, limit: int = 50,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_access(session, principal, project_id)
    rows = (await session.exec(
        select(ReleaseSignoff).where(ReleaseSignoff.project_id == project_id)
        .order_by(ReleaseSignoff.signed_at.desc(), ReleaseSignoff.id.desc())
        .limit(max(1, min(limit, 200))))).all()
    names = await _names(session, [r.signed_by_id for r in rows])
    return [_signoff_view(r, names) for r in rows]


@router.post("/projects/{project_id}/signoffs")
async def create_signoff(project_id: int, body: SignoffIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_access(session, principal, project_id, "editor")
    # A sign-off is a person's accountable decision; a service account
    # approving a release would make the record meaningless.
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="A release sign-off must be made by a person, not an API key")
    if body.verdict not in mt.VERDICTS:
        raise HTTPException(status_code=400, detail="verdict must be approve or reject")
    label = body.release_label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="release_label is required")

    run_ids = list(dict.fromkeys(body.run_ids))
    runs = list((await session.exec(select(TestRun).where(TestRun.id.in_(run_ids)))).all())
    # Runs of another project are refused as "not found", not "forbidden":
    # whether some id exists elsewhere is not this caller's business.
    found = {r.id for r in runs if r.project_id == project.id}
    missing = [rid for rid in run_ids if rid not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Runs not found in this project: {missing[:10]}")
    if body.verdict == "approve":
        live = [r.id for r in runs if r.status in _LIVE]
        if live:
            raise HTTPException(status_code=409, detail=(
                f"Runs still in progress: {live[:10]} — complete them before approving"))

    results = list((await session.exec(
        select(TestCaseResult).where(TestCaseResult.test_run_id.in_(run_ids)))).all())
    case_ids = None
    if body.case_ids:
        case_ids = list(dict.fromkeys(body.case_ids))
        covered = {r.test_case_id for r in results}
        outside = [c for c in case_ids if c not in covered]
        if outside:
            raise HTTPException(status_code=400, detail=f"Cases not in these runs: {outside[:10]}")

    from app.services.proposal_policy import separation_required
    workspace = await session.get(Workspace, project.workspace_id)
    required = separation_required(
        workspace_flag=bool(getattr(workspace, "require_separate_approver", False)))
    executors = mt.executor_ids(runs, results, set(case_ids) if case_ids else None)
    if mt.signoff_conflict(principal.user.id, executors, required):
        raise HTTPException(status_code=403, detail=(
            "Separation of duties is on for this workspace: you executed part of these "
            "manual runs, so someone else must sign off"))

    commit = (body.git_commit or "").strip() or None
    if commit is None:
        commits = {r.git_commit for r in runs if r.git_commit}
        commit = commits.pop() if len(commits) == 1 else None
    signoff = ReleaseSignoff(
        project_id=project.id, release_label=label, git_commit=commit, run_ids=run_ids,
        case_ids=case_ids, verdict=body.verdict, note=(body.note or "").strip() or None,
        signed_by_id=principal.user.id)
    session.add(signoff)
    await session.flush()
    await audit_record(session, entity_type="release_signoff", entity_id=signoff.id,
                       action=body.verdict, principal=principal, request=request,
                       workspace_id=project.workspace_id,
                       changes={"project_id": project.id, "release_label": label,
                                "run_ids": run_ids, "case_ids": case_ids, "git_commit": commit,
                                "separate_approver_required": required})
    await session.commit()
    await session.refresh(signoff)
    return _signoff_view(signoff, await _names(session, [signoff.signed_by_id]))
