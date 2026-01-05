from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Project, Workspace, UserWorkspace, ProjectReadWithAccess, TeamProjectAccess, UserTeam, UserProjectAccess, Tenant
from app.services.workspace_service import workspace_service
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service
from pydantic import BaseModel

router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    workspace_id: int

class AccessUpdate(BaseModel):
    access_level: str

@router.post("/projects", response_model=Project)
async def create_project(project_in: ProjectCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check workspace access
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == project_in.workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="User is not a member of this workspace")
        
    return await workspace_service.create_project(
        name=project_in.name, 
        workspace_id=project_in.workspace_id, 
        creator_id=current_user.id, 
        session=session, 
        description=project_in.description
    )

@router.get("/projects", response_model=List[ProjectReadWithAccess])
async def list_projects(workspace_id: Optional[int] = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Projects accessible via Workspace Admin, Tenant Owner, Teams, or Direct access
    
    # 1. Workspace Admin Access: All projects in workspaces where user is admin
    ws_admin_stmt = select(Project.id).join(UserWorkspace, UserWorkspace.workspace_id == Project.workspace_id).where(
        UserWorkspace.user_id == current_user.id,
        UserWorkspace.role == "admin"
    )
    
    # 2. Tenant Owner Access: All projects in workspaces belonging to user's tenants
    tenant_owner_stmt = select(Project.id).join(Workspace).join(Tenant).where(Tenant.owner_id == current_user.id)
    
    # 2.5 Tenant Admin Access (Role-based)
    from app.models import UserSystemRole, Role
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

    # 3. Team & Direct Access (Unchanged)
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    
    combined_query = select(Project).where(
        or_(
            Project.id.in_(ws_admin_stmt),
            Project.id.in_(tenant_owner_stmt),
            Project.id.in_(tenant_admin_stmt),
            Project.id.in_(team_stmt),
            Project.id.in_(user_stmt)
        )
    )
    
    if workspace_id:
        combined_query = combined_query.where(Project.workspace_id == workspace_id)
        
    result = await session.exec(combined_query)
    projects = result.all()
    
    resp_projects = []
    for p in projects:
        role = await access_service.get_project_role(current_user.id, p.id, session)
        pr = ProjectReadWithAccess.model_validate(p)
        pr.access_level = role
        resp_projects.append(pr)
        
    return resp_projects

@router.get("/projects/{project_id}/teams")
async def get_project_teams(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user has context access to the project
    role = await access_service.get_project_role(current_user.id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
        
    return await workspace_service.get_project_teams(project_id, session)

@router.get("/projects/{project_id}/users")
async def get_project_members(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user has context access to the project
    role = await access_service.get_project_role(current_user.id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
        
    return await workspace_service.get_project_members(project_id, session)

@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user has permission to delete project
    allowed = await rbac_service.has_permission(session, current_user.id, "project:delete", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to delete project")
    
    await workspace_service.delete_project(project_id, session)
    return {"status": "success"}

@router.delete("/projects/{project_id}/teams/{team_id}")
async def unlink_team_from_project(project_id: int, team_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission to update project (manage access)
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    success = await workspace_service.unlink_team_from_project(team_id, project_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"status": "success"}

@router.delete("/projects/{project_id}/users/{user_id}")
async def remove_user_project_access(project_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    success = await workspace_service.remove_user_project_access(user_id, project_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="Direct access not found")
    return {"status": "success"}

@router.post("/projects/{project_id}/teams/{team_id}")
async def add_team_to_project(project_id: int, team_id: int, access: AccessUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    await workspace_service.link_team_to_project(team_id, project_id, access.access_level, session)
    return {"status": "success"}

@router.post("/projects/{project_id}/users/{user_id}")
async def add_user_to_project(project_id: int, user_id: int, access: AccessUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    await workspace_service.add_user_project_access(user_id, project_id, access.access_level, session)
    return {"status": "success"}

class ProjectInvite(BaseModel):
    email: str
    role: str = "viewer"  # admin, editor, viewer

@router.post("/projects/{project_id}/invitations")
async def invite_to_project(project_id: int, invite: ProjectInvite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # 1. Check Permission (Project Admin) - Use manage_access permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:manage_access", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied: project:manage_access")
    
    # 2. Get Org ID from Project
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # 3. Restriction: Project Admin (who is not Workspace Admin) can ONLY add existing Workspace Members
    # Check if user has Workspace Admin privileges
    can_manage_ws_users = await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=project.workspace_id)
    
    if not can_manage_ws_users:
        # User is likely just Project Admin. STRICT CHECK.
        # Check if email exists & is in Workspace
        stmt = (
            select(User)
            .join(UserWorkspace)
            .where(User.email == invite.email, UserWorkspace.workspace_id == project.workspace_id)
        )
        existing_member = (await session.exec(stmt)).first()
        if not existing_member:
            raise HTTPException(
                status_code=403, 
                detail="Permission denied: Project Admins can only add existing Workspace Members."
            )

    # 4. Call Workspace Service
    # Note: We invite to Workspace as 'Member' by default if they are new.
    return await workspace_service.invite_user_to_workspace(
        email=invite.email, 
        workspace_id=project.workspace_id, 
        invited_by_id=current_user.id, 
        role="member", 
        session=session,
        project_id=project_id,
        project_role=invite.role
    )
