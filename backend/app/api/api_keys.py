"""API key CRUD endpoints — workspace-scoped service-account credentials.

Surface:
    POST   /api/workspaces/{workspace_id}/api-keys        create (returns secret once)
    GET    /api/workspaces/{workspace_id}/api-keys        list
    DELETE /api/workspaces/{workspace_id}/api-keys/{id}   revoke
"""
from datetime import datetime, timedelta
from typing import List, Optional

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import (
    AuthPrincipal,
    generate_api_key,
    get_current_principal,
)
from app.core.client_ip import normalize_cidrs
from app.core.database import get_session
from app.services.rbac_service import rbac_service
from app.services.audit import record as audit_record
from app.models import (
    ApiKey,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyRead,
    UserWorkspace,
)

router = APIRouter()


async def _ensure_workspace_member(workspace_id: int, user_id: int, session: AsyncSession) -> UserWorkspace:
    # Key management is workspace-wide: a calling API key must be for this
    # workspace and not narrowed to a project.
    from app.services.access_service import access_service
    membership = await access_service.workspace_membership(user_id, workspace_id, session)
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

    # Both limits are enforced on every request (app/services/key_scope.py), so
    # they must name something real: a project in THIS workspace that the
    # creator can reach, and an existing role.
    if body.project_id is not None:
        from app.models import Project
        from app.services.access_service import access_service
        project = await session.get(Project, body.project_id)
        if (project is None or project.workspace_id != workspace_id
                or not await access_service.has_project_access(
                    principal.user.id, project.id, session)):
            raise HTTPException(status_code=400, detail="project_id is not a project in this workspace")
    if body.role_id is not None:
        from app.models import Role
        if await session.get(Role, body.role_id) is None:
            raise HTTPException(status_code=400, detail="role_id does not name a role")

    try:
        allowed_cidrs = normalize_cidrs(body.allowed_cidrs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"allowed_cidrs: {exc}")

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
        allowed_cidrs=allowed_cidrs,
    )
    session.add(key)
    await session.flush()   # need key.id for the audit row
    # Minting a credential is never silent. The prefix identifies the key
    # without revealing it, and is what later audit rows carry as actor_label,
    # so the two can be correlated.
    await audit_record(
        session,
        entity_type="api_key", entity_id=key.id, action="create",
        user_id=principal.user.id, workspace_id=key.workspace_id,
        changes={"name": key.name, "prefix": key.prefix,
                 "project_id": key.project_id, "role_id": key.role_id,
                 "expires_at": expires_at.isoformat() if expires_at else None,
                 "allowed_cidrs": allowed_cidrs},
    )
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
        allowed_cidrs=key.allowed_cidrs,
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
            allowed_cidrs=k.allowed_cidrs,
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
    await audit_record(
        session,
        entity_type="api_key", entity_id=key.id, action="revoke",
        user_id=principal.user.id, workspace_id=key.workspace_id,
        changes={"name": key.name, "prefix": key.prefix},
    )
    await session.commit()
    return {"status": "revoked", "id": key_id}


class ApiKeyNetworkUpdate(BaseModel):
    # None or [] removes the restriction.
    allowed_cidrs: Optional[List[str]] = None


@router.put(
    "/workspaces/{workspace_id}/api-keys/{key_id}/allowed-cidrs",
    response_model=ApiKeyRead,
)
async def update_api_key_cidrs(
    workspace_id: int,
    key_id: int,
    body: ApiKeyNetworkUpdate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyRead:
    """Set or clear a key's source-IP allowlist.

    Only the key's creator or a workspace admin: clearing the list widens
    where a credential works, and any-member is the rule for *revoking* only
    because revocation can only ever reduce access. An API key cannot edit
    any key's allowlist — a leaked key must not be able to free itself.
    """
    if principal.is_api_caller:
        raise HTTPException(status_code=403, detail="API keys cannot change API key settings")
    await _ensure_workspace_member(workspace_id, principal.user.id, session)
    key = await session.get(ApiKey, key_id)
    if not key or key.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.created_by_id != principal.user.id and not await rbac_service.has_permission(
            session, principal.user.id, "workspace:manage_users", workspace_id=workspace_id):
        raise HTTPException(status_code=403,
                            detail="Only the key's creator or a workspace admin can change it")
    try:
        cidrs = normalize_cidrs(body.allowed_cidrs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"allowed_cidrs: {exc}")
    before = key.allowed_cidrs
    key.allowed_cidrs = cidrs
    session.add(key)
    await audit_record(
        session,
        entity_type="api_key", entity_id=key.id, action="update_allowed_cidrs",
        user_id=principal.user.id, workspace_id=key.workspace_id,
        changes={"prefix": key.prefix, "before": before, "after": cidrs},
    )
    await session.commit()
    await session.refresh(key)
    return ApiKeyRead(
        id=key.id, workspace_id=key.workspace_id, project_id=key.project_id,
        name=key.name, prefix=key.prefix, created_at=key.created_at,
        last_used_at=key.last_used_at, expires_at=key.expires_at,
        revoked_at=key.revoked_at, allowed_cidrs=key.allowed_cidrs,
    )
