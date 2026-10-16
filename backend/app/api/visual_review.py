"""Visual review (WP14e): a run's visual comparisons, grouped by likely
same change, with group accept/keep and comment threads.

    GET  /api/projects/{project_id}/visual-review/runs
    GET  /api/projects/{project_id}/visual-review?run_id=
    POST /api/projects/{project_id}/visual-review/groups/{group_key}/accept
    POST /api/projects/{project_id}/visual-review/groups/{group_key}/keep
    GET  /api/projects/{project_id}/visual-review/comments
    POST /api/projects/{project_id}/visual-review/comments
    POST /api/projects/{project_id}/visual-review/comments/{comment_id}/resolve

Viewers read; editors decide and comment. A group decision is still one
audit row per baseline (`visual_baseline.accept` / `.keep`, with the group
key in `changes`), exactly as when each item is decided alone — so a
baseline's history reads the same however the decision was made.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.api.visual_baselines import _audit, _resolve_image_url, promote_candidate
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    AuditLog, Project, TestCase, TestRun, User, VisualBaseline, VisualComparison,
    VisualReviewComment,
)
from app.services import audit
from app.services.access_service import access_service
from app.services.visual_review import group_comparisons, is_change

router = APIRouter()

GROUP_KEY_RE = re.compile(r"^g(\d{1,12})$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_COMMENT = 4000


async def _project_for(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                       min_role: str = "viewer") -> Project:
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    project = await session.get(Project, project_id)
    if not project or not await access_service.has_project_access(
            principal.user.id, project_id, session, min_role=min_role):
        raise HTTPException(status_code=403 if project else 404,
                            detail="Editor access required" if min_role == "editor" else "Access denied")
    return project


async def _run_in_project(run_id: int, project_id: int, session: AsyncSession) -> TestRun:
    run = await session.get(TestRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found in this project")
    return run


def _presign(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    try:
        from app.core.storage import minio_client
        return minio_client.get_presigned_url(key)
    except Exception:  # noqa: BLE001 — storage down: the review still loads
        return None


def _as_item(c: VisualComparison) -> Dict[str, Any]:
    return {
        "id": c.id, "test_case_id": c.test_case_id, "test_name": c.test_name,
        "step_id": c.step_id, "browser": c.browser, "device": c.device,
        "element_key": c.element_key or "", "selector": c.selector,
        "kind": "element" if c.element_key else "full_page",
        "status": c.status, "diff_ratio": c.diff_ratio, "diff_pixels": c.diff_pixels,
        "total_pixels": c.total_pixels, "width": c.width, "height": c.height,
        "size_mismatch": c.size_mismatch, "tolerance": c.tolerance,
        "regions": c.regions or [], "candidate_key": c.candidate_key, "diff_key": c.diff_key,
    }


async def _run_items(run_id: int, project_id: int, session: AsyncSession) -> List[Dict[str, Any]]:
    rows = (await session.exec(select(VisualComparison).where(
        VisualComparison.test_run_id == run_id,
        VisualComparison.project_id == project_id).order_by(VisualComparison.id))).all()
    return [_as_item(c) for c in rows]


async def _slot_baselines(items, session: AsyncSession) -> Dict[tuple, VisualBaseline]:
    """Current baseline of each item's slot (case, step, browser, device,
    element key) — looked up by slot, not by the comparison's baseline_id,
    which may predate a re-promote or a delete."""
    case_ids = sorted({i["test_case_id"] for i in items if i["test_case_id"]})
    if not case_ids:
        return {}
    rows = (await session.exec(select(VisualBaseline).where(
        VisualBaseline.test_case_id.in_(case_ids)))).all()
    return {(b.test_case_id, b.step_id, b.browser, b.device, b.element_key or ""): b for b in rows}


def _slot(i) -> tuple:
    return (i["test_case_id"], i["step_id"], i["browser"], i["device"], i["element_key"])


@router.get("/projects/{project_id}/visual-review/runs")
async def review_runs(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Recent runs of the project that recorded visual comparisons."""
    await _project_for(project_id, principal, session)
    rows = (await session.exec(
        select(VisualComparison.test_run_id, func.count(VisualComparison.id),
               func.count(VisualComparison.id).filter(VisualComparison.status == "failed"),
               func.count(VisualComparison.id).filter(VisualComparison.diff_pixels > 0))
        .where(VisualComparison.project_id == project_id)
        .group_by(VisualComparison.test_run_id)
        .order_by(VisualComparison.test_run_id.desc()).limit(limit))).all()
    runs = {r.id: r for r in (await session.exec(select(TestRun).where(
        TestRun.id.in_([r[0] for r in rows] or [-1])))).all()}
    return [{
        "run_id": run_id,
        "created_at": runs[run_id].created_at if run_id in runs else None,
        "status": getattr(getattr(runs.get(run_id), "status", None), "value", None),
        "git_branch": getattr(runs.get(run_id), "git_branch", None),
        "comparisons": total, "failed": failed, "changed": changed,
    } for run_id, total, failed, changed in rows]


@router.get("/projects/{project_id}/visual-review")
async def review_run(
    project_id: int,
    run_id: Optional[int] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """One run's comparisons with groups of related changes. Defaults to the
    project's latest run that has comparisons."""
    await _project_for(project_id, principal, session)
    if run_id is None:
        run_id = (await session.exec(select(func.max(VisualComparison.test_run_id)).where(
            VisualComparison.project_id == project_id))).first()
        if run_id is None:
            return {"run": None, "items": [], "groups": []}
    run = await _run_in_project(run_id, project_id, session)
    items = await _run_items(run_id, project_id, session)

    baselines = await _slot_baselines(items, session)
    case_names = {c.id: c.name for c in (await session.exec(select(TestCase).where(
        TestCase.id.in_([i["test_case_id"] for i in items if i["test_case_id"]] or [-1])))).all()}
    decisions = await _decisions(run_id, [b.id for b in baselines.values()], session)
    comments = (await session.exec(select(VisualReviewComment).where(
        VisualReviewComment.project_id == project_id,
        VisualReviewComment.test_run_id == run_id))).all()
    groups = group_comparisons(items)
    group_of = {iid: g["key"] for g in groups for iid in g["item_ids"]}

    for i in items:
        b = baselines.get(_slot(i))
        i["case_name"] = case_names.get(i["test_case_id"]) or i["test_name"]
        i["baseline_id"] = b.id if b else None
        i["baseline_url"] = _resolve_image_url(b.image_url) if b else None
        candidate_key = i.pop("candidate_key")
        i["has_candidate"] = bool(candidate_key)
        i["candidate_url"] = _presign(candidate_key)
        i["diff_url"] = _presign(i.pop("diff_key"))
        i["decision"] = decisions.get(b.id) if b else None
        i["group_key"] = group_of.get(i["id"])
        i["is_change"] = is_change(i)
        thread = [c for c in comments if c.comparison_id == i["id"]]
        i["comment_count"] = len(thread)
        i["open_comment_count"] = sum(1 for c in thread if not c.resolved)
    for g in groups:
        thread = [c for c in comments if c.group_key == g["key"] and c.comparison_id is None]
        g["comment_count"] = len(thread)
        g["open_comment_count"] = sum(1 for c in thread if not c.resolved)
    return {
        "run": {"id": run.id, "created_at": run.created_at,
                "status": getattr(run.status, "value", run.status),
                "git_branch": getattr(run, "git_branch", None)},
        "items": items,
        "groups": groups,
    }


async def _decisions(run_id: int, baseline_ids: List[int], session: AsyncSession) -> Dict[int, dict]:
    """The latest accept/keep on each baseline that names this run."""
    if not baseline_ids:
        return {}
    rows = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "visual_baseline",
        AuditLog.entity_id.in_(baseline_ids),
        AuditLog.action.in_(["visual_baseline.accept", "visual_baseline.keep"]),
    ).order_by(AuditLog.id.desc()).limit(2000))).all()
    out: Dict[int, dict] = {}
    for r in rows:
        if r.entity_id in out or (r.changes or {}).get("run_id") != run_id:
            continue
        out[r.entity_id] = {"action": r.action.removeprefix("visual_baseline."),
                            "at": r.timestamp, "actor_label": r.actor_label}
    return out


class GroupDecision(BaseModel):
    run_id: int
    note: Optional[str] = PydField(default=None, max_length=500)


async def _group_members(project_id: int, run_id: int, group_key: str,
                         session: AsyncSession) -> List[Dict[str, Any]]:
    if not GROUP_KEY_RE.match(group_key):
        raise HTTPException(status_code=422, detail="Invalid group key")
    items = await _run_items(run_id, project_id, session)
    group = next((g for g in group_comparisons(items) if g["key"] == group_key), None)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found in this run")
    ids = set(group["item_ids"])
    return [i for i in items if i["id"] in ids]


@router.post("/projects/{project_id}/visual-review/groups/{group_key}/accept")
async def accept_group(
    project_id: int,
    group_key: str,
    body: GroupDecision,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Accept every candidate in the group as its slot's new baseline."""
    await _project_for(project_id, principal, session, min_role="editor")
    await _run_in_project(body.run_id, project_id, session)
    members = await _group_members(project_id, body.run_id, group_key, session)
    accepted, skipped = [], []
    for m in members:
        case = await session.get(TestCase, m["test_case_id"]) if m["test_case_id"] else None
        if not case or case.project_id != project_id:
            skipped.append({"id": m["id"], "reason": "case no longer exists"})
            continue
        try:
            async with session.begin_nested():
                b = await promote_candidate(
                    session, principal, case, run_id=body.run_id, step_id=m["step_id"],
                    browser=m["browser"], device=m["device"], element_key=m["element_key"],
                    selector=m["selector"], source_key=m["candidate_key"],
                    audit_extra={"group_key": group_key})
            accepted.append({"id": m["id"], "baseline_id": b.id})
        except HTTPException as exc:
            skipped.append({"id": m["id"], "reason": exc.detail})
    await session.commit()
    return {"accepted": accepted, "skipped": skipped}


@router.post("/projects/{project_id}/visual-review/groups/{group_key}/keep")
async def keep_group(
    project_id: int,
    group_key: str,
    body: GroupDecision,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Record "keep the baseline" for every item of the group that has one."""
    await _project_for(project_id, principal, session, min_role="editor")
    await _run_in_project(body.run_id, project_id, session)
    members = await _group_members(project_id, body.run_id, group_key, session)
    baselines = await _slot_baselines(members, session)
    kept, skipped = [], []
    for m in members:
        b = baselines.get(_slot(m))
        case = await session.get(TestCase, m["test_case_id"]) if m["test_case_id"] else None
        if not b or not case or case.project_id != project_id:
            skipped.append({"id": m["id"], "reason": "no baseline to keep"})
            continue
        await _audit(session, case, b, "visual_baseline.keep", principal, {
            "image": b.image_url, "run_id": body.run_id, "group_key": group_key,
            "note": (body.note or "")[:500] or None,
        })
        kept.append({"id": m["id"], "baseline_id": b.id})
    await session.commit()
    return {"kept": kept, "skipped": skipped}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

class CommentCreate(BaseModel):
    body: str = PydField(min_length=1, max_length=MAX_COMMENT)
    comparison_id: Optional[int] = None
    run_id: Optional[int] = None
    group_key: Optional[str] = None


class CommentResolve(BaseModel):
    resolved: bool = True


def _clean_body(text: str) -> str:
    """Plain text: control characters out, nothing interpreted. The UI
    renders it as text (escaped), never as HTML or markdown."""
    return _CONTROL_RE.sub("", text).strip()[:MAX_COMMENT]


def _comment_out(c: VisualReviewComment, users: Dict[int, User]) -> Dict[str, Any]:
    author = users.get(c.author_id) if c.author_id else None
    return {
        "id": c.id, "comparison_id": c.comparison_id, "run_id": c.test_run_id,
        "group_key": c.group_key, "body": c.body, "resolved": c.resolved,
        "resolved_at": c.resolved_at, "created_at": c.created_at,
        "author": ({"id": author.id, "name": author.full_name or author.email}
                   if author else None),
    }


async def _users(ids, session) -> Dict[int, User]:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    return {u.id: u for u in (await session.exec(select(User).where(User.id.in_(ids)))).all()}


@router.get("/projects/{project_id}/visual-review/comments")
async def list_comments(
    project_id: int,
    comparison_id: Optional[int] = None,
    run_id: Optional[int] = None,
    group_key: Optional[str] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    await _project_for(project_id, principal, session)
    q = select(VisualReviewComment).where(VisualReviewComment.project_id == project_id)
    if comparison_id is not None:
        q = q.where(VisualReviewComment.comparison_id == comparison_id)
    elif run_id is not None and group_key:
        q = q.where(VisualReviewComment.test_run_id == run_id,
                    VisualReviewComment.group_key == group_key,
                    VisualReviewComment.comparison_id.is_(None))
    elif run_id is not None:
        q = q.where(VisualReviewComment.test_run_id == run_id)
    else:
        raise HTTPException(status_code=422, detail="Provide comparison_id or run_id")
    rows = (await session.exec(q.order_by(VisualReviewComment.id).limit(500))).all()
    users = await _users([r.author_id for r in rows], session)
    return [_comment_out(r, users) for r in rows]


@router.post("/projects/{project_id}/visual-review/comments")
async def add_comment(
    project_id: int,
    body: CommentCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await _project_for(project_id, principal, session, min_role="editor")
    text = _clean_body(body.body)
    if not text:
        raise HTTPException(status_code=422, detail="Comment is empty")
    if body.comparison_id is not None:
        comp = await session.get(VisualComparison, body.comparison_id)
        if not comp or comp.project_id != project_id:
            raise HTTPException(status_code=404, detail="Review item not found in this project")
        run_id, group_key = comp.test_run_id, None
    elif body.run_id is not None and body.group_key:
        m = GROUP_KEY_RE.match(body.group_key)
        if not m:
            raise HTTPException(status_code=422, detail="Invalid group key")
        await _run_in_project(body.run_id, project_id, session)
        anchor = await session.get(VisualComparison, int(m.group(1)))
        if not anchor or anchor.project_id != project_id or anchor.test_run_id != body.run_id:
            raise HTTPException(status_code=404, detail="Group not found in this run")
        run_id, group_key = body.run_id, body.group_key
    else:
        raise HTTPException(status_code=422, detail="Comment on a review item or a group")
    comment = VisualReviewComment(
        project_id=project_id, test_run_id=run_id, comparison_id=body.comparison_id,
        group_key=group_key, author_id=principal.user.id, body=text)
    session.add(comment)
    await session.flush()
    await audit.record(
        session, entity_type="visual_review_comment", entity_id=comment.id,
        action="visual_review_comment.create", principal=principal, user_id=principal.user.id,
        workspace_id=project.workspace_id,
        # The text itself stays out of the audit trail (it is in the row);
        # the trail records who commented where.
        changes={"project_id": project_id, "run_id": run_id,
                 "comparison_id": body.comparison_id, "group_key": group_key,
                 "length": len(text)})
    await session.commit()
    await session.refresh(comment)
    return _comment_out(comment, {principal.user.id: principal.user})


@router.post("/projects/{project_id}/visual-review/comments/{comment_id}/resolve")
async def resolve_comment(
    project_id: int,
    comment_id: int,
    body: CommentResolve,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await _project_for(project_id, principal, session, min_role="editor")
    comment = await session.get(VisualReviewComment, comment_id)
    if not comment or comment.project_id != project_id:
        raise HTTPException(status_code=404, detail="Comment not found")
    comment.resolved = body.resolved
    comment.resolved_by_id = principal.user.id if body.resolved else None
    comment.resolved_at = datetime.utcnow() if body.resolved else None
    session.add(comment)
    await audit.record(
        session, entity_type="visual_review_comment", entity_id=comment.id,
        action="visual_review_comment.resolve" if body.resolved else "visual_review_comment.reopen",
        principal=principal, user_id=principal.user.id, workspace_id=project.workspace_id,
        changes={"project_id": project_id, "run_id": comment.test_run_id})
    await session.commit()
    await session.refresh(comment)
    users = await _users([comment.author_id], session)
    return _comment_out(comment, users)
