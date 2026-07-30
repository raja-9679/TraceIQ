"""Standalone-user provisioning shared by self-registration, SSO JIT login,
and the ADMIN_EMAIL bootstrap.

"Standalone" = the user gets their own Tenant (as Tenant Admin) and a default
Workspace, exactly like the /auth/register non-invite flow. Keeping the three
entry points on one code path stops SSO users from landing without a
workspace and keeps tenant semantics identical everywhere.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Tenant, User, UserSystemRole
from app.services.rbac_service import rbac_service

logger = logging.getLogger(__name__)


async def provision_standalone_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    hashed_password: str,
    organization_name: Optional[str] = None,
    project_name: Optional[str] = None,
    is_verified: bool = False,
    is_instance_admin: bool = False,
    commit: bool = True,
) -> User:
    """Create a user with their own tenant + default workspace. The caller is
    responsible for uniqueness checks (or handles IntegrityError)."""
    from datetime import datetime

    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=full_name,
        is_verified=is_verified,
        email_verified_at=datetime.utcnow() if is_verified else None,
        is_instance_admin=is_instance_admin,
    )
    session.add(user)
    await session.flush()

    tenant_name = organization_name or f"{full_name or email}'s Workspace"
    tenant = Tenant(name=tenant_name, owner_id=user.id)
    session.add(tenant)
    await session.flush()

    ta_role = await rbac_service.get_role_by_name(session, "Tenant Admin")
    if not ta_role:
        raise RuntimeError("System configuration error: Tenant Admin role missing")
    session.add(UserSystemRole(user_id=user.id, role_id=ta_role.id, tenant_id=tenant.id))

    from app.services.workspace_service import workspace_service
    await workspace_service.create_workspace(
        name=tenant_name,
        owner_id=user.id,
        session=session,
        commit=False,
        auto_create_project=True,
        project_name=project_name,
        tenant_id=tenant.id,
    )
    # Email-based invitations issued before this account existed.
    await workspace_service.process_pending_invitations(email, user.id, session)

    if commit:
        await session.commit()
        await session.refresh(user)
    return user


async def ensure_bootstrap_admin(session: AsyncSession) -> None:
    """First-boot instance admin from ADMIN_EMAIL/ADMIN_PASSWORD env vars.

    Idempotent: creates the account only when the email doesn't exist yet
    (env is never a rolling password authority — changing ADMIN_PASSWORD
    later does NOT rewrite the stored hash); an existing account is only
    healed to is_instance_admin=True. No-op when the vars are unset, which is
    the default — first registered user stays the operator via the
    first-tenant fallback.
    """
    import os

    from app.core.auth import get_password_hash

    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or ""
    if not email:
        return
    if not password:
        logger.warning("[bootstrap-admin] ADMIN_EMAIL is set but ADMIN_PASSWORD is empty; skipping")
        return

    user = (await session.exec(select(User).where(User.email == email))).first()
    if user:
        if not user.is_instance_admin:
            user.is_instance_admin = True
            session.add(user)
            await session.commit()
            logger.info("[bootstrap-admin] granted instance admin to existing %s", email)
        return

    await provision_standalone_user(
        session,
        email=email,
        full_name=os.getenv("ADMIN_FULL_NAME") or "Instance Admin",
        hashed_password=get_password_hash(password),
        organization_name=os.getenv("ADMIN_ORG_NAME") or None,
        is_verified=True,
        is_instance_admin=True,
    )
    logger.info("[bootstrap-admin] created instance admin account %s", email)
