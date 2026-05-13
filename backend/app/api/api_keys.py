"""API key CRUD endpoints — workspace-scoped service-account credentials.

Surface:
    POST   /api/workspaces/{workspace_id}/api-keys        create (returns secret once)
    GET    /api/workspaces/{workspace_id}/api-keys        list
    DELETE /api/workspaces/{workspace_id}/api-keys/{id}   revoke
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import (
    AuthPrincipal,
    generate_api_key,
    get_current_principal,
)
from app.core.database import get_session
from app.models import (
    ApiKey,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyRead,
    UserWorkspace,
)

router = APIRouter()


async def _ensure_workspace_member(workspace_id: int, user_id: int, session: AsyncSession) -> UserWorkspace:
    res = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.user_id == user_id,
        )
    )
    membership = res.first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return membership


@router.post(
    "/workspaces/{workspace_id}/api-keys",
    response_model=ApiKeyCreateResponse,
)
async def create_api_key(
    workspace_id: int,
    body: ApiKeyCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreateResponse:
    # An API key cannot mint another API key — limits blast radius if one leaks.
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot create other API keys")
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id mismatch")

    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    raw_key, prefix, hashed_key = generate_api_key()
    expires_at: Optional[datetime] = None
    if body.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=body.expires_in_days)

    key = ApiKey(
        workspace_id=workspace_id,
        project_id=body.project_id,
        name=body.name,
        prefix=prefix,
        hashed_key=hashed_key,
        role_id=body.role_id,
        created_by_id=principal.user.id,
        expires_at=expires_at,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)

    return ApiKeyCreateResponse(
        id=key.id,
        workspace_id=key.workspace_id,
        project_id=key.project_id,
        name=key.name,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
        secret=raw_key,
    )


@router.get(
    "/workspaces/{workspace_id}/api-keys",
    response_model=List[ApiKeyRead],
)
async def list_api_keys(
    workspace_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ApiKeyRead]:
    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    res = await session.exec(
        select(ApiKey)
        .where(ApiKey.workspace_id == workspace_id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = res.all()
    return [
        ApiKeyRead(
            id=k.id,
            workspace_id=k.workspace_id,
            project_id=k.project_id,
            name=k.name,
            prefix=k.prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            revoked_at=k.revoked_at,
        )
        for k in keys
    ]


@router.delete("/workspaces/{workspace_id}/api-keys/{key_id}")
async def revoke_api_key(
    workspace_id: int,
    key_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot revoke API keys")
    await _ensure_workspace_member(workspace_id, principal.user.id, session)

    key = await session.get(ApiKey, key_id)
    if not key or key.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is not None:
        return {"status": "already_revoked"}

    key.revoked_at = datetime.utcnow()
    session.add(key)
    await session.commit()
    return {"status": "revoked", "id": key_id}
