"""Pilot value report + hypothesis feedback (WP16d, decision V6).

    GET    /api/projects/{id}/value-report        ?start=&end=          (viewer)
    GET    /api/workspaces/{id}/value-report      ?start=&end=&project_ids=
           roll-up over the projects the caller can read (others ignored)
    GET    /api/runs/{run_id}/hypothesis-feedback                       (viewer)
    PUT    /api/runs/{run_id}/results/{result_id}/hypothesis-feedback   (editor, humans)
           {index, hypothesis_key, verdict: correct|incorrect}
    DELETE /api/runs/{run_id}/results/{result_id}/hypothesis-feedback
           ?index=&hypothesis_key=                                      (editor, humans)

Ratings are editor-only and refuse API keys: they move a reported accuracy
figure, and an agent rating its own analysis would make that figure
meaningless. The client sends back the hypothesis key it was shown; if the
analysis has been regenerated since, the key no longer matches and the vote is
refused (409) rather than landing on a different claim. Validation runs
(WP16c) are not rated — they never count in a statistic.
The maths is app.services.value_report.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field as PydField
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.poc_reports import _ids
from app.api.reliability_reports import require_project
from app.api.reports import _workspace, accessible_projects
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import HypothesisFeedback, TestCaseResult, TestRun
from app.services import value_report as vr
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.poc_report import parse_range

router = APIRouter()


def _range(start: Optional[str], end: Optional[str]):
    try:
        return parse_range(start, end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/projects/{project_id}/value-report")
async def project_value_report(project_id: int, start: Optional[str] = Query(None),
                               end: Optional[str] = Query(None),
                               session: AsyncSession = Depends(get_session),
                               principal: AuthPrincipal = Depends(get_current_principal)):
    project = await require_project(project_id, principal, session)
    s, e = _range(start, end)
    inputs = await vr.collect(session, [project.id], s, e)
    return vr.assemble(inputs, s, e, {"kind": "project", "project_id": project.id,
                                      "project_name": project.name,
                                      "projects": [{"id": project.id, "name": project.name}]})


@router.get("/workspaces/{workspace_id}/value-report")
async def workspace_value_report(workspace_id: int, start: Optional[str] = Query(None),
                                 end: Optional[str] = Query(None),
                                 project_ids: Optional[str] = Query(None),
                                 session: AsyncSession = Depends(get_session),
                                 principal: AuthPrincipal = Depends(get_current_principal)):
    ws = await _workspace(session, principal, workspace_id)
    if principal.api_key is not None and principal.api_key.workspace_id != ws.id:
        raise HTTPException(status_code=403, detail="API key belongs to another workspace")
    s, e = _range(start, end)
    visible = await accessible_projects(session, principal, workspace_id)
    picked = set(_ids(project_ids))
    projects = [p for p in visible if p.id in picked] if picked else visible
    inputs = await vr.collect(session, [p.id for p in projects], s, e)
    report = vr.assemble(inputs, s, e, {"kind": "workspace", "workspace_id": ws.id,
                                        "workspace_name": ws.name,
                                        "projects": [{"id": p.id, "name": p.name} for p in projects]})
    report["available_projects"] = [{"id": p.id, "name": p.name} for p in visible]
    return report


# ---------------------------------------------------------------------------
# Hypothesis feedback
# ---------------------------------------------------------------------------

class HypothesisVoteIn(BaseModel):
    index: int = PydField(ge=0, le=50)
    hypothesis_key: str = PydField(min_length=8, max_length=64)
    verdict: Literal["correct", "incorrect"]


def _report_hypotheses(run: TestRun, result: TestCaseResult) -> List[Dict[str, Any]]:
    """The hypotheses the run page shows for this result: the run rollup's
    report for it first (that is what the AI panel renders), else the
    result's own stored report."""
    rollup = run.ai_analysis if isinstance(run.ai_analysis, dict) else {}
    for r in rollup.get("reports") or []:
        if isinstance(r, dict) and r.get("result_id") == result.id:
            return [h for h in (r.get("hypotheses") or []) if isinstance(h, dict)]
    raw = result.ai_analysis
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [h for h in (data.get("hypotheses") or []) if isinstance(h, dict)]


async def _run(run_id: int, principal: AuthPrincipal, session: AsyncSession) -> TestRun:
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await require_project(run.project_id, principal, session)
    return run


async def _can_rate(run: TestRun, principal: AuthPrincipal, session: AsyncSession) -> bool:
    if principal.api_key is not None or run.is_validation is True:
        return False
    return await access_service.has_project_access(principal.user.id, run.project_id, session,
                                                   min_role="editor")


@router.get("/runs/{run_id}/hypothesis-feedback")
async def run_hypothesis_feedback(run_id: int, session: AsyncSession = Depends(get_session),
                                  principal: AuthPrincipal = Depends(get_current_principal)):
    run = await _run(run_id, principal, session)
    results = (await session.exec(select(TestCaseResult).where(
        TestCaseResult.test_run_id == run.id))).all()
    votes = (await session.exec(select(HypothesisFeedback).where(
        HypothesisFeedback.run_id == run.id,
        HypothesisFeedback.project_id == run.project_id))).all()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for res in results:
        hyps = _report_hypotheses(run, res)
        if not hyps:
            continue
        rows = []
        for i, h in enumerate(hyps):
            key = vr.hypothesis_key(h)
            mine = [v for v in votes if v.result_id == res.id and v.hypothesis_key == key
                    and v.user_id == principal.user.id]
            rows.append({
                "index": i, "hypothesis_key": key,
                "correct": sum(1 for v in votes if v.result_id == res.id and v.hypothesis_key == key
                               and v.verdict == "correct"),
                "incorrect": sum(1 for v in votes if v.result_id == res.id and v.hypothesis_key == key
                                 and v.verdict == "incorrect"),
                "mine": mine[0].verdict if mine else None,
            })
        out[str(res.id)] = rows
    return {"run_id": run.id, "can_rate": await _can_rate(run, principal, session),
            "is_validation": run.is_validation is True, "results": out}


async def _target(run_id: int, result_id: int, index: int, key: str,
                  principal: AuthPrincipal, session: AsyncSession):
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="Ratings are made by people, not API keys")
    run = await _run(run_id, principal, session)
    if not await access_service.has_project_access(principal.user.id, run.project_id, session,
                                                   min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    if run.is_validation is True:
        raise HTTPException(status_code=400, detail="Validation runs are not rated")
    result = await session.get(TestCaseResult, result_id)
    if not result or result.test_run_id != run.id:
        raise HTTPException(status_code=404, detail="Result not found in this run")
    hyps = _report_hypotheses(run, result)
    if index >= len(hyps) or vr.hypothesis_key(hyps[index]) != key:
        raise HTTPException(status_code=409,
                            detail="The analysis has changed since it was shown; reload and rate again")
    return run, result, hyps[index]


@router.put("/runs/{run_id}/results/{result_id}/hypothesis-feedback")
async def rate_hypothesis(run_id: int, result_id: int, body: HypothesisVoteIn,
                          session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    run, result, hyp = await _target(run_id, result_id, body.index, body.hypothesis_key,
                                     principal, session)
    # Plain values: a rollback below expires every loaded instance, and an
    # expired attribute cannot lazy-load on an async session.
    uid, pid, rid, res_id, run_at = (principal.user.id, run.project_id, run.id, result.id,
                                     run.created_at)

    async def existing():
        return (await session.exec(select(HypothesisFeedback).where(
            HypothesisFeedback.result_id == res_id,
            HypothesisFeedback.hypothesis_key == body.hypothesis_key,
            HypothesisFeedback.user_id == uid))).first()

    row = await existing()
    previous = row.verdict if row else None
    if row is None:
        row = HypothesisFeedback(
            project_id=pid, run_id=rid, result_id=res_id, run_created_at=run_at,
            hypothesis_index=body.index, hypothesis_key=body.hypothesis_key,
            category=str(hyp.get("category") or "unknown")[:40],
            source=str(hyp.get("source") or "heuristic")[:80], verdict=body.verdict, user_id=uid)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # A double click raced us to the insert: take the row it made.
            await session.rollback()
            row = await existing()
            if row is None:
                raise HTTPException(status_code=409, detail="Could not record the rating; try again")
            previous = row.verdict
    if previous != body.verdict:
        row.verdict = body.verdict
        row.updated_at = datetime.utcnow()
        session.add(row)
        await session.flush()
        from app.models import Project
        project = await session.get(Project, pid)
        await audit_record(session, entity_type="hypothesis_feedback", entity_id=row.id,
                           action="rate", principal=principal,
                           workspace_id=project.workspace_id if project else None,
                           changes={"run_id": rid, "result_id": res_id,
                                    "hypothesis_index": body.index, "verdict": body.verdict,
                                    "previous": previous})
    out = {"id": row.id, "verdict": body.verdict, "previous": previous}
    await session.commit()
    return out


@router.delete("/runs/{run_id}/results/{result_id}/hypothesis-feedback", status_code=204)
async def clear_hypothesis_rating(run_id: int, result_id: int,
                                  index: int = Query(..., ge=0, le=50),
                                  hypothesis_key: str = Query(..., min_length=8, max_length=64),
                                  session: AsyncSession = Depends(get_session),
                                  principal: AuthPrincipal = Depends(get_current_principal)):
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="Ratings are made by people, not API keys")
    run = await _run(run_id, principal, session)
    if not await access_service.has_project_access(principal.user.id, run.project_id, session,
                                                   min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    row = (await session.exec(select(HypothesisFeedback).where(
        HypothesisFeedback.run_id == run.id, HypothesisFeedback.result_id == result_id,
        HypothesisFeedback.hypothesis_key == hypothesis_key,
        HypothesisFeedback.user_id == principal.user.id))).first()
    if row:
        from app.models import Project
        project = await session.get(Project, run.project_id)
        await audit_record(session, entity_type="hypothesis_feedback", entity_id=row.id,
                           action="clear", principal=principal,
                           workspace_id=project.workspace_id if project else None,
                           changes={"run_id": run.id, "result_id": result_id,
                                    "hypothesis_index": index, "previous": row.verdict})
        await session.delete(row)
        await session.commit()
    return Response(status_code=204)
