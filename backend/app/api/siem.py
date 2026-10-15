"""SIEM streaming: connectivity test and outbox status (WP7).

Configuration itself is ordinary instance settings (group `siem`), edited on
Settings → Instance (Admin). These two endpoints are what that card needs on
top: "does it actually arrive", and "is anything stuck".
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.core.database import get_session
from app.core.net_guard import UnsafeUrlError
from app.models import User
from app.services import audit, siem

router = APIRouter()


@router.post("/admin/siem/test")
async def send_test_event(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    """Send one test event straight to the saved sink (not via the outbox, so the
    answer is immediate and the error is the sink's own)."""
    try:
        cfg = siem.load_config()
    except siem.SiemConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if cfg is None:
        raise HTTPException(status_code=400, detail="SIEM streaming is off — enable it and save first")
    try:
        await siem.check_destination(cfg)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Destination refused: {exc}")
    event = siem.test_event(requested_by=admin.email)
    try:
        await asyncio.to_thread(siem.send, cfg, [event])
    except siem.SiemDeliveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await audit.record(session, entity_type="siem", entity_id=0, action="test_event",
                       user_id=admin.id, request=request,
                       changes={"sink_type": cfg.sink_type})
    await session.commit()
    return {"ok": True, "sink_type": cfg.sink_type, "event": event}


@router.get("/admin/siem/status")
async def siem_status(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    from app.tasks.security_ops_tasks import outbox_status
    try:
        cfg = siem.load_config()
        config_error = None
    except siem.SiemConfigError as exc:
        cfg, config_error = None, str(exc)
    status = await session.run_sync(outbox_status)
    return {"enabled": cfg is not None or config_error is not None,
            "sink_type": cfg.sink_type if cfg else None,
            "config_error": config_error, **status}
