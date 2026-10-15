"""Access-matrix execution: dispatch request/page cells, collect verdicts.

Request and page cells ride the normal `jobs:pending` stream as
`job_type=access_check` side jobs (the persona_refresh/discovery shape: no
TestRun, the worker parks its observation under `access_check:result:{cell}`).
Case cells run as ordinary persona TestRuns created by the API.

Collection does not hold a Celery worker hostage the way persona refresh does:
`collect_access_matrix_run` checks once, then re-schedules itself with a
countdown until every cell has a verdict or the run's deadline passes. Cells
still pending at the deadline become `inconclusive` — never `pass`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import redis
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings

_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

JOBS_STREAM = "jobs:pending"
CONSUMER_GROUP = "execution-workers"
POLL_COUNTDOWN = 3
# Case rows run full journeys and may queue behind other runs.
DEADLINE = timedelta(minutes=45)
_TERMINAL = {"passed", "failed", "error"}


def _redis():
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def _status_text(status) -> str:
    return str(getattr(status, "value", status) or "").lower()


@celery_app.task(name="app.tasks.access_matrix_tasks.dispatch_access_matrix_run")
def dispatch_access_matrix_run(matrix_run_id: int) -> Dict[str, Any]:
    from app.models import AccessMatrixCell, AccessMatrixRun, Persona, Project
    from app.services.access_matrix import ANONYMOUS, resolve_url
    from app.services.access_matrix_runtime import (
        build_cell_job, identity_for, security_allowlist_for,
    )
    from app.worker import _load_data_policy

    with Session(_engine) as session:
        mrun = session.get(AccessMatrixRun, matrix_run_id)
        if not mrun:
            return {"status": "not_found"}
        snap = mrun.snapshot or {}
        rows = {r["key"]: r for r in snap.get("rows", [])}
        project = session.get(Project, mrun.project_id)
        allowed = security_allowlist_for(mrun.project_id, session)
        data_policy = _load_data_policy(mrun.project_id, session)
        cells = session.exec(select(AccessMatrixCell).where(
            AccessMatrixCell.matrix_run_id == mrun.id)).all()

        personas: Dict[str, Any] = {}

        def identity(key: str):
            if key == ANONYMOUS:
                return identity_for(None)
            if key not in personas:
                p = session.get(Persona, int(key))
                # Re-validated: the persona may have been deleted or moved
                # since the matrix was saved.
                if p and p.workspace_id == project.workspace_id and (
                        not p.project_id or p.project_id == project.id):
                    personas[key] = identity_for(p)
                else:
                    personas[key] = None
            return personas[key]

        jobs: List[Dict[str, Any]] = []
        for cell in cells:
            row = rows.get(cell.row_key) or {}
            if row.get("kind") == "case" or cell.verdict != "pending":
                continue
            ident = identity(cell.persona_key)
            if ident is None:
                cell.verdict, cell.reason = "inconclusive", "persona no longer available"
                cell.completed_at = datetime.utcnow()
                session.add(cell)
                continue
            owner = None
            swap = row.get("swap")
            if swap:
                owner_ident = identity(str(swap.get("owner_persona_id")))
                if owner_ident is None:
                    cell.verdict, cell.reason = "inconclusive", "swap owner persona no longer available"
                    cell.completed_at = datetime.utcnow()
                    session.add(cell)
                    continue
                owner = {
                    "identity": owner_ident,
                    "method": (swap.get("method") or "GET").upper(),
                    "url": resolve_url(swap.get("url"), snap.get("base_url")),
                    "body": swap.get("body"),
                    "extractor": swap.get("extractor"),
                    "placeholder": swap.get("placeholder") or "{id}",
                }
            jobs.append(build_cell_job(
                cell_id=cell.id, row=row, url=resolve_url(row.get("url"), snap.get("base_url")),
                identity=ident, owner=owner, allowed_domains=allowed,
                authorized=mrun.authorized, data_policy=data_policy, matrix_run_id=mrun.id))
        mrun.status = "running"
        session.add(mrun)
        session.commit()

    if jobs:
        rc = _redis()
        try:
            rc.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        for job in jobs:
            rc.xadd(JOBS_STREAM, {"job_id": job["job_id"], "run_id": "0",
                                  "payload": json.dumps(job)})
    collect_access_matrix_run.apply_async((matrix_run_id,), countdown=POLL_COUNTDOWN)
    return {"status": "dispatched", "jobs": len(jobs)}


def _first_result_error(session, run_id: int) -> Optional[str]:
    from app.models import TestCaseResult
    row = session.exec(select(TestCaseResult.error_message).where(
        TestCaseResult.test_run_id == run_id,
        TestCaseResult.error_message.is_not(None)).limit(1)).first()
    return row


def collect_once(session, rc, matrix_run_id: int, now: Optional[datetime] = None) -> bool:
    """Fold any available results into cells. Returns True when the run is done.

    `rc` needs `get` and `delete` (a fake in tests).
    """
    from app.models import AccessMatrix, AccessMatrixCell, AccessMatrixRun, TestRun
    from app.services.access_matrix import (
        ANONYMOUS, apply_findings, judge_case, judge_observation, summarize,
    )
    from app.services.access_matrix_runtime import RESULT_KEY
    from app.services.redaction import redact_access_observation, redact_text

    now = now or datetime.utcnow()
    mrun = session.get(AccessMatrixRun, matrix_run_id)
    if not mrun or mrun.status in ("completed", "error"):
        return True
    snap = mrun.snapshot or {}
    rows = {r["key"]: r for r in snap.get("rows", [])}
    deny = snap.get("deny_statuses") or [401, 403, 404]
    cells = session.exec(select(AccessMatrixCell).where(
        AccessMatrixCell.matrix_run_id == mrun.id)).all()
    expired = now - mrun.created_at > DEADLINE

    for cell in cells:
        if cell.verdict != "pending":
            continue
        row = rows.get(cell.row_key) or {}
        if row.get("kind") == "case":
            run = session.get(TestRun, cell.test_run_id) if cell.test_run_id else None
            status = _status_text(run.status) if run else ""
            if status in _TERMINAL:
                error = run.error_message or _first_result_error(session, run.id)
                cell.verdict, cell.reason = judge_case(cell.expect, status, error, deny)
                cell.observed = {"run_id": run.id, "run_status": status,
                                 "error": redact_text((error or "")[:500]) or None}
                cell.completed_at = now
            elif expired or run is None:
                cell.verdict, cell.reason = "inconclusive", "the case run did not finish in time"
                cell.completed_at = now
            session.add(cell)
            continue
        key = RESULT_KEY.format(cell.id)
        raw = rc.get(key)
        if raw:
            rc.delete(key)
            try:
                obs = json.loads(raw)
            except ValueError:
                obs = {"error_kind": "error", "error": "unreadable worker result"}
            # The worker redacts at capture; this is the backstop for an older
            # worker image (same reason worker results are redacted on ingestion).
            obs = redact_access_observation(obs)
            cell.observed = obs
            cell.verdict, cell.reason = judge_observation(
                row.get("kind"), cell.expect, obs, deny, cell.expected_status,
                has_selector=bool(row.get("selector")))
            cell.completed_at = now
            session.add(cell)
        elif expired:
            cell.verdict, cell.reason = "inconclusive", "no worker result before the deadline"
            cell.completed_at = now
            session.add(cell)

    if any(c.verdict == "pending" for c in cells):
        session.commit()
        return False

    matrix = session.get(AccessMatrix, mrun.matrix_id)
    names = {str(k): v for k, v in (snap.get("persona_names") or {}).items()}
    names.setdefault(ANONYMOUS, "anonymous")
    findings = apply_findings(session, matrix, mrun, cells, names) if matrix else {}
    mrun.summary = {**summarize(cells), "findings": findings}
    mrun.status = "completed"
    mrun.finished_at = now
    session.add(mrun)
    session.commit()
    return True


@celery_app.task(name="app.tasks.access_matrix_tasks.collect_access_matrix_run")
def collect_access_matrix_run(matrix_run_id: int) -> Dict[str, Any]:
    with Session(_engine) as session:
        done = collect_once(session, _redis(), matrix_run_id)
    if not done:
        collect_access_matrix_run.apply_async((matrix_run_id,), countdown=POLL_COUNTDOWN)
    return {"matrix_run_id": matrix_run_id, "done": done}
