from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.core.database import get_session
from app.core.auth import get_current_user
from app.services.rbac_service import rbac_service
from app.models import (
    User, AuditLog, Project, UserWorkspace, UserTeam, UserProjectAccess, UserSystemRole, Role, Workspace, TeamProjectAccess,
    TestSchedule, TestScheduleRead, TestScheduleUpdate, TestScheduleBase, TestSuite, TestCase,
    MonitorCheck, MonitorCheckRead, MonitorStatusRead, MonitorRegionStatus
)
from app.services.worker_fleet import InvalidRegion, normalize_region


def _clean_regions(regions: Optional[List[str]]) -> Optional[List[str]]:
    """Validate and de-duplicate a monitor's region list (None = unlabelled)."""
    if not regions:
        return None
    out: List[str] = []
    try:
        for r in regions:
            region = normalize_region(r)
            if region and region not in out:
                out.append(region)
    except InvalidRegion as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(out) > 20:
        raise HTTPException(status_code=400, detail="at most 20 regions per schedule")
    return out or None


async def _check_region_grants(session: AsyncSession, project_id: int,
                               regions: Optional[List[str]]) -> None:
    """Every region must be granted to the project's workspace (WP8): region
    workers can sit inside another customer's network."""
    if not regions:
        return
    from app.services.worker_fleet import RegionNotPermitted, check_region_access
    project = await session.get(Project, project_id)
    ws_id = project.workspace_id if project else None
    try:
        for region in regions:
            check_region_access(region, ws_id)
    except RegionNotPermitted as exc:
        raise HTTPException(status_code=403, detail=str(exc))


async def _region_statuses(schedule: TestSchedule, session: AsyncSession) -> List[MonitorRegionStatus]:
    """Per-region state for a multi-region monitor. The streak is computed
    from that region's own checks, so one flapping region cannot hide or
    inflate another's."""
    out: List[MonitorRegionStatus] = []
    states = schedule.region_states or {}
    for region in schedule.regions or []:
        rows = (await session.exec(
            select(MonitorCheck)
            .where(MonitorCheck.schedule_id == schedule.id, MonitorCheck.region == region)
            .order_by(MonitorCheck.checked_at.desc(), MonitorCheck.id.desc())
            .limit(50))).all()
        streak = 0
        for check in rows:
            if check.is_up:
                break
            streak += 1
        info = states.get(region) or {}
        out.append(MonitorRegionStatus(
            region=region, state=info.get("state") or "unknown",
            consecutive_failures=streak,
            last_checked_at=rows[0].checked_at if rows else None))
    return out
from croniter import croniter

router = APIRouter()


async def _uptime(session: AsyncSession, schedule_id: int, cutoff: datetime) -> Optional[float]:
    """Uptime % (passing / total checks) since `cutoff`; None when no checks."""
    total = (await session.exec(
        select(func.count()).select_from(MonitorCheck).where(
            MonitorCheck.schedule_id == schedule_id,
            MonitorCheck.checked_at >= cutoff))).one()
    if not total:
        return None
    up = (await session.exec(
        select(func.count()).select_from(MonitorCheck).where(
            MonitorCheck.schedule_id == schedule_id,
            MonitorCheck.checked_at >= cutoff,
            MonitorCheck.is_up == True))).one()  # noqa: E712
    return round(100.0 * up / total, 2)


async def _build_monitor_status(schedule: TestSchedule, session: AsyncSession) -> MonitorStatusRead:
    now = datetime.utcnow()
    recent = (await session.exec(
        select(MonitorCheck)
        .where(MonitorCheck.schedule_id == schedule.id)
        .order_by(MonitorCheck.checked_at.desc(), MonitorCheck.id.desc())
        .limit(20))).all()

    streak = 0
    for check in recent:
        if check.is_up:
            break
        streak += 1

    total = (await session.exec(
        select(func.count()).select_from(MonitorCheck).where(
            MonitorCheck.schedule_id == schedule.id))).one()

    return MonitorStatusRead(
        schedule_id=schedule.id,
        name=schedule.name,
        is_active=schedule.is_active,
        state=schedule.monitor_state or "unknown",
        consecutive_failures=streak,
        total_checks=int(total or 0),
        uptime_24h=await _uptime(session, schedule.id, now - timedelta(hours=24)),
        uptime_7d=await _uptime(session, schedule.id, now - timedelta(days=7)),
        last_checked_at=schedule.last_checked_at,
        recent_checks=[MonitorCheckRead.model_validate(c, from_attributes=True) for c in recent],
        regions=await _region_statuses(schedule, session),
    )


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

    schedule.regions = _clean_regions(schedule.regions)
    await _check_region_grants(session, schedule.project_id, schedule.regions)

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


@router.get("/monitors", response_model=List[MonitorStatusRead])
async def list_monitors(
    project_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Health snapshot for every monitor the user can see (optionally one project).

    Registered before `/{schedule_id}` so the literal path wins over the
    int path-param.
    """
    org_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(UserWorkspace.user_id == current_user.id)
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    tenant_admin_stmt = (
        select(Project.id)
        .join(Workspace, Workspace.id == Project.workspace_id)
        .join(UserSystemRole, UserSystemRole.tenant_id == Workspace.tenant_id)
        .where(
            UserSystemRole.user_id == current_user.id,
            UserSystemRole.role_id.in_(select(Role.id).where(Role.name == "Tenant Admin")),
        )
    )

    query = select(TestSchedule).where(
        TestSchedule.is_monitor == True,  # noqa: E712
        or_(
            TestSchedule.project_id.in_(org_stmt),
            TestSchedule.project_id.in_(team_stmt),
            TestSchedule.project_id.in_(user_stmt),
            TestSchedule.project_id.in_(tenant_admin_stmt),
        ),
    )
    if project_id:
        if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=project_id):
            raise HTTPException(status_code=403, detail="Access denied to this project")
        query = query.where(TestSchedule.project_id == project_id)

    monitors = (await session.exec(query)).all()
    return [await _build_monitor_status(m, session) for m in monitors]


@router.get("/{schedule_id}/monitor", response_model=MonitorStatusRead)
async def get_monitor_status(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    schedule = await session.get(TestSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if not await rbac_service.has_permission(session, current_user.id, "project:view", project_id=schedule.project_id):
        raise HTTPException(status_code=403, detail="Access denied")
    if not schedule.is_monitor:
        raise HTTPException(status_code=400, detail="Schedule is not a monitor")
    return await _build_monitor_status(schedule, session)


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
    if "regions" in update_data:
        update_data["regions"] = _clean_regions(update_data["regions"])
        await _check_region_grants(session, db_schedule.project_id, update_data["regions"])
        # Region states of regions no longer monitored would otherwise keep
        # the aggregate "down" forever.
        kept = set(update_data["regions"] or [])
        if db_schedule.region_states:
            db_schedule.region_states = {
                k: v for k, v in db_schedule.region_states.items() if k in kept} or None

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
