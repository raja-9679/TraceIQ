"""Visual baseline CRUD — Phase B scaffold.

Endpoints exist so baselines can be uploaded/listed/deleted; the actual
perceptual diff that consumes them lives in the execution-engine worker
and is not yet wired (see SCOPE_NOTES.md).

Surface:
    POST   /api/visual-baselines                   create
    GET    /api/visual-baselines?test_case_id=&step_id=
    DELETE /api/visual-baselines/{id}
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
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
    dest_key = f"baselines/{body.test_case_id}/{body.step_id}/{body.browser}-{body.device or 'default'}.png"
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
    if baseline:
        baseline.image_url = dest_key
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
