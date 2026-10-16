"""Access-control matrix API (WP4).

    GET    /api/projects/{project_id}/access-matrices            list (viewer)
    POST   /api/projects/{project_id}/access-matrices            create (editor)
    GET    /api/projects/{project_id}/access-matrix-personas     usable columns (viewer)
    GET    /api/access-matrices/{id}                             fetch (viewer)
    PUT    /api/access-matrices/{id}                             replace (editor)
    DELETE /api/access-matrices/{id}                             delete + its runs (editor)
    POST   /api/access-matrices/{id}/run                         execute (editor)
    GET    /api/access-matrices/{id}/runs                        history (viewer)
    GET    /api/access-matrix-runs/{run_id}                      run + cells (viewer)
    GET    /api/access-matrix-runs/{run_id}/cells/{cell_id}/screenshot  presigned (viewer)
    GET    /api/access-matrices/{id}/findings                    access_control findings + run links (viewer)

Rules (see app/services/access_matrix.py): request/page rows only run against
the project's security allowlist with security testing enabled; mutating
requests need `authorized: true`; personas must belong to this project or be
workspace-wide (which additionally needs workspace editor, as in POST /runs).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field as PydField
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    AccessMatrix, AccessMatrixCell, AccessMatrixRun, Persona, Project, TestCase,
    TestRun, TestStatus,
)
from app.services import access_matrix as am
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


class AccessMatrixIn(BaseModel):
    name: str = PydField(min_length=1, max_length=200)
    description: Optional[str] = PydField(default=None, max_length=2000)
    base_url: Optional[str] = PydField(default=None, max_length=500)
    persona_ids: List[int] = []
    include_anonymous: bool = False
    deny_statuses: Optional[List[int]] = None
    rows: List[Dict[str, Any]] = []


class MatrixRunRequest(BaseModel):
    # Attestation that the caller is authorized to send this matrix's mutating
    # requests to the target — the same attestation an active scan requires.
    authorized: bool = False


async def _project_for(session: AsyncSession, principal: AuthPrincipal, project_id: int,
                       min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} role required")
    return project


async def _matrix_for(session, principal, matrix_id: int, min_role: str = "viewer"):
    matrix = await session.get(AccessMatrix, matrix_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="Access matrix not found")
    project = await _project_for(session, principal, matrix.project_id, min_role)
    return matrix, project


async def _validate(session, principal, project: Project, body: AccessMatrixIn) -> List[am.MatrixRow]:
    persona_ids = list(dict.fromkeys(body.persona_ids))
    cols = am.column_keys(persona_ids, body.include_anonymous)
    if not cols:
        raise HTTPException(status_code=400, detail="a matrix needs at least one persona or the anonymous column")
    if len(cols) > am.MAX_COLUMNS:
        raise HTTPException(status_code=400, detail=f"at most {am.MAX_COLUMNS} columns")
    try:
        rows = am.parse_rows(body.rows, cols)
        am.deny_set(body.deny_statuses)
    except ValueError as e:   # pydantic ValidationError is a ValueError
        raise HTTPException(status_code=422, detail=str(e)[:1000])
    await am.validate_personas(session, persona_ids, project.workspace_id, project.id,
                               principal.user.id, field="persona_ids")
    case_ids = {r.test_case_id for r in rows if r.kind == "case"}
    if case_ids:
        found = set((await session.exec(select(TestCase.id).where(
            TestCase.id.in_(case_ids), TestCase.project_id == project.id))).all())
        missing = sorted(case_ids - found)
        if missing:
            raise HTTPException(status_code=400, detail=f"test cases not in this project: {missing}")
    if body.base_url and not body.base_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="base_url must be an http(s) URL")
    return rows


def _matrix_out(m: AccessMatrix) -> Dict[str, Any]:
    return {
        "id": m.id, "project_id": m.project_id, "name": m.name, "description": m.description,
        "base_url": m.base_url, "persona_ids": m.persona_ids or [],
        "include_anonymous": m.include_anonymous,
        "deny_statuses": m.deny_statuses or list(am.DEFAULT_DENY_STATUSES),
        "rows": m.rows or [], "created_by_id": m.created_by_id,
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


def _run_out(r: AccessMatrixRun, cells: Optional[List[AccessMatrixCell]] = None) -> Dict[str, Any]:
    out = {
        "id": r.id, "matrix_id": r.matrix_id, "project_id": r.project_id, "status": r.status,
        "matrix_group": r.matrix_group, "summary": r.summary, "authorized": r.authorized,
        "error": r.error, "created_by_id": r.created_by_id, "created_at": r.created_at,
        "finished_at": r.finished_at, "snapshot": r.snapshot,
    }
    if cells is not None:
        out["cells"] = [{
            "id": c.id, "row_key": c.row_key, "persona_key": c.persona_key, "expect": c.expect,
            "expected_status": c.expected_status, "verdict": c.verdict, "reason": c.reason,
            "observed": c.observed, "test_run_id": c.test_run_id, "completed_at": c.completed_at,
        } for c in cells]
    return out


@router.get("/projects/{project_id}/access-matrices")
async def list_matrices(project_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_for(session, principal, project_id)
    rows = (await session.exec(select(AccessMatrix).where(
        AccessMatrix.project_id == project_id).order_by(AccessMatrix.created_at.desc()))).all()
    return [_matrix_out(m) for m in rows]


@router.get("/projects/{project_id}/access-matrix-personas")
async def usable_personas(project_id: int, session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    """Personas that can be a column here: this project's plus workspace-wide ones.
    Names only — never session state."""
    project = await _project_for(session, principal, project_id)
    rows = (await session.exec(select(Persona).where(
        Persona.workspace_id == project.workspace_id))).all()
    return [{"id": p.id, "name": p.name, "project_id": p.project_id,
             "last_refreshed_at": p.last_refreshed_at}
            for p in rows if p.project_id in (None, project_id)]


@router.post("/projects/{project_id}/access-matrices", status_code=201)
async def create_matrix(project_id: int, body: AccessMatrixIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_for(session, principal, project_id, "editor")
    rows = await _validate(session, principal, project, body)
    m = AccessMatrix(
        project_id=project_id, name=body.name, description=body.description,
        base_url=body.base_url, persona_ids=list(dict.fromkeys(body.persona_ids)),
        include_anonymous=body.include_anonymous,
        deny_statuses=am.deny_set(body.deny_statuses) if body.deny_statuses else None,
        rows=[r.model_dump(exclude_none=True) for r in rows], created_by_id=principal.user.id)
    session.add(m)
    await session.flush()
    await audit_record(session, entity_type="access_matrix", entity_id=m.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": m.name, "rows": len(rows), "columns": len(m.persona_ids)})
    await session.commit()
    await session.refresh(m)
    return _matrix_out(m)


@router.get("/access-matrices/{matrix_id}")
async def get_matrix(matrix_id: int, session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    m, _ = await _matrix_for(session, principal, matrix_id)
    return _matrix_out(m)


@router.put("/access-matrices/{matrix_id}")
async def update_matrix(matrix_id: int, body: AccessMatrixIn, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    m, project = await _matrix_for(session, principal, matrix_id, "editor")
    rows = await _validate(session, principal, project, body)
    m.name, m.description, m.base_url = body.name, body.description, body.base_url
    m.persona_ids = list(dict.fromkeys(body.persona_ids))
    m.include_anonymous = body.include_anonymous
    m.deny_statuses = am.deny_set(body.deny_statuses) if body.deny_statuses else None
    m.rows = [r.model_dump(exclude_none=True) for r in rows]
    m.updated_at = datetime.utcnow()
    session.add(m)
    await audit_record(session, entity_type="access_matrix", entity_id=m.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": m.name, "rows": len(rows), "columns": len(m.persona_ids)})
    await session.commit()
    await session.refresh(m)
    return _matrix_out(m)


@router.delete("/access-matrices/{matrix_id}")
async def delete_matrix(matrix_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    m, project = await _matrix_for(session, principal, matrix_id, "editor")
    run_ids = (await session.exec(select(AccessMatrixRun.id).where(
        AccessMatrixRun.matrix_id == m.id))).all()
    if run_ids:
        await session.exec(delete(AccessMatrixCell).where(AccessMatrixCell.matrix_run_id.in_(run_ids)))
        await session.exec(delete(AccessMatrixRun).where(AccessMatrixRun.id.in_(run_ids)))
    await session.delete(m)
    await audit_record(session, entity_type="access_matrix", entity_id=matrix_id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": m.name, "runs": len(run_ids)})
    await session.commit()
    # Page-row screenshots live under access-matrix/{run_id}/.
    if run_ids:
        from app.services.purge import _delete_objects
        _delete_objects([f"access-matrix/{rid}/" for rid in run_ids])
    return {"deleted": matrix_id, "runs": len(run_ids)}


@router.post("/access-matrices/{matrix_id}/run", status_code=202)
async def run_matrix(matrix_id: int, request: Request,
                     body: MatrixRunRequest = Body(default_factory=MatrixRunRequest),
                     session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    m, project = await _matrix_for(session, principal, matrix_id, "editor")
    cols = am.column_keys(m.persona_ids or [], m.include_anonymous)
    try:
        rows = am.parse_rows(m.rows or [], cols)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)[:1000])
    # Personas are re-validated: one may have been deleted or moved since save.
    personas = await am.validate_personas(session, m.persona_ids or [], project.workspace_id,
                                          project.id, principal.user.id, field="persona_ids")
    from app.services.access_matrix_runtime import security_settings_for
    sec = security_settings_for(project)
    problems = am.safety_problems(
        rows, m.base_url, security_enabled=bool(sec.get("enabled")),
        allowed_domains=sec.get("allowed_domains") or [], authorized=body.authorized,
        columns=cols)
    if problems:
        status = 403 if any(code == 403 for code, _ in problems) else 400
        raise HTTPException(status_code=status, detail="; ".join(msg for _, msg in problems)[:2000])

    planned = am.plan_cells(rows, cols)
    if not planned:
        raise HTTPException(status_code=400, detail="every cell is skip — nothing to run")
    case_cells = [p for p in planned if p[0].kind == "case"]

    from app.services import billing as _billing
    if case_cells:
        from app.services.policy import PolicyContext, evaluate
        verdict = await evaluate("run.dispatch", PolicyContext.from_principal(
            principal, workspace_id=project.workspace_id, project_id=project.id,
            stage="create", session=session))
        if not verdict.allowed:
            if verdict.rule == "plan.run_quota":
                raise HTTPException(status_code=402, detail=(
                    f"Monthly run quota reached ({verdict.data['used']}/{verdict.data['limit']})."))
            raise verdict.http_exception()

    group = uuid.uuid4().hex
    mrun = AccessMatrixRun(
        matrix_id=m.id, project_id=project.id, status="pending", matrix_group=group,
        authorized=body.authorized, created_by_id=principal.user.id,
        snapshot={
            "name": m.name, "base_url": m.base_url, "columns": cols,
            "persona_names": {str(p.id): p.name for p in personas},
            "deny_statuses": am.deny_set(m.deny_statuses),
            "rows": [r.model_dump(exclude_none=True) for r in rows],
        })
    session.add(mrun)
    await session.flush()

    from app.services.test_service import test_service
    created_runs: List[TestRun] = []
    for row, col, cell in planned:
        c = AccessMatrixCell(matrix_run_id=mrun.id, row_key=row.key, persona_key=col,
                             expect=cell.expect, expected_status=cell.status)
        if row.kind == "case":
            case = await session.get(TestCase, row.test_case_id)
            if not case or case.project_id != project.id:
                c.verdict, c.reason = "inconclusive", "test case no longer in this project"
                c.completed_at = datetime.utcnow()
            else:
                eff = await test_service.get_effective_settings(case.test_suite_id, session)
                run = TestRun(
                    status=TestStatus.PENDING, test_suite_id=case.test_suite_id,
                    test_case_id=case.id, project_id=project.id,
                    suite_name=await test_service.get_suite_path(case.test_suite_id, session),
                    test_case_name=case.name,
                    request_headers=eff.get("headers", {}), request_params=eff.get("params", {}),
                    allowed_domains=eff.get("allowed_domains", []),
                    domain_settings=eff.get("domain_settings", {}),
                    browser="chromium", user_id=principal.user.id,
                    persona_id=None if col == am.ANONYMOUS else int(col),
                    anonymous=True if col == am.ANONYMOUS else None,
                    matrix_group=group, selection_mode="matrix", selection_total=1,
                    agent_id=principal.agent_id,
                    api_key_id=principal.api_key.id if principal.api_key else None)
                session.add(run)
                await session.flush()
                c.test_run_id = run.id
                created_runs.append(run)
        session.add(c)

    await audit_record(session, entity_type="access_matrix", entity_id=m.id, action="run",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"matrix_run_id": mrun.id, "cells": len(planned),
                                "authorized": body.authorized})
    await session.commit()

    from app.worker import run_test_suite
    for run in created_runs:
        try:
            run_test_suite.delay(run.id)
        except Exception as e:  # noqa: BLE001
            print(f"[AccessMatrix] failed to queue run {run.id}: {e}")
    if created_runs:
        try:
            await _billing.record_usage(session, project.workspace_id, "runs", len(created_runs))
        except Exception as e:  # noqa: BLE001
            print(f"[AccessMatrix] usage metering failed: {e}")
        from app.services.agent_controls import record_usage
        await record_usage(session, principal, project.workspace_id, runs=len(created_runs))
    from app.tasks.access_matrix_tasks import dispatch_access_matrix_run
    try:
        dispatch_access_matrix_run.delay(mrun.id)
    except Exception as e:  # noqa: BLE001
        print(f"[AccessMatrix] failed to queue matrix run {mrun.id}: {e}")
    await session.refresh(mrun)
    return _run_out(mrun)


@router.get("/access-matrices/{matrix_id}/runs")
async def list_matrix_runs(matrix_id: int, limit: int = 20,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    m, _ = await _matrix_for(session, principal, matrix_id)
    rows = (await session.exec(select(AccessMatrixRun).where(
        AccessMatrixRun.matrix_id == m.id).order_by(AccessMatrixRun.created_at.desc())
        .limit(max(1, min(limit, 100))))).all()
    out = []
    for r in rows:
        d = _run_out(r)
        d.pop("snapshot", None)
        out.append(d)
    return out


async def _matrix_run_for(session, principal, run_id: int) -> AccessMatrixRun:
    mrun = await session.get(AccessMatrixRun, run_id)
    if not mrun:
        raise HTTPException(status_code=404, detail="Matrix run not found")
    await _project_for(session, principal, mrun.project_id)
    return mrun


@router.get("/access-matrix-runs/{run_id}")
async def get_matrix_run(run_id: int, session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    mrun = await _matrix_run_for(session, principal, run_id)
    cells = (await session.exec(select(AccessMatrixCell).where(
        AccessMatrixCell.matrix_run_id == mrun.id).order_by(AccessMatrixCell.id))).all()
    return _run_out(mrun, cells)


@router.get("/access-matrix-runs/{run_id}/cells/{cell_id}/screenshot")
async def cell_screenshot(run_id: int, cell_id: int, session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    mrun = await _matrix_run_for(session, principal, run_id)
    cell = await session.get(AccessMatrixCell, cell_id)
    if not cell or cell.matrix_run_id != mrun.id:
        raise HTTPException(status_code=404, detail="Cell not found")
    key = (cell.observed or {}).get("screenshot")
    # Only keys under this run's own prefix are presigned: the observation is
    # worker-supplied, and a crafted one must not mint a URL for another run.
    if not key or not str(key).startswith(f"access-matrix/{mrun.id}/") or ".." in str(key):
        raise HTTPException(status_code=404, detail="No screenshot for this cell")
    from app.core.storage import minio_client
    return {"url": minio_client.get_presigned_url(key)}


@router.get("/access-matrices/{matrix_id}/findings")
async def matrix_findings(matrix_id: int, session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    """The `access_control` findings this matrix raised, each linked to the
    latest run whose cell granted the access.

    SecurityFinding has no matrix column (its run_id/scan_id are FKs to other
    tables), so the link is recomputed: the finding fingerprint is a function of
    (matrix, row, persona), and the run comes from this matrix's own failed
    DENY cells. Scoped by project AND fingerprint, so another matrix's (or
    project's) findings never appear here.
    """
    from app.models import SecurityFinding

    m, _ = await _matrix_for(session, principal, matrix_id)
    granted = (await session.exec(
        select(AccessMatrixCell.id, AccessMatrixCell.row_key, AccessMatrixCell.persona_key,
               AccessMatrixRun.id)
        .join(AccessMatrixRun, AccessMatrixRun.id == AccessMatrixCell.matrix_run_id)
        .where(AccessMatrixRun.matrix_id == m.id, AccessMatrixCell.expect == "deny",
               AccessMatrixCell.verdict == "fail")
        .order_by(AccessMatrixRun.id.desc()))).all()
    latest: Dict[str, Dict[str, Any]] = {}
    for cell_id, row_key, persona_key, run_id in granted:
        fp = am.finding_fingerprint(m.id, row_key, persona_key)
        latest.setdefault(fp, {"matrix_run_id": run_id, "cell_id": cell_id,
                               "row_key": row_key, "persona_key": persona_key})
    if not latest:
        return []
    findings = (await session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == m.project_id,
        SecurityFinding.scan_type == am.FINDING_SCAN_TYPE,
        SecurityFinding.fingerprint.in_(list(latest)))
        .order_by(SecurityFinding.created_at.desc()))).all()
    return [{
        "id": f.id, "title": f.title, "severity": f.severity, "status": f.status,
        "category": f.category, "evidence": f.evidence, "target_url": f.target_url,
        "created_at": f.created_at, "resolved_at": f.resolved_at, **latest[f.fingerprint],
    } for f in findings]
