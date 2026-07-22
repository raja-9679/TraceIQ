"""Scheduled-report configuration API (PLATFORM_VISION.md §5, item 4)."""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Body
from croniter import croniter
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user
from app.services.access_service import access_service
from app.models import (
    User, ReportSchedule, ReportScheduleCreate, ReportScheduleUpdate, ReportScheduleRead,
)

router = APIRouter()


@router.post("/projects/{project_id}/report-schedules", response_model=ReportScheduleRead)
async def create_report_schedule(project_id: int, body: ReportScheduleCreate,
                                 session: AsyncSession = Depends(get_session),
                                 current_user: User = Depends(get_current_user)):
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    if not croniter.is_valid(body.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")
    sched = ReportSchedule(
        project_id=project_id, name=body.name, cron_expression=body.cron_expression,
        window_days=body.window_days, channels=body.channels, recipients=body.recipients,
        is_active=body.is_active, created_by_id=current_user.id,
        next_run_at=croniter(body.cron_expression, datetime.utcnow()).get_next(datetime))
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return sched


@router.get("/projects/{project_id}/report-schedules", response_model=List[ReportScheduleRead])
async def list_report_schedules(project_id: int, session: AsyncSession = Depends(get_session),
                                current_user: User = Depends(get_current_user)):
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return (await session.exec(
        select(ReportSchedule).where(ReportSchedule.project_id == project_id))).all()


@router.patch("/report-schedules/{schedule_id}", response_model=ReportScheduleRead)
async def update_report_schedule(schedule_id: int, body: ReportScheduleUpdate,
                                 session: AsyncSession = Depends(get_session),
                                 current_user: User = Depends(get_current_user)):
    sched = await session.get(ReportSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Report schedule not found")
    if not await access_service.has_project_access(current_user.id, sched.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    data = body.model_dump(exclude_unset=True)
    if "cron_expression" in data:
        if not croniter.is_valid(data["cron_expression"]):
            raise HTTPException(status_code=400, detail="Invalid cron expression")
        sched.next_run_at = croniter(data["cron_expression"], datetime.utcnow()).get_next(datetime)
    for k, v in data.items():
        setattr(sched, k, v)
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return sched


@router.delete("/report-schedules/{schedule_id}")
async def delete_report_schedule(schedule_id: int, session: AsyncSession = Depends(get_session),
                                 current_user: User = Depends(get_current_user)):
    sched = await session.get(ReportSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Report schedule not found")
    if not await access_service.has_project_access(current_user.id, sched.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    await session.delete(sched)
    await session.commit()
    return {"status": "deleted"}


@router.post("/report-schedules/{schedule_id}/send-now")
async def send_report_now_endpoint(schedule_id: int, session: AsyncSession = Depends(get_session),
                                   current_user: User = Depends(get_current_user)):
    sched = await session.get(ReportSchedule, schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Report schedule not found")
    if not await access_service.has_project_access(current_user.id, sched.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    from app.tasks.report_tasks import send_report_now
    send_report_now.delay(schedule_id)
    return {"status": "queued"}
