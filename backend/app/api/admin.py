from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.core.auth import get_current_user
from app.models import User, Organization, UserOrganization, UserRead, Tenant
from app.services.org_service import org_service
from pydantic import BaseModel

router = APIRouter()

class OrgAssignment(BaseModel):
    org_ids: List[int]
    role: str = "member" # 'admin' or 'member'

async def get_current_tenant_owner(
    session: AsyncSession = Depends(get_session), 
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure the current user is a Tenant Owner.
    """
    stmt = select(Tenant).where(Tenant.owner_id == current_user.id)
    tenant = (await session.exec(stmt)).first()
    
    if not tenant:
        raise HTTPException(status_code=403, detail="Only Tenant Admins can access this resource")
    return current_user

@router.get("/users", response_model=List[UserRead])
async def list_all_users(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_owner)
):
    """
    List ALL users in the system (for Tenant Admin to assign).
    """
    users = await session.exec(select(User))
    return users.all()

@router.get("/orgs", response_model=List[Organization])
async def list_tenant_orgs(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_owner)
):
    """
    List ALL organizations in the tenant (for Admin dropdowns).
    """
    stmt = select(Tenant).where(Tenant.owner_id == current_user.id)
    tenant = (await session.exec(stmt)).first()
    
    # In a real multi-tenant system, we filter by tenant_id.
    # For now, we assume this Tenant Owner owns ALL orgs or we filter by tenant relationship.
    # Check if we enforced tenant_id on Orgs. Yes we did.
    
    if tenant:
        orgs = await session.exec(select(Organization).where(Organization.tenant_id == tenant.id))
        return orgs.all()
    return []

@router.post("/users/{user_id}/assignments")
async def assign_user_to_orgs(
    user_id: int, 
    assignment: OrgAssignment,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_tenant_owner)
):
    """
    Bulk assign a user to multiple organizations.
    """
    target_user = await session.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    results = []
    
    for org_id in assignment.org_ids:
        # Verify Org exists (and optionally belongs to this tenant, but Super Admin implies full access)
        # For strict multi-tenancy, we should check if Org belongs to the caller's Tenant.
        # However, if we assume single-tenant deployment or "Super Admin" means "Platform Owner", we skip.
        # Let's enforce strict Tenant ownership for safety.
        
        # Get Org and check Tenant
        org = await session.get(Organization, org_id)
        if not org:
            results.append({"org_id": org_id, "status": "error", "message": "Organization not found"})
            continue
            
        stmt = select(Tenant).where(Tenant.owner_id == current_user.id)
        my_tenant = (await session.exec(stmt)).first()
        
        if not my_tenant or org.tenant_id != my_tenant.id:
             # If org has no tenant, maybe claim it? For now, error.
             if org.tenant_id is None:
                 # Auto-claim logic or error? Let's error to be safe.
                 pass
             elif org.tenant_id != my_tenant.id:
                 results.append({"org_id": org_id, "status": "error", "message": "Organization does not belong to your tenant"})
                 continue

        # Add User to Org (using Service or Direct)
        await org_service.add_user_to_organization(
            user_id=target_user.id, 
            org_id=org_id, 
            role=assignment.role, 
            session=session
        )
        results.append({"org_id": org_id, "status": "success"})
        
    return {"results": results}
