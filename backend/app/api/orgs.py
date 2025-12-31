from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Organization, UserOrganization, Team, UserTeam, UserRead, Project, Tenant
from app.services.org_service import org_service
from pydantic import BaseModel

router = APIRouter()

class OrgCreate(BaseModel):
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

class OrgInvite(BaseModel):
    email: str
    role: str = "member"

from app.services.rbac_service import rbac_service

@router.post("/organizations", response_model=Organization)
async def create_org(org_in: OrgCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check permissions
    if not await rbac_service.has_permission(session, current_user.id, "tenant:create_org"):
         raise HTTPException(status_code=403, detail="Permission denied: tenant:create_org")
         
    # Check if user owns ANY tenant (legacy check, but RBAC covers it via Tenant Admin role)
    # Ideally pass tenant_id from context, but for now we rely on the permission check which 
    # verifies the user has the 'tenant:create_org' permission logic (System Role).
    
    return await org_service.create_organization(name=org_in.name, owner_id=current_user.id, session=session, description=org_in.description)

@router.get("/organizations", response_model=List[Organization])
async def list_orgs(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return await org_service.get_user_organizations(current_user.id, session)

@router.post("/organizations/{org_id}/teams", response_model=Team)
async def create_team(org_id: int, team_in: TeamCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "org:create_team", org_id=org_id):
        raise HTTPException(status_code=403, detail="Permission denied: org:create_team")
    
    team = Team(name=team_in.name, organization_id=org_id)
    session.add(team)
    await session.flush() # Get team ID
    
    # Link to initial project if provided
    if team_in.initial_project_id:
        await org_service.link_team_to_project(
            team_id=team.id, 
            project_id=team_in.initial_project_id, 
            access_level=team_in.initial_access_level or "editor", 
            session=session
        )
    
    await session.commit()
    await session.refresh(team)
    return team

@router.get("/organizations/{org_id}/teams", response_model=List[Team])
async def list_teams(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if member of org
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
    return await org_service.get_org_teams(org_id, session)

@router.post("/teams/{team_id}/users/invite")
async def invite_to_team(team_id: int, invite: InviteMember, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=team.organization_id):
         raise HTTPException(status_code=403, detail="Permission denied: org:manage_users")
        
    success = await org_service.add_user_to_team_by_email(invite.email, team_id, session)
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
        
    if not await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=team.organization_id):
         raise HTTPException(status_code=403, detail="Permission denied: org:manage_users")
        
    ut = UserTeam(user_id=user_id, team_id=team_id)
    session.add(ut)
    await session.commit()
    return {"status": "success"}

@router.post("/projects/{project_id}/teams/{team_id}/access")
async def link_team_project(project_id: int, team_id: int, access: ProjectAccess, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    if not await rbac_service.has_permission(session, current_user.id, "project:manage_access", project_id=project_id, org_id=project.organization_id):
        raise HTTPException(status_code=403, detail="Permission denied: project:manage_access")
        
    await org_service.link_team_to_project(team_id, project_id, access.access_level, session)
    return {"status": "success"}

@router.get("/organizations/{org_id}/users", response_model=List[UserRead])
async def list_org_members(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in org
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
    return await org_service.get_org_members(org_id, session)

from app.models import User, Organization, UserOrganization, Team, UserTeam, UserRead, Project, Tenant, UserReadDetailed
# ...

@router.get("/organizations/{org_id}/members/detailed", response_model=List[UserReadDetailed])
async def list_org_members_detailed(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in org
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
    return await org_service.get_org_members_detailed(org_id, session, current_user.id)

@router.post("/organizations/{org_id}/invitations")
async def invite_to_org(org_id: int, invite: OrgInvite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=org_id):
        raise HTTPException(status_code=403, detail="Permission denied: org:manage_users")
        
    return await org_service.invite_user_to_organization(invite.email, org_id, current_user.id, invite.role, session)

@router.get("/organizations/{org_id}/invitations")
async def list_org_invitations(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in org
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
    return await org_service.get_org_invitations(org_id, session)

@router.delete("/teams/{team_id}/users/{user_id}")
async def remove_from_team(team_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    is_admin = await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=team.organization_id)
    
    if not is_admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Permission denied: org:manage_users")
        
    success = await org_service.remove_user_from_team(team_id, user_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="User not found in team")
    return {"status": "success"}

@router.get("/teams/{team_id}/users", response_model=List[UserRead])
async def list_team_members(team_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Check org membership
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == team.organization_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
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
        
    if not await rbac_service.has_permission(session, current_user.id, "org:create_team", org_id=team.organization_id):
        raise HTTPException(status_code=403, detail="Permission denied: org:create_team")
        
    await org_service.delete_team(team_id, session)
    return {"status": "success"}

@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "org:delete_org", org_id=org_id):
        raise HTTPException(status_code=403, detail="Permission denied: org:delete_org")
        
    await org_service.delete_organization(org_id, session)
    return {"status": "success"}

@router.delete("/organizations/{org_id}/users/{user_id}")
async def remove_user_from_org(org_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=org_id):
        raise HTTPException(status_code=403, detail="Permission denied: org:manage_users")
    
    success = await org_service.remove_user_from_organization(org_id, user_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="User not found in organization")
        
    return {"status": "success"}

from app.models import Role

@router.get("/roles", response_model=List[Role])
async def list_roles(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    """
    List all available roles.
    """
    roles = await session.exec(select(Role))
    return roles.all()
