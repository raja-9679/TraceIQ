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

class Token(BaseModel):
    access_token: str
    token_type: str

from app.core.rbac_service import rbac_service

class PermissionsResponse(BaseModel):
    permissions: List[str]
    roles: List[str]

@router.get("/permissions", response_model=PermissionsResponse)
async def get_my_permissions(
    project_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> PermissionsResponse:
    """
    Get effective permissions and roles for the current user in a specific project.
    """
    roles = await rbac_service.get_user_roles_for_project(current_user.id, project_id, session)
    role_names = [r.name for r in roles]
    role_ids = [r.id for r in roles]
    
    if not role_ids:
        return PermissionsResponse(permissions=[], roles=[])

    # Fetch permissions
    query = (
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id.in_(role_ids))
    )
    perms_result = await session.exec(query)
    permissions = [f"{p.resource}:{p.action}" for p in perms_result.all()]
    
    return PermissionsResponse(permissions=permissions, roles=role_names)

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
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name
    )
    session.add(user)
    await session.flush() # Get user id
    
    # Create default organization for the user
    from app.services.org_service import org_service
    org_name = user_in.org_name or f"{user_in.full_name or user_in.email}'s Org"
    await org_service.create_organization(
        name=org_name, 
        owner_id=user.id, 
        session=session, 
        commit=False, 
        auto_create_project=True,
        project_name=user_in.project_name
    )
    
    # Process any pending invitations
    await org_service.process_pending_invitations(user.email, user.id, session)
    
    await session.commit()
    await session.refresh(user)
    return user

@router.get("/me", response_model=UserRead)
async def read_users_me(
    current_user: User = Depends(get_current_user)
) -> Any:
    return current_user
