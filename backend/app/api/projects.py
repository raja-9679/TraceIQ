from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Project, Organization, UserOrganization, ProjectReadWithAccess, TeamProjectAccess, UserTeam, UserProjectAccess, Tenant
from app.services.org_service import org_service
from app.services.access_service import access_service
from app.services.rbac_service import rbac_service
from pydantic import BaseModel

router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    organization_id: int

class AccessUpdate(BaseModel):
    access_level: str

@router.post("/projects", response_model=Project)
async def create_project(project_in: ProjectCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check org access
    result = await session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id, UserOrganization.organization_id == project_in.organization_id)
    )
    if not result.first():
        raise HTTPException(status_code=403, detail="User is not a member of this organization")
        
    return await org_service.create_project(
        name=project_in.name, 
        org_id=project_in.organization_id, 
        creator_id=current_user.id, 
        session=session, 
        description=project_in.description
    )

@router.get("/projects", response_model=List[ProjectReadWithAccess])
async def list_projects(org_id: Optional[int] = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Projects accessible via Org Admin, Tenant Owner, Teams, or Direct access
    
    # 1. Org Admin Access: All projects in orgs where user is admin
    org_admin_stmt = select(Project.id).join(UserOrganization, UserOrganization.organization_id == Project.organization_id).where(
        UserOrganization.user_id == current_user.id,
        UserOrganization.role == "admin"
    )
    
    # 2. Tenant Owner Access: All projects in orgs belonging to user's tenants
    tenant_owner_stmt = select(Project.id).join(Organization).join(Tenant).where(Tenant.owner_id == current_user.id)

    # 3. Team & Direct Access (Unchanged)
    team_stmt = select(Project.id).join(TeamProjectAccess, TeamProjectAccess.project_id == Project.id).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id).where(UserTeam.user_id == current_user.id)
    user_stmt = select(Project.id).join(UserProjectAccess, UserProjectAccess.project_id == Project.id).where(UserProjectAccess.user_id == current_user.id)
    
    combined_query = select(Project).where(
        or_(
            Project.id.in_(org_admin_stmt),
            Project.id.in_(tenant_owner_stmt),
            Project.id.in_(team_stmt),
            Project.id.in_(user_stmt)
        )
    )
    
    if org_id:
        combined_query = combined_query.where(Project.organization_id == org_id)
        
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
        
    return await org_service.get_project_teams(project_id, session)

@router.get("/projects/{project_id}/users")
async def get_project_members(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user has context access to the project
    role = await access_service.get_project_role(current_user.id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
        
    return await org_service.get_project_members(project_id, session)

@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check if user has permission to delete project
    allowed = await rbac_service.has_permission(session, current_user.id, "project:delete", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to delete project")
    
    await org_service.delete_project(project_id, session)
    return {"status": "success"}

@router.delete("/projects/{project_id}/teams/{team_id}")
async def unlink_team_from_project(project_id: int, team_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission to update project (manage access)
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    success = await org_service.unlink_team_from_project(team_id, project_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"status": "success"}

@router.delete("/projects/{project_id}/users/{user_id}")
async def remove_user_project_access(project_id: int, user_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    success = await org_service.remove_user_project_access(user_id, project_id, session)
    if not success:
        raise HTTPException(status_code=404, detail="Direct access not found")
    return {"status": "success"}

@router.post("/projects/{project_id}/teams/{team_id}")
async def add_team_to_project(project_id: int, team_id: int, access: AccessUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    await org_service.link_team_to_project(team_id, project_id, access.access_level, session)
    return {"status": "success"}

@router.post("/projects/{project_id}/users/{user_id}")
async def add_user_to_project(project_id: int, user_id: int, access: AccessUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    # Check for permission
    allowed = await rbac_service.has_permission(session, current_user.id, "project:update", project_id=project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied to modify project access")
        
    await org_service.add_user_project_access(user_id, project_id, access.access_level, session)
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

    # 3. Restriction: Project Admin (who is not Org Admin) can ONLY add existing Org Members
    # Check if user has Org Admin privileges
    can_manage_org_users = await rbac_service.has_permission(session, current_user.id, "org:manage_users", org_id=project.organization_id)
    
    if not can_manage_org_users:
        # User is likely just Project Admin. STRICT CHECK.
        # Check if email exists & is in Org
        stmt = (
            select(User)
            .join(UserOrganization)
            .where(User.email == invite.email, UserOrganization.organization_id == project.organization_id)
        )
        existing_member = (await session.exec(stmt)).first()
        if not existing_member:
            raise HTTPException(
                status_code=403, 
                detail="Permission denied: Project Admins can only add existing Organization Members."
            )

    # 4. Call Org Service
    # Note: We invite to Org as 'Member' by default if they are new.
    return await org_service.invite_user_to_organization(
        email=invite.email, 
        org_id=project.organization_id, 
        invited_by_id=current_user.id, 
        role="member", 
        session=session,
        project_id=project_id,
        project_role=invite.role
    )
