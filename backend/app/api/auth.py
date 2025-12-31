from typing import Any
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List, Optional

from app.core.database import get_session
from app.core.auth import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.models import User, UserRead, Role, Permission, RolePermission
from pydantic import BaseModel

router = APIRouter()

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    org_name: str | None = None
    project_name: str | None = None
    invite_token: str | None = None

class Token(BaseModel):
    access_token: str
    token_type: str

from app.services.rbac_service import rbac_service

class PermissionsResponse(BaseModel):
    system: List[str]
    organization: dict
    project: dict

@router.get("/permissions", response_model=PermissionsResponse)
async def get_my_permissions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> PermissionsResponse:
    """
    Get effective permissions for the current user across all scopes.
    """
    perms_map = await rbac_service.get_user_permissions_map(current_user.id, session)
    return PermissionsResponse(**perms_map)

@router.post("/login", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session)
) -> Any:
    result = await session.exec(select(User).where(User.email == form_data.username))
    user = result.first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    # Update last login
    user.last_login_at = datetime.utcnow()
    session.add(user)
    await session.commit()
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/register", response_model=UserRead)
async def register_user(
    user_in: UserCreate,
    session: AsyncSession = Depends(get_session)
) -> Any:
    result = await session.exec(select(User).where(User.email == user_in.email))
    existing_user = result.first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User with this email already exists"
        )
    
    # --- Branching Logic ---
    if user_in.invite_token:
        # 1. Invite Flow
        from app.models import OrganizationInvitation, UserOrganization
        # Validate Invite
        stmt = select(OrganizationInvitation).where(OrganizationInvitation.token == user_in.invite_token)
        invite = (await session.exec(stmt)).first()
        
        if not invite:
             raise HTTPException(status_code=400, detail="Invalid invitation token")
        if invite.expires_at < datetime.utcnow():
             raise HTTPException(status_code=400, detail="Invitation token expired")
        if invite.email != user_in.email:
             # Basic check, though usually token implies email matching or binding
             raise HTTPException(status_code=400, detail="Email does not match invitation")

        # Create User (No Tenant Admin)
        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name
        )
        session.add(user)
        await session.flush()
        
        # Link to Org (Role is in invite)
        # Re-use process logic but specific to this token
        # Map role
        rbac_role_name = "Organization Member"
        if invite.role == "admin": 
            rbac_role_name = "Organization Admin"
        
        # We need rbac_service here
        # Local import or global? 'rbac_service' is imported at module level in original file but inside Pydantic block?
        # Check original file imports. Line 33 imports rbac_service.
        
        rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
        role_id = rbac_role.id if rbac_role else None
        
        uo = UserOrganization(
            user_id=user.id, 
            organization_id=invite.organization_id, 
            role=invite.role,
            role_id=role_id
        )
        session.add(uo)
        
        # Consume Invite
        await session.delete(invite)
        
        # NEW: Link to Project if invite has project info
        if invite.project_id and invite.project_role:
             from app.models import UserProjectAccess
             # Map access_level ('admin', 'editor', 'viewer') to Role
             pa_role_name = "Project Viewer"
             if invite.project_role == "admin": pa_role_name = "Project Admin"
             elif invite.project_role == "editor": pa_role_name = "Project Editor"
             
             p_role = await rbac_service.get_role_by_name(session, pa_role_name)
             
             upa = UserProjectAccess(
                 user_id=user.id,
                 project_id=invite.project_id,
                 access_level=invite.project_role,
                 role_id=p_role.id if p_role else None
             )
             session.add(upa)
             await session.flush()
        
        # We DO NOT create a Tenant. We DO NOT assign UserSystemRole (Tenant Admin).
        # We DO NOT create a default Org.
        
    else:
        # 2. Standalone Flow (Tenant Creation)
        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name
        )
        session.add(user)
        await session.flush()
        
        # Create New Tenant
        from app.models import Tenant, UserSystemRole
        tenant_name = user_in.org_name or f"{user_in.full_name or user_in.email}'s Workspace"
        tenant = Tenant(name=tenant_name, owner_id=user.id)
        session.add(tenant)
        await session.flush() # Get Tenant ID
        
        # Assign Tenant Admin Role
        ta_role = await rbac_service.get_role_by_name(session, "Tenant Admin")
        if not ta_role:
             raise HTTPException(500, "System configuration error: Tenant Admin role missing")
             
        usr = UserSystemRole(
            user_id=user.id,
            role_id=ta_role.id,
            tenant_id=tenant.id
        )
        session.add(usr)
        
        # Create Default Org linked to Tenant
        from app.services.org_service import org_service
        # Note: org_service.create_organization now accepts tenant_id
        await org_service.create_organization(
            name=tenant_name, 
            owner_id=user.id, 
            session=session, 
            commit=False, 
            auto_create_project=True,
            project_name=user_in.project_name,
            tenant_id=tenant.id
        )
        
        # Org Admin role is assigned inside create_organization
        
        # Process any other pending email-based invitations (legacy / team invites)
        await org_service.process_pending_invitations(user.email, user.id, session)
        
    await session.commit()
    await session.refresh(user)
    return user

@router.get("/me", response_model=UserRead)
async def read_users_me(
    current_user: User = Depends(get_current_user)
) -> Any:
    return current_user
