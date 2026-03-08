from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_
from sqlalchemy.orm import selectinload
from app.core.database import get_session
from app.core.auth import get_current_user
from app.services.rbac_service import rbac_service
from app.models import (
    User, AuditLog, Project, UserWorkspace, UserTeam, UserProjectAccess, UserSystemRole, Role, Workspace, TeamProjectAccess,
    TestSchedule, TestScheduleRead, TestScheduleUpdate, TestScheduleBase, TestSuite, TestCase
)
from croniter import croniter

router = APIRouter()


@router.post("", response_model=TestScheduleRead)
async def create_schedule(
    schedule: TestScheduleBase,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Check project access
    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=schedule.project_id):
        raise HTTPException(status_code=403, detail="Permission denied to create schedules in this project")

    # Validate cron expression
    if not croniter.is_valid(schedule.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    # Set next_run_at based on cron
    now = datetime.utcnow()
    cron = croniter(schedule.cron_expression, now)
    schedule.next_run_at = cron.get_next(datetime)

    new_schedule = TestSchedule.model_validate(schedule)
    new_schedule.created_by_id = current_user.id
    new_schedule.updated_by_id = current_user.id

    session.add(new_schedule)
    await session.commit()
    await session.refresh(new_schedule)

    # Audit Log
    audit = AuditLog(
        entity_type="schedule",
        entity_id=new_schedule.id,
        action="create",
        user_id=current_user.id,
        changes=new_schedule.model_dump(mode='json')
    )
    session.add(audit)
    await session.commit()

    return new_schedule


@router.get("", response_model=List[TestScheduleRead])
async def list_schedules(
    project_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Filter by user access
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    
    tenant_admin_stmt = (
        select(Project.id)
        .join(Workspace, Workspace.id == Project.workspace_id)
        .join(UserSystemRole, UserSystemRole.tenant_id == Workspace.tenant_id)
        .where(
            UserSystemRole.user_id == current_user.id,
            UserSystemRole.role_id.in_(
                select(Role.id).where(Role.name == "Tenant Admin")
            )
        )
    )

    query = select(TestSchedule).where(
        or_(
            TestSchedule.project_id.in_(org_stmt),
            TestSchedule.project_id.in_(team_stmt),
            TestSchedule.project_id.in_(user_stmt),
            TestSchedule.project_id.in_(tenant_admin_stmt)
        )
    )
    
    if project_id:
        if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=project_id):
            raise HTTPException(status_code=403, detail="Access denied to this project")
        query = query.where(TestSchedule.project_id == project_id)
        
    result = await session.exec(query)
    return result.all()


@router.get("/{schedule_id}", response_model=TestScheduleRead)
async def get_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    schedule = await session.get(TestSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=schedule.project_id):
        raise HTTPException(status_code=403, detail="Access denied")
        
    return schedule


@router.put("/{schedule_id}", response_model=TestScheduleRead)
async def update_schedule(
    schedule_id: int,
    schedule_update: TestScheduleUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    db_schedule = await session.get(TestSchedule, schedule_id)
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=db_schedule.project_id):
        raise HTTPException(status_code=403, detail="Permission denied to modify schedules in this project")

    update_data = schedule_update.model_dump(exclude_unset=True)

    # Re-evaluate next run if cron was updated
    if "cron_expression" in update_data and update_data["cron_expression"] != db_schedule.cron_expression:
        if not croniter.is_valid(update_data["cron_expression"]):
            raise HTTPException(status_code=400, detail="Invalid cron expression")
        now = datetime.utcnow()
        cron = croniter(update_data["cron_expression"], now)
        db_schedule.next_run_at = cron.get_next(datetime)

    changes = {}
    for key, value in update_data.items():
        old_value = getattr(db_schedule, key)
        if old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(db_schedule, key, value)
            
    if changes:
        db_schedule.updated_by_id = current_user.id
        db_schedule.updated_at = datetime.utcnow()
        session.add(db_schedule)
        
        audit = AuditLog(
            entity_type="schedule", 
            entity_id=schedule_id, 
            action="update", 
            user_id=current_user.id, 
            changes=changes
        )
        session.add(audit)
        await session.commit()
        await session.refresh(db_schedule)
        
    return db_schedule


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    db_schedule = await session.get(TestSchedule, schedule_id)
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if not await rbac_service.has_permission(session, current_user.id, "project:create_suite", project_id=db_schedule.project_id):
        raise HTTPException(status_code=403, detail="Permission denied to delete schedules in this project")

    await session.delete(db_schedule)
    
    audit = AuditLog(
        entity_type="schedule", 
        entity_id=schedule_id, 
        action="delete", 
        user_id=current_user.id, 
        changes={}
    )
    session.add(audit)
    await session.commit()
    
    return {"status": "success", "message": f"Schedule {schedule_id} deleted successfully"}
