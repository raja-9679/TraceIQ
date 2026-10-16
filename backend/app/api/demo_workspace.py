"""Demo workspace endpoints (WP13c, decision C5).

GET    /api/demo-workspace   the caller's demo, or {"demo": null}
POST   /api/demo-workspace   seed one (409 if the caller already has one)
DELETE /api/demo-workspace   purge it (ordinary workspace purge, no name to type:
                             it holds generated data only)

Any signed-in user may create ONE demo for themselves; the demo belongs to them
(they are its Workspace Admin) and is invisible to everyone else unless they
invite people into it. The slowapi limit stops a create/delete loop from
hammering the database (each seed writes ~1,000 rows).
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import User
from app.services import demo_workspace as demo

router = APIRouter()


@router.get("/demo-workspace")
async def get_demo_workspace(current_user: User = Depends(get_current_user),
                             session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return {"demo": await demo.describe_demo(session, current_user.id)}


@router.post("/demo-workspace", status_code=201)
@limiter.limit("5/hour")
async def create_demo_workspace(request: Request, current_user: User = Depends(get_current_user),
                                session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    try:
        return {"demo": await demo.create_demo_workspace(session, current_user, request=request)}
    except demo.DemoExists as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail={
            "message": "You already have a demo workspace. Delete it before creating another.",
            "workspace_id": exc.workspace_id})


@router.delete("/demo-workspace")
async def delete_demo_workspace(request: Request, current_user: User = Depends(get_current_user),
                                session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    report = await demo.delete_demo_workspace(session, current_user, request=request)
    if report is None:
        raise HTTPException(status_code=404, detail="You have no demo workspace")
    return {"status": "deleted", **report}
