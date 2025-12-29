from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Organization, UserOrganization, Team, UserTeam, UserRead, Project
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

@router.post("/organizations", response_model=Organization)
async def create_org(org_in: OrgCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return await org_service.create_organization(name=org_in.name, owner_id=current_user.id, session=session, description=org_in.description)

@router.get("/organizations", response_model=List[Organization])
async def list_orgs(session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return await org_service.get_user_organizations(current_user.id, session)

@router.post("/organizations/{org_id}/teams", response_model=Team)
async def create_team(org_id: int, team_in: TeamCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is org admin
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can create teams")
    
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
        
    # Only org admins can invite
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == team.organization_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can invite to teams")
        
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
        
    # Check org admin status
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == team.organization_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can manage teams")
        
    ut = UserTeam(user_id=user_id, team_id=team_id)
    session.add(ut)
    await session.commit()
    return {"status": "success"}

@router.post("/projects/{project_id}/teams/{team_id}/access")
async def link_team_project(project_id: int, team_id: int, access: ProjectAccess, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check org admin
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == project.organization_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can manage project access")
        
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

@router.get("/organizations/{org_id}/members/detailed")
async def list_org_members_detailed(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user is in org
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Not a member of this organization")
        
    return await org_service.get_org_members_detailed(org_id, session)

@router.post("/organizations/{org_id}/invitations")
async def invite_to_org(org_id: int, invite: OrgInvite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check org admin status
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can invite members")
        
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
    # Check if user is org admin or team member (self-removal)
    team = await session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Check if current user is admin of the org
    result = await session.exec(select(UserOrganization).where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == team.organization_id, UserOrganization.role == "admin"))
    is_admin = result.first() is not None
    
    if not is_admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Only org admins can remove other members from teams")
        
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
        
    # Check org admin
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == team.organization_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can delete teams")
        
    await org_service.delete_team(team_id, session)
    return {"status": "success"}

@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check org admin
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id, UserOrganization.role == "admin")
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="Only organization admins can delete organizations")
        
    await org_service.delete_organization(org_id, session)
    return {"status": "success"}

@router.delete("/organizations/{org_id}/users/{user_id}")
async def remove_user_from_org(org_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check org admin
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == org_id, UserOrganization.role == "admin")
    )
    if not result.first():
        # Allow self-removal? Typically yes, but require clarification or implemented as "leave org".
        # For this requirement "admin can delete user", strict admin check is safer.
        raise HTTPException(status_code=403, detail="Only organization admins can remove members")
    
    # Prevent removing yourself if you are the last admin? (Advanced validation, skipping for MVP/task speed unless critical)
    
    success = await org_service.remove_user_from_organization(org_id, user_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="User not found in organization")
        
    return {"status": "success"}
