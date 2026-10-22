"""Access-review reports and campaigns (WP7).

    GET  /workspaces/{id}/access-review/report?format=json|csv|html   workspace admin
    GET  /admin/access-review/report?format=json|csv|html             instance admin
    POST /workspaces/{id}/access-reviews        start a workspace campaign
    POST /admin/access-reviews                  start an instance-wide campaign
    GET  /workspaces/{id}/access-reviews        list
    GET  /admin/access-reviews                  list
    GET  /access-reviews/{cid}                  campaign + items
    PATCH /access-reviews/{cid}/items/{iid}     keep / revoke + note
    POST /access-reviews/{cid}/sign-off         reviewer + timestamp → audit trail
    POST /access-reviews/{cid}/apply-revocations {confirm}   after sign-off only
    GET  /access-reviews/{cid}/export?format=csv|html

Every write is a human act: API keys are refused, because "a reviewer looked at
this" is the whole content of the evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.audit import _require_workspace_admin
from app.api.instance_settings import get_current_instance_admin
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    AccessReviewCampaign, AccessReviewItem, ApiKey, RefreshToken, User, Workspace,
)
from app.services import access_review, audit

router = APIRouter()

#: The HTML export is served from the API origin; it is escaped throughout, and
#: this makes sure that even a slip could not run script there.
_HTML_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}


def _human(principal: AuthPrincipal) -> User:
    if principal.api_key is not None:
        raise HTTPException(status_code=403,
                            detail="Access reviews must be carried out by a person, not an API key")
    return principal.user


async def _require_instance_admin(session: AsyncSession, user: User) -> None:
    await get_current_instance_admin(session=session, current_user=user)


async def _authorize_scope(session: AsyncSession, principal: AuthPrincipal,
                           workspace_id: Optional[int]) -> User:
    user = _human(principal)
    if workspace_id is None:
        await _require_instance_admin(session, user)
    else:
        await _require_workspace_admin(workspace_id, principal, session)
    return user


async def _load_campaign(session: AsyncSession, principal: AuthPrincipal,
                         campaign_id: int) -> tuple[AccessReviewCampaign, User]:
    campaign = await session.get(AccessReviewCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Access review not found")
    user = await _authorize_scope(session, principal, campaign.workspace_id)
    return campaign, user


def _report_response(report: Dict[str, Any], fmt: str, title: str):
    items = access_review.items_from_report(report)
    if fmt == "csv":
        return Response(access_review.render_csv(items), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="access-review.csv"'})
    if fmt == "html":
        return Response(access_review.render_html(title, items, meta={
            "scope": report["scope"], "workspace": report.get("workspace"),
            "generated_at": report["generated_at"]}), media_type="text/html",
            headers=_HTML_HEADERS)
    return access_review.jsonable(report)


@router.get("/workspaces/{workspace_id}/access-review/report")
async def workspace_access_report(
    workspace_id: int,
    format: Literal["json", "csv", "html"] = "json",
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    await _require_workspace_admin(workspace_id, principal, session)
    if not await session.get(Workspace, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    report = await access_review.build_report(session, workspace_id)
    return _report_response(report, format, f"Access report — {report['workspace']}")


@router.get("/admin/access-review/report")
async def instance_access_report(
    format: Literal["json", "csv", "html"] = "json",
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    report = await access_review.build_report(session, None)
    return _report_response(report, format, "Access report — whole instance")


class CampaignCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)


def _default_name(scope: str) -> str:
    now = datetime.utcnow()
    return f"{now.year} Q{(now.month - 1) // 3 + 1} {scope} access review"


async def _start(session: AsyncSession, request: Request, user: User,
                 workspace_id: Optional[int], name: Optional[str]) -> Dict[str, Any]:
    report = await access_review.build_report(session, workspace_id)
    campaign = AccessReviewCampaign(
        workspace_id=workspace_id, created_by_id=user.id,
        name=(name or "").strip() or _default_name("workspace" if workspace_id else "instance"))
    session.add(campaign)
    await session.flush()
    items = access_review.items_from_report(report)
    for it in items:
        session.add(AccessReviewItem(campaign_id=campaign.id, **it))
    await audit.record(session, entity_type="access_review", entity_id=campaign.id,
                       action="start", user_id=user.id, workspace_id=workspace_id,
                       request=request,
                       changes={"name": campaign.name, "scope": report["scope"],
                                "users": len(report["users"]), "api_keys": len(report["api_keys"])})
    await session.commit()
    return await _campaign_out(session, campaign)


@router.post("/workspaces/{workspace_id}/access-reviews", status_code=201)
async def start_workspace_review(
    workspace_id: int, body: CampaignCreate, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    user = await _authorize_scope(session, principal, workspace_id)
    if not await session.get(Workspace, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return await _start(session, request, user, workspace_id, body.name)


@router.post("/admin/access-reviews", status_code=201)
async def start_instance_review(
    body: CampaignCreate, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    user = await _authorize_scope(session, principal, None)
    return await _start(session, request, user, None, body.name)


async def _list(session: AsyncSession, workspace_id: Optional[int]) -> List[Dict[str, Any]]:
    q = select(AccessReviewCampaign)
    q = q.where(AccessReviewCampaign.workspace_id == workspace_id) if workspace_id is not None \
        else q.where(AccessReviewCampaign.workspace_id == None)  # noqa: E711
    campaigns = (await session.exec(q.order_by(AccessReviewCampaign.created_at.desc()))).all()
    return [await _campaign_out(session, c, with_items=False) for c in campaigns]


@router.get("/workspaces/{workspace_id}/access-reviews")
async def list_workspace_reviews(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    await _require_workspace_admin(workspace_id, principal, session)
    return await _list(session, workspace_id)


@router.get("/admin/access-reviews")
async def list_instance_reviews(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    return await _list(session, None)


async def _emails(session: AsyncSession, ids) -> Dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {u.id: u.email for u in (await session.exec(select(User).where(User.id.in_(ids)))).all()}


async def _campaign_out(session: AsyncSession, c: AccessReviewCampaign,
                        with_items: bool = True) -> Dict[str, Any]:
    items = (await session.exec(select(AccessReviewItem).where(
        AccessReviewItem.campaign_id == c.id).order_by(AccessReviewItem.subject_type.desc(),
                                                       AccessReviewItem.label))).all()
    people = await _emails(session, [c.created_by_id, c.signed_off_by_id,
                                     c.revocations_applied_by_id] + [i.decided_by_id for i in items])
    counts = {"total": len(items), "keep": 0, "revoke": 0, "undecided": 0, "applied": 0}
    for i in items:
        counts[i.decision or "undecided"] = counts.get(i.decision or "undecided", 0) + 1
        if i.applied_at:
            counts["applied"] += 1
    out = {
        "id": c.id, "workspace_id": c.workspace_id,
        "scope": "workspace" if c.workspace_id else "instance",
        "name": c.name, "status": c.status, "created_at": c.created_at,
        "created_by": people.get(c.created_by_id),
        "signed_off_at": c.signed_off_at, "signed_off_by": people.get(c.signed_off_by_id),
        "sign_off_note": c.sign_off_note,
        "revocations_applied_at": c.revocations_applied_at,
        "revocations_applied_by": people.get(c.revocations_applied_by_id),
        "counts": counts,
    }
    if with_items:
        out["items"] = [_item_out(i, people) for i in items]
    return out


def _item_out(i: AccessReviewItem, people: Dict[int, str]) -> Dict[str, Any]:
    return {"id": i.id, "subject_type": i.subject_type, "subject_id": i.subject_id,
            "label": i.label, "snapshot": i.snapshot, "decision": i.decision, "note": i.note,
            "decided_by": people.get(i.decided_by_id), "decided_at": i.decided_at,
            "applied_at": i.applied_at, "apply_result": i.apply_result}


@router.get("/access-reviews/{campaign_id}")
async def get_review(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    campaign, _ = await _load_campaign(session, principal, campaign_id)
    return await _campaign_out(session, campaign)


class Decision(BaseModel):
    decision: Literal["keep", "revoke"]
    note: Optional[str] = Field(default=None, max_length=2000)


@router.patch("/access-reviews/{campaign_id}/items/{item_id}")
async def decide_item(
    campaign_id: int, item_id: int, body: Decision, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    campaign, user = await _load_campaign(session, principal, campaign_id)
    if campaign.status != "open":
        raise HTTPException(status_code=409, detail="This review is signed off; decisions are final")
    item = await session.get(AccessReviewItem, item_id)
    if not item or item.campaign_id != campaign.id:
        raise HTTPException(status_code=404, detail="Item not found in this review")
    note = (body.note or "").strip() or None
    if body.decision == "revoke" and not note:
        raise HTTPException(status_code=422, detail="Say why access should be revoked")
    item.decision, item.note = body.decision, note
    item.decided_by_id, item.decided_at = user.id, datetime.utcnow()
    session.add(item)
    await audit.record(session, entity_type="access_review", entity_id=campaign.id,
                       action="decision", user_id=user.id, workspace_id=campaign.workspace_id,
                       request=request,
                       changes={"item_id": item.id, "subject_type": item.subject_type,
                                "subject_id": item.subject_id, "label": item.label,
                                "decision": body.decision, "note": note})
    await session.commit()
    people = await _emails(session, [item.decided_by_id])
    return _item_out(item, people)


class SignOff(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/access-reviews/{campaign_id}/sign-off")
async def sign_off(
    campaign_id: int, body: SignOff, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    campaign, user = await _load_campaign(session, principal, campaign_id)
    if campaign.status != "open":
        raise HTTPException(status_code=409, detail="Already signed off")
    items = (await session.exec(select(AccessReviewItem).where(
        AccessReviewItem.campaign_id == campaign.id))).all()
    undecided = [i.label for i in items if not i.decision]
    if undecided:
        raise HTTPException(status_code=422, detail={
            "message": f"{len(undecided)} item(s) still need a decision",
            "undecided": undecided[:50]})
    now = datetime.utcnow()
    campaign.status, campaign.signed_off_by_id, campaign.signed_off_at = "signed_off", user.id, now
    campaign.sign_off_note = (body.note or "").strip() or None
    session.add(campaign)
    await audit.record(session, entity_type="access_review", entity_id=campaign.id,
                       action="sign_off", user_id=user.id, workspace_id=campaign.workspace_id,
                       request=request,
                       changes={"name": campaign.name, "reviewer": user.email,
                                "signed_off_at": now.isoformat() + "Z",
                                "keep": sum(1 for i in items if i.decision == "keep"),
                                "revoke": sum(1 for i in items if i.decision == "revoke"),
                                "note": campaign.sign_off_note})
    await session.commit()
    return await _campaign_out(session, campaign)


class ApplyRequest(BaseModel):
    confirm: bool = False


async def _revoke_user(session: AsyncSession, campaign: AccessReviewCampaign, user_id: int) -> str:
    target = await session.get(User, user_id)
    if not target:
        return "skipped: user no longer exists"
    if campaign.workspace_id is None:
        # Instance-wide revoke = the account goes. Deactivation, not deletion:
        # their history stays attributable, and live sessions die with it.
        target.is_active = False
        target.is_instance_admin = False
        session.add(target)
        for t in (await session.exec(select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at == None))).all():  # noqa: E711
            t.revoked_at = datetime.utcnow()
            session.add(t)
        return "deactivated (sessions revoked)"
    # Same removal as DELETE /workspaces/{id}/users/{uid}: teams, project
    # grants, per-case overrides, the member's keys for this workspace.
    from app.services.workspace_service import workspace_service
    await workspace_service.revoke_workspace_access(campaign.workspace_id, user_id, session)
    return "removed from workspace, its teams and projects"


async def _revoke_key(session: AsyncSession, campaign: AccessReviewCampaign, key_id: int) -> str:
    key = await session.get(ApiKey, key_id)
    if not key:
        return "skipped: key no longer exists"
    if campaign.workspace_id is not None and key.workspace_id != campaign.workspace_id:
        return "skipped: key belongs to another workspace"
    if key.revoked_at:
        return "already revoked"
    key.revoked_at = datetime.utcnow()
    session.add(key)
    return "revoked"


@router.post("/access-reviews/{campaign_id}/apply-revocations")
async def apply_revocations(
    campaign_id: int, body: ApplyRequest, request: Request,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Without `confirm: true` this is a preview of exactly what would change."""
    campaign, user = await _load_campaign(session, principal, campaign_id)
    if campaign.status != "signed_off":
        raise HTTPException(status_code=409, detail="Sign the review off before applying revocations")
    pending = (await session.exec(select(AccessReviewItem).where(
        AccessReviewItem.campaign_id == campaign.id, AccessReviewItem.decision == "revoke",
        AccessReviewItem.applied_at == None))).all()  # noqa: E711
    plan = [{"item_id": i.id, "subject_type": i.subject_type, "subject_id": i.subject_id,
             "label": i.label, "note": i.note} for i in pending]
    if not body.confirm:
        return {"confirmed": False, "would_revoke": plan}

    results = []
    for item in pending:
        if item.subject_type == "user" and item.subject_id == user.id:
            outcome = "skipped: a reviewer cannot revoke their own access"
        elif item.subject_type == "user":
            outcome = await _revoke_user(session, campaign, item.subject_id)
        else:
            outcome = await _revoke_key(session, campaign, item.subject_id)
        if not outcome.startswith("skipped"):
            item.applied_at = datetime.utcnow()
        item.apply_result = outcome
        session.add(item)
        results.append({"item_id": item.id, "label": item.label, "result": outcome})
        await audit.record(
            session, entity_type="user" if item.subject_type == "user" else "api_key",
            entity_id=item.subject_id, action="access_review_revoke", user_id=user.id,
            workspace_id=campaign.workspace_id, request=request,
            changes={"campaign_id": campaign.id, "label": item.label, "result": outcome,
                     "note": item.note})
    campaign.revocations_applied_at = datetime.utcnow()
    campaign.revocations_applied_by_id = user.id
    session.add(campaign)
    await session.commit()
    return {"confirmed": True, "results": results}


@router.get("/access-reviews/{campaign_id}/export")
async def export_review(
    campaign_id: int,
    format: Literal["csv", "html"] = "csv",
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    campaign, _ = await _load_campaign(session, principal, campaign_id)
    out = await _campaign_out(session, campaign)
    if format == "csv":
        return Response(access_review.render_csv(out["items"]), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="access-review-{campaign.id}.csv"'})
    return Response(access_review.render_html(campaign.name, out["items"], meta={
        "scope": out["scope"], "status": campaign.status, "started": campaign.created_at,
        "started_by": out["created_by"], "signed_off_by": out["signed_off_by"],
        "signed_off_at": campaign.signed_off_at, "sign_off_note": campaign.sign_off_note,
        "revocations_applied_at": campaign.revocations_applied_at,
    }), media_type="text/html", headers=_HTML_HEADERS)
