"""Visual baseline CRUD — Phase B scaffold.

Endpoints exist so baselines can be uploaded/listed/deleted; the actual
perceptual diff that consumes them lives in the execution-engine worker
and is not yet wired (see SCOPE_NOTES.md).

Surface:
    POST   /api/visual-baselines                   create
    GET    /api/visual-baselines?test_case_id=&step_id=
    DELETE /api/visual-baselines/{id}
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.config import settings
from app.core.database import get_session
from app.models import TestCase, VisualBaseline, VisualBaselineRead
from app.services.access_service import access_service
from pydantic import BaseModel

router = APIRouter()


class VisualBaselineCreate(BaseModel):
    test_case_id: int
    step_id: str
    image_url: str
    browser: str = "chromium"
    device: Optional[str] = None
    viewport: Optional[str] = None
    tolerance: float = 0.01
    mask_regions: Optional[List[dict]] = None


def _resolve_image_url(image_url: str) -> str:
    """Bare object keys (e.g. promoted baselines under baselines/) are turned
    into presigned URLs so the engine and UI can fetch them directly; full
    URLs are passed through unchanged."""
    if image_url and not image_url.startswith(("http://", "https://")):
        try:
            from app.core.storage import minio_client
            return minio_client.get_presigned_url(image_url)
        except Exception:
            return image_url
    return image_url


def _to_read(b: VisualBaseline) -> VisualBaselineRead:
    return VisualBaselineRead(
        id=b.id,
        test_case_id=b.test_case_id,
        step_id=b.step_id,
        browser=b.browser,
        device=b.device,
        viewport=b.viewport,
        image_url=_resolve_image_url(b.image_url),
        tolerance=b.tolerance,
        created_at=b.created_at,
    )


@router.post("/visual-baselines", response_model=VisualBaselineRead)
async def create_baseline(
    body: VisualBaselineCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> VisualBaselineRead:
    case = await session.get(TestCase, body.test_case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    # A bare key is presigned back to the caller (and used as a purge prefix),
    # so it must live in this case's own baseline folder; anything else in the
    # shared bucket may belong to another tenant. Promote is the normal way
    # to create a stored baseline; external images use a full URL.
    if (not body.image_url.startswith(("http://", "https://"))
            and not body.image_url.startswith(f"baselines/{body.test_case_id}/")):
        raise HTTPException(
            status_code=400,
            detail="image_url must be an http(s) URL or a key under "
                   f"baselines/{body.test_case_id}/ (use /visual-baselines/promote "
                   "to pin a run's screenshot)")

    baseline = VisualBaseline(
        test_case_id=body.test_case_id,
        step_id=body.step_id,
        image_url=body.image_url,
        browser=body.browser,
        device=body.device,
        viewport=body.viewport,
        tolerance=body.tolerance,
        mask_regions=body.mask_regions or [],
        created_by_id=principal.user.id,
    )
    session.add(baseline)
    await session.commit()
    await session.refresh(baseline)
    return _to_read(baseline)


@router.get("/visual-baselines", response_model=List[VisualBaselineRead])
async def list_baselines(
    test_case_id: Optional[int] = None,
    step_id: Optional[str] = None,
    project_id: Optional[int] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[VisualBaselineRead]:
    # A baseline read mints a presigned MinIO URL for the screenshot, so an
    # unscoped list would leak every tenant's images. Require a scope
    # (project_id or test_case_id) and verify the caller can access it.
    if project_id is None and test_case_id is None:
        raise HTTPException(
            status_code=422,
            detail="Provide project_id or test_case_id to scope the query")
    if test_case_id is not None:
        case = await session.get(TestCase, test_case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Test case not found")
        if not await access_service.has_project_access(
            principal.user.id, case.project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")
    if project_id is not None:
        if not await access_service.has_project_access(
            principal.user.id, project_id, session):
            raise HTTPException(status_code=403, detail="Access denied")

    query = select(VisualBaseline)
    if test_case_id is not None:
        query = query.where(VisualBaseline.test_case_id == test_case_id)
    if step_id is not None:
        query = query.where(VisualBaseline.step_id == step_id)
    if project_id is not None:
        # Scope to the project by joining through TestCase.
        query = query.join(TestCase, TestCase.id == VisualBaseline.test_case_id).where(
            TestCase.project_id == project_id)
    res = await session.exec(query.order_by(VisualBaseline.created_at.desc()))
    return [_to_read(b) for b in res.all()]


class PromoteBaselineRequest(BaseModel):
    test_case_id: int
    step_id: str
    run_id: int
    browser: str = "chromium"
    device: Optional[str] = None
    tolerance: float = 0.01
    mask_regions: Optional[List[dict]] = None


@router.get("/internal/visual-baselines/resolve")
async def internal_resolve_baseline(
    test_case_id: int,
    step_id: str,
    browser: str = "chromium",
    device: Optional[str] = None,
    x_worker_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Worker-facing baseline lookup for `expect-visual-match`.

    Authenticated by the worker shared secret (WEBHOOK_SECRET, falling back
    to SECRET_KEY) instead of a user JWT, and returns the baseline image as a
    presigned URL against the *internal* MinIO endpoint — the public
    localhost URL is unreachable from inside worker containers."""
    from app.core.storage import minio_client

    import hmac
    expected = settings.WEBHOOK_SECRET or settings.SECRET_KEY
    if not x_worker_secret or not hmac.compare_digest(x_worker_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid worker secret")

    res = await session.exec(
        select(VisualBaseline).where(
            VisualBaseline.test_case_id == test_case_id,
            VisualBaseline.step_id == step_id,
        )
    )
    baselines = list(res.all())
    if not baselines:
        raise HTTPException(status_code=404, detail="No baseline for this step")
    # Prefer exact browser+device match, then browser match, then first.
    exact = next((b for b in baselines if b.browser == browser and b.device == device), None)
    if exact is None and device and device.startswith("vp:"):
        # A sweep variant (viewport/locale/scheme, see frontend_matrix.
        # baseline_key) never falls back: comparing a 375px screenshot with
        # the desktop baseline fails for the wrong reason. No baseline here =
        # capture-only, so the first run of a variant seeds its candidate.
        raise HTTPException(status_code=404, detail="No baseline for this step and variant")
    baseline = exact or next((b for b in baselines if b.browser == browser), baselines[0])
    image_url = baseline.image_url
    if image_url and not image_url.startswith("http"):
        image_url = minio_client.get_internal_presigned_url(image_url)
    return {
        "id": baseline.id,
        "test_case_id": baseline.test_case_id,
        "step_id": baseline.step_id,
        "browser": baseline.browser,
        "device": baseline.device,
        "image_url": image_url,
        "tolerance": baseline.tolerance,
        "mask_regions": baseline.mask_regions or [],
    }


@router.post("/visual-baselines/promote", response_model=VisualBaselineRead)
async def promote_baseline(
    body: PromoteBaselineRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> VisualBaselineRead:
    """Promote a run's candidate screenshot into a durable baseline.

    The worker writes the candidate at runs/{run_id}/screenshots/visual-{step_id}.png.
    This copies it (server-side) to a stable baselines/ key so the baseline
    survives artifact cleanup and presigned-URL expiry, then upserts the
    VisualBaseline row for (test_case, step, browser, device)."""
    from app.core.storage import minio_client

    case = await session.get(TestCase, body.test_case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    source_key = f"runs/{body.run_id}/screenshots/visual-{body.step_id}.png"
    if not minio_client.object_exists(source_key):
        # Workers prefix candidate uploads with the job id
        # (runs/{run_id}/screenshots/{job_id}-visual-{step_id}.png).
        source_key = minio_client.find_object(
            f"runs/{body.run_id}/screenshots/", f"visual-{body.step_id}.png")
    if not source_key:
        raise HTTPException(
            status_code=404,
            detail=f"No candidate screenshot for step {body.step_id} in run {body.run_id}; "
                   "run the case with an expect-visual-match step first")
    # Versioned key: the previous baseline object is kept so the change can
    # be rolled back (it used to be overwritten in place).
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    dest_key = (f"baselines/{body.test_case_id}/{body.step_id}/"
                f"{body.browser}-{body.device or 'default'}-{stamp}.png")
    minio_client.copy_object(source_key, dest_key)

    res = await session.exec(
        select(VisualBaseline).where(
            VisualBaseline.test_case_id == body.test_case_id,
            VisualBaseline.step_id == body.step_id,
            VisualBaseline.browser == body.browser,
            VisualBaseline.device == body.device,
        )
    )
    baseline = res.first()
    previous_image = baseline.image_url if baseline else None
    if baseline:
        baseline.image_url = dest_key
        baseline.updated_at = datetime.utcnow()
        baseline.tolerance = body.tolerance
        if body.mask_regions is not None:
            baseline.mask_regions = body.mask_regions
    else:
        baseline = VisualBaseline(
            test_case_id=body.test_case_id,
            step_id=body.step_id,
            image_url=dest_key,
            browser=body.browser,
            device=body.device,
            tolerance=body.tolerance,
            mask_regions=body.mask_regions or [],
            created_by_id=principal.user.id,
        )
    session.add(baseline)
    await session.flush()
    await _audit(session, case, baseline, "visual_baseline.accept", principal, {
        "image": dest_key, "previous_image": previous_image,
        "run_id": body.run_id, "step_id": body.step_id,
    })
    await session.commit()
    await session.refresh(baseline)
    return _to_read(baseline)


class BaselineDecision(BaseModel):
    run_id: Optional[int] = None
    note: Optional[str] = None


class BaselineRollback(BaseModel):
    image: str


async def _baseline_for_editor(baseline_id: int, principal: AuthPrincipal,
                               session: AsyncSession):
    baseline = await session.get(VisualBaseline, baseline_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    case = await session.get(TestCase, baseline.test_case_id)
    if not case or not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    return baseline, case


async def _audit(session, case, baseline, action, principal, changes):
    from app.models import Project
    from app.services import audit
    project = await session.get(Project, case.project_id) if case.project_id else None
    await audit.record(
        session, entity_type="visual_baseline", entity_id=baseline.id,
        action=action, principal=principal, user_id=principal.user.id,
        workspace_id=project.workspace_id if project else None,
        changes={**changes, "test_case_id": baseline.test_case_id},
    )


@router.post("/visual-baselines/{baseline_id}/keep")
async def keep_baseline(
    baseline_id: int,
    body: BaselineDecision,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Record that a reviewer looked at a run's visual change and kept the
    current baseline (i.e. judged the change a regression). Nothing about the
    baseline changes; the decision is what gets recorded."""
    baseline, case = await _baseline_for_editor(baseline_id, principal, session)
    await _audit(session, case, baseline, "visual_baseline.keep", principal, {
        "image": baseline.image_url, "run_id": body.run_id,
        "note": (body.note or "")[:500] or None,
    })
    await session.commit()
    return {"status": "kept", "id": baseline_id}


@router.get("/visual-baselines/{baseline_id}/history")
async def baseline_history(
    baseline_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Accept / keep / rollback decisions for this baseline, newest first,
    read from the audit trail (which is append-only and hash-chained)."""
    baseline = await session.get(VisualBaseline, baseline_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    case = await session.get(TestCase, baseline.test_case_id)
    if not case or not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role="viewer"
    ):
        raise HTTPException(status_code=403, detail="Access denied")
    rows = await _history_rows(session, baseline_id)
    return [{
        "action": r.action.removeprefix("visual_baseline."),
        "at": r.timestamp,
        "user_id": r.user_id,
        "actor_label": r.actor_label,
        "run_id": (r.changes or {}).get("run_id"),
        "image": (r.changes or {}).get("image"),
        "previous_image": (r.changes or {}).get("previous_image"),
        "is_current": (r.changes or {}).get("image") == baseline.image_url,
    } for r in rows]


async def _history_rows(session, baseline_id: int):
    from app.models import AuditLog
    res = await session.exec(
        select(AuditLog).where(
            AuditLog.entity_type == "visual_baseline",
            AuditLog.entity_id == baseline_id,
        ).order_by(AuditLog.id.desc()).limit(100))
    return res.all()


@router.post("/visual-baselines/{baseline_id}/rollback", response_model=VisualBaselineRead)
async def rollback_baseline(
    baseline_id: int,
    body: BaselineRollback,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> VisualBaselineRead:
    """Re-pin an earlier version of this baseline.

    Only an image this baseline has actually used (per its audit history) is
    accepted — otherwise the endpoint would let an editor point a baseline at
    any object key in the shared bucket, including another tenant's."""
    baseline, case = await _baseline_for_editor(baseline_id, principal, session)
    known = set()
    for r in await _history_rows(session, baseline_id):
        for key in ("image", "previous_image"):
            v = (r.changes or {}).get(key)
            if v:
                known.add(v)
    own_folder = f"baselines/{baseline.test_case_id}/"
    if body.image not in known or not (
            body.image.startswith(own_folder)
            or body.image.startswith(("http://", "https://"))):
        raise HTTPException(status_code=400,
                            detail="That image is not a previous version of this baseline")
    if body.image == baseline.image_url:
        raise HTTPException(status_code=409, detail="That version is already the baseline")
    from app.core.storage import minio_client
    if (not body.image.startswith(("http://", "https://"))
            and not minio_client.object_exists(body.image)):
        raise HTTPException(status_code=410,
                            detail="That version's image no longer exists in storage")
    previous_image = baseline.image_url
    baseline.image_url = body.image
    baseline.updated_at = datetime.utcnow()
    session.add(baseline)
    await _audit(session, case, baseline, "visual_baseline.rollback", principal, {
        "image": body.image, "previous_image": previous_image,
    })
    await session.commit()
    await session.refresh(baseline)
    return _to_read(baseline)


@router.delete("/visual-baselines/{baseline_id}")
async def delete_baseline(
    baseline_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    baseline = await session.get(VisualBaseline, baseline_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    case = await session.get(TestCase, baseline.test_case_id)
    if case and not await access_service.has_project_access(
        principal.user.id, case.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    await session.delete(baseline)
    await session.commit()
    return {"status": "deleted", "id": baseline_id}
