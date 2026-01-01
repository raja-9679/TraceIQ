from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Workspace, UserWorkspace, UserRead, Tenant, UserSystemRole, UserReadDetailed
from app.services.workspace_service import workspace_service
from app.services.rbac_service import rbac_service
from pydantic import BaseModel

router = APIRouter()

class WorkspaceAssignment(BaseModel):
    workspace_ids: List[int]
    role: str = "member" # 'admin' or 'member'

async def get_current_tenant_admin(
    session: AsyncSession = Depends(get_session), 
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure the current user has Tenant Admin permissions.
    """
    if not await rbac_service.has_permission(session, current_user.id, "tenant:manage_settings"):
        raise HTTPException(status_code=403, detail="Only Tenant Admins can access this resource")
    return current_user

@router.get("/users", response_model=List[UserReadDetailed])
async def list_all_users(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_admin)
):
    """
    List ALL users in the system (scoped to Tenant Admin's tenants).
    """
    # 1. Get Tenants user administers
    stmt = select(UserSystemRole.tenant_id).where(UserSystemRole.user_id == current_user.id)
    tenant_ids = (await session.exec(stmt)).all()
    
    if not tenant_ids:
        # Fallback ownership
        t_stmt = select(Tenant.id).where(Tenant.owner_id == current_user.id)
        tenant_ids = list((await session.exec(t_stmt)).all())

    return await workspace_service.get_tenant_users_detailed(tenant_ids, session)

@router.get("/workspaces", response_model=List[Workspace])
async def list_tenant_workspaces(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_admin)
):
    """
    List ALL workspaces in the tenant(s) administered by this user.
    """
    # 1. Get Tenants user administers
    stmt = select(UserSystemRole.tenant_id).where(UserSystemRole.user_id == current_user.id)
    tenant_ids = (await session.exec(stmt)).all()
    
    if not tenant_ids:
        # Fallback: Check if they are owner of any tenant directly (legacy/seed consistency)
        t_stmt = select(Tenant.id).where(Tenant.owner_id == current_user.id)
        implied_ids = (await session.exec(t_stmt)).all()
        tenant_ids = list(implied_ids)

    if not tenant_ids:
        return []

    # 2. Get Workspaces for these tenants
    if tenant_ids:
        workspaces = await session.exec(select(Workspace).where(Workspace.tenant_id.in_(tenant_ids))) # type: ignore
        return workspaces.all()
    return []

@router.post("/users/{user_id}/assignments")
async def assign_user_to_workspaces(
    user_id: int, 
    assignment: WorkspaceAssignment,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_admin)
):
    """
    Bulk assign a user to multiple workspaces.
    """
    target_user = await session.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get admin's tenant scope
    stmt = select(UserSystemRole.tenant_id).where(UserSystemRole.user_id == current_user.id)
    admin_tenant_ids = (await session.exec(stmt)).all()
    
    # Fallback to direct ownership
    if not admin_tenant_ids:
        t_stmt = select(Tenant.id).where(Tenant.owner_id == current_user.id)
        admin_tenant_ids = (await session.exec(t_stmt)).all()

    results = []
    
    for workspace_id in assignment.workspace_ids:
        # Get Workspace and check Tenant
        ws = await session.get(Workspace, workspace_id)
        if not ws:
            results.append({"workspace_id": workspace_id, "status": "error", "message": "Workspace not found"})
            continue
            
        # Security: Ensure Admin manages the tenant this workspace belongs to
        if not ws.tenant_id or ws.tenant_id not in admin_tenant_ids:
             results.append({"workspace_id": workspace_id, "status": "error", "message": "Workspace does not belong to your tenant"})
             continue

        # Add User to Workspace (using Service or Direct)
        # Note: 'role' here is string "admin" or "member". Service handles RBAC mapping.
        await workspace_service.invite_user_to_workspace(
            email=target_user.email,
            workspace_id=workspace_id, 
            invited_by_id=current_user.id,
            role=assignment.role, 
            session=session
        )
        # Auto-accept since admin is forcing assignment?
        # Service creates an invite if not strictly adding. 
        # But 'assign' implies immediate addition usually.
        # The service `invite_user_to_workspace` logic:
        # If user exists, it ADDS them directly (check workspace_service code).
        # Yes: "if user: ... uw = UserWorkspace(...)".
        
        results.append({"workspace_id": workspace_id, "status": "success"})
        
    return {"results": results}
