from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Workspace, UserWorkspace, Team, UserTeam, UserRead, Project, Tenant
from app.services.workspace_service import workspace_service
from pydantic import BaseModel

router = APIRouter()

class WorkspaceCreate(BaseModel):
    name: str
    description: Optional[str] = None

class TeamCreate(BaseModel):
    name: str
    initial_project_id: Optional[int] = None
    initial_access_level: Optional[str] = "editor"

class InviteMember(BaseModel):
    email: str

class ProjectAccess(BaseModel):
    access_level: str # admin, editor, viewer

class WorkspaceInvite(BaseModel):
    email: str
    role: str = "member"

from app.services.rbac_service import rbac_service

@router.post("/workspaces", response_model=Workspace)
async def create_workspace(ws_in: WorkspaceCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check permissions
    if not await rbac_service.has_permission(session, current_user.id, "tenant:create_workspace"):
         raise HTTPException(status_code=403, detail="Permission denied: tenant:create_workspace")
         
    # Check if user owns ANY tenant (legacy check, but RBAC covers it via Tenant Admin role)
    # Ideally pass tenant_id from context, but for now we rely on the permission check which 
    # verifies the user has the 'tenant:create_workspace' permission logic (System Role).
    
    return await workspace_service.create_workspace(name=ws_in.name, owner_id=current_user.id, session=session, description=ws_in.description)

@router.get("/workspaces", response_model=List[Workspace])
async def list_workspaces(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return await workspace_service.get_user_workspaces(current_user.id, session)

@router.post("/workspaces/{workspace_id}/teams", response_model=Team)
async def create_team(workspace_id: int, team_in: TeamCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "workspace:create_team", workspace_id=workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: workspace:create_team")
    
    team = Team(name=team_in.name, workspace_id=workspace_id)
    session.add(team)
    await session.flush() # Get team ID
    
    # Link to initial project if provided
    if team_in.initial_project_id:
        await workspace_service.link_team_to_project(
            team_id=team.id, 
            project_id=team_in.initial_project_id, 
            access_level=team_in.initial_access_level or "editor", 
            session=session
        )
    
    await session.commit()
    await session.refresh(team)
    return team

@router.get("/workspaces/{workspace_id}/teams", response_model=List[Team])
async def list_teams(workspace_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if member of workspace
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
    return await workspace_service.get_workspace_teams(workspace_id, session)

@router.post("/teams/{team_id}/users/invite")
async def invite_to_team(team_id: int, invite: InviteMember, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=team.workspace_id):
         raise HTTPException(status_code=403, detail="Permission denied: workspace:manage_users")
        
    success = await workspace_service.add_user_to_team_by_email(invite.email, team_id, session)
    if not success:
        # Create an invitation for a non-existent user
        from app.models import TeamInvitation
        invitation = TeamInvitation(
            email=invite.email,
            team_id=team_id,
            invited_by_id=current_user.id
        )
        session.add(invitation)
        await session.commit()
        return {"status": "invited", "message": "User not found, but an invitation has been created."}
        
    return {"status": "success", "message": "User added to team."}

@router.post("/teams/{team_id}/users/{user_id}")
async def add_user_to_team(team_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=team.workspace_id):
         raise HTTPException(status_code=403, detail="Permission denied: workspace:manage_users")
        
    ut = UserTeam(user_id=user_id, team_id=team_id)
    session.add(ut)
    await session.commit()
    return {"status": "success"}

@router.post("/projects/{project_id}/teams/{team_id}/access")
async def link_team_project(project_id: int, team_id: int, access: ProjectAccess, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "project:manage_access", project_id=project_id, workspace_id=project.workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: project:manage_access")
        
    await workspace_service.link_team_to_project(team_id, project_id, access.access_level, session)
    return {"status": "success"}

@router.get("/workspaces/{workspace_id}/users", response_model=List[UserRead])
async def list_workspace_members(workspace_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in workspace
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
    return await workspace_service.get_workspace_members(workspace_id, session)

from app.models import User, Workspace, UserWorkspace, Team, UserTeam, UserRead, Project, Tenant, UserReadDetailed
# ...

@router.get("/workspaces/{workspace_id}/members/detailed", response_model=List[UserReadDetailed])
async def list_workspace_members_detailed(workspace_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in workspace
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
    return await workspace_service.get_workspace_members_detailed(workspace_id, session, current_user.id)

@router.post("/workspaces/{workspace_id}/invitations")
async def invite_to_workspace(workspace_id: int, invite: WorkspaceInvite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: workspace:manage_users")
        
    return await workspace_service.invite_user_to_workspace(invite.email, workspace_id, current_user.id, invite.role, session)

@router.get("/workspaces/{workspace_id}/invitations")
async def list_workspace_invitations(workspace_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in workspace
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
    return await workspace_service.get_workspace_invitations(workspace_id, session)

@router.delete("/teams/{team_id}/users/{user_id}")
async def remove_from_team(team_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    is_admin = await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=team.workspace_id)
    
    if not is_admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Permission denied: workspace:manage_users")
        
    success = await workspace_service.remove_user_from_team(team_id, user_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="User not found in team")
    return {"status": "success"}

@router.get("/teams/{team_id}/users", response_model=List[UserRead])
async def list_team_members(team_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Check workspace membership
    result = await session.exec(
        select(UserWorkspace)
        .where(UserWorkspace.user_id == current_user.id, UserWorkspace.workspace_id == team.workspace_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
    result = await session.exec(
        select(User)
        .join(UserTeam)
        .where(UserTeam.team_id == team_id)
    )
    return result.all()

@router.delete("/teams/{team_id}")
async def delete_team(team_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "workspace:create_team", workspace_id=team.workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: workspace:create_team")
        
    await workspace_service.delete_team(team_id, session)
    return {"status": "success"}

@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "workspace:delete_workspace", workspace_id=workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: workspace:delete_workspace")
        
    await workspace_service.delete_workspace(workspace_id, session)
    return {"status": "success"}

@router.delete("/workspaces/{workspace_id}/users/{user_id}")
async def remove_user_from_workspace(workspace_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "workspace:manage_users", workspace_id=workspace_id):
        raise HTTPException(status_code=403, detail="Permission denied: workspace:manage_users")
    
    success = await workspace_service.remove_user_from_workspace(workspace_id, user_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="User not found in workspace")
        
    return {"status": "success"}

from app.models import Role

@router.get("/roles", response_model=List[Role])
async def list_roles(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    """
    List all available roles.
    """
    roles = await session.exec(select(Role))
    return roles.all()
