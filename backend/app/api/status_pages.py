"""Public status pages — a shareable uptime view generated from a project's
monitors. Config endpoints are authenticated; the page itself
(`GET /api/status/{slug}`) is public by design and exposes only monitor
names, up/down state, and uptime percentages (never target URLs, run
artifacts, or error details)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.endpoints.schedules import _build_monitor_status
from app.core.auth import get_current_user
from app.core.database import get_session
from app.models import Project, StatusPage, StatusPageRead, TestSchedule, User
from app.services.access_service import access_service

router = APIRouter()


@router.get("/projects/{project_id}/status-page", response_model=StatusPageRead)
async def get_status_page_config(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    page = (await session.exec(select(StatusPage).where(
        StatusPage.project_id == project_id))).first()
    if not page:
        raise HTTPException(status_code=404, detail="No status page configured for this project")
    return page


@router.put("/projects/{project_id}/status-page", response_model=StatusPageRead)
async def upsert_status_page(
    project_id: int,
    body: dict = Body(default={}),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Create or update the project's status page. Pass `regenerate_slug=true`
    to rotate the public URL (invalidates the old link)."""
    if not await access_service.has_project_access(
            current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    page = (await session.exec(select(StatusPage).where(
        StatusPage.project_id == project_id))).first()
    if not page:
        page = StatusPage(project_id=project_id, slug=secrets.token_urlsafe(9))
    if body.get("regenerate_slug"):
        page.slug = secrets.token_urlsafe(9)
    if "title" in body:
        page.title = str(body["title"])[:120] or page.title
    if "enabled" in body:
        page.enabled = bool(body["enabled"])
    session.add(page)
    await session.commit()
    await session.refresh(page)
    return page


@router.get("/status/{slug}")
async def public_status_page(slug: str, session: AsyncSession = Depends(get_session)):
    """PUBLIC — no auth. Powers the shareable /status/{slug} page."""
    page = (await session.exec(select(StatusPage).where(StatusPage.slug == slug))).first()
    if not page or not page.enabled:
        raise HTTPException(status_code=404, detail="Status page not found")

    monitors = (await session.exec(select(TestSchedule).where(
        TestSchedule.project_id == page.project_id,
        TestSchedule.is_monitor == True,   # noqa: E712
        TestSchedule.is_active == True))).all()  # noqa: E712

    entries = []
    for m in monitors:
        st = await _build_monitor_status(m, session)
        entries.append({
            "name": m.name,
            "state": st.state,
            "uptime_24h": st.uptime_24h,
            "uptime_7d": st.uptime_7d,
            "last_checked_at": m.last_checked_at.isoformat() if m.last_checked_at else None,
            # Public surface stops here — no URLs, run ids, or error text.
        })

    overall = "operational"
    if any(e["state"] == "down" for e in entries):
        overall = "degraded" if any(e["state"] == "up" for e in entries) else "down"
    elif not entries:
        overall = "unknown"

    return {"title": page.title, "overall": overall, "monitors": entries}
