"""User provisioning shared by self-registration, SSO/LDAP login, and the
ADMIN_EMAIL bootstrap.

Two shapes:

**Standalone** — the user gets their own Tenant (as Tenant Admin) and a default
Workspace, exactly like the /auth/register non-invite flow. Correct for
self-registration and for the bootstrap admin.

**Federated** — the user joins an existing workspace with a role derived from
their IdP groups, and gets no tenant of their own. Correct for SSO and LDAP:
routing those through the standalone path (which is what happened before
workstream F1) turns an organisation of 500 employees into 500 isolated tenants
with 500 tenant admins, none of whom can see a shared project.

Which shape a federated login takes is an operator decision, resolved in
`app/services/federation.py`. The default is still standalone, so existing
installs upgrade unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Tenant, Team, User, UserSystemRole, UserTeam, UserWorkspace, Workspace
from app.services.federation import (
    LEGACY_ROLE_LEVEL,
    MODE_DENY,
    MODE_STANDALONE,
    MODE_WORKSPACE,
    FederationConfigError,
    FederationPolicy,
    resolve_policy,
    role_for_groups,
    teams_for_groups,
)
from app.services.rbac_service import rbac_service

logger = logging.getLogger(__name__)


class FederatedProvisioningDenied(Exception):
    """No account exists and FEDERATED_PROVISIONING_MODE is 'deny'.

    The deployment provisions accounts out-of-band (invitations, or SCIM), so a
    successful IdP authentication is not by itself grounds for an account.
    Surfaced to the user as a 403.
    """


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


async def provision_federated_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    hashed_password: str,
    groups: Optional[Sequence[str]] = None,
    is_verified: bool = True,
    commit: bool = True,
) -> User:
    """Create an account for a user the IdP just authenticated.

    Honours the instance's federation policy: `standalone` delegates to
    `provision_standalone_user` (the pre-F1 behaviour), `workspace` joins the
    configured workspace, `deny` raises FederatedProvisioningDenied.

    Raises FederationConfigError when the policy cannot be honoured — a missing
    workspace id, a workspace that no longer exists, an unknown role name. It
    deliberately does NOT fall back to standalone: a deployment whose admin
    believes they configured a shared workspace must not silently keep minting
    one tenant per employee.
    """
    policy = resolve_policy()

    if policy.mode == MODE_DENY:
        raise FederatedProvisioningDenied(
            f"{email} authenticated successfully but has no TraceIQ account, and "
            "just-in-time provisioning is disabled on this instance")

    if policy.mode == MODE_STANDALONE:
        return await provision_standalone_user(
            session, email=email, full_name=full_name,
            hashed_password=hashed_password, is_verified=is_verified, commit=commit)

    workspace = await _require_workspace(session, policy)

    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=full_name,
        is_verified=is_verified,
        email_verified_at=datetime.utcnow() if is_verified else None,
    )
    session.add(user)
    await session.flush()

    # authoritative_role: at creation the policy decides the role outright.
    await _apply_federated_access(
        session, user, workspace, policy, list(groups or []), authoritative_role=True)

    # Email-based invitations issued before this account existed. Runs after the
    # federated membership so the policy wins for the target workspace; invites
    # to OTHER workspaces still land.
    from app.services.workspace_service import workspace_service
    await workspace_service.process_pending_invitations(email, user.id, session)

    if commit:
        await session.commit()
        await session.refresh(user)
    logger.info("[federation] provisioned %s into workspace %s", email, workspace.id)
    return user


async def sync_federated_access(
    session: AsyncSession,
    user: User,
    groups: Optional[Sequence[str]] = None,
    *,
    commit: bool = True,
) -> None:
    """Re-apply the federation policy to an EXISTING user on every login.

    This is what makes IdP group mapping mean anything. Applied only at
    creation, a mapping would grant admin on first login and never take it
    back — removing someone from the `traceiq-admins` group in Okta would leave
    their TraceIQ admin in place indefinitely, which is worse than having no
    mapping at all because it looks authoritative and isn't.

    The role is only overwritten when a group map is configured. With no map the
    IdP is saying nothing about roles, so an in-app promotion is the only source
    of truth and must survive the next login. Team membership is synced only for
    teams that appear in the map; teams TraceIQ manages itself are untouched.
    """
    policy = resolve_policy()
    if policy.mode != MODE_WORKSPACE:
        return
    workspace = await _require_workspace(session, policy)
    await _apply_federated_access(
        session, user, workspace, policy, list(groups or []), authoritative_role=False)
    if commit:
        await session.commit()


async def _require_workspace(session: AsyncSession, policy: FederationPolicy) -> Workspace:
    workspace = await session.get(Workspace, policy.workspace_id)
    if workspace is None:
        raise FederationConfigError(
            f"FEDERATED_WORKSPACE_ID is {policy.workspace_id}, which is not an existing "
            "workspace — federated logins are refused until it is corrected")
    return workspace


async def _apply_federated_access(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    policy: FederationPolicy,
    groups: List[str],
    *,
    authoritative_role: bool,
) -> None:
    role_name = role_for_groups(policy, groups)
    role = await rbac_service.get_role_by_name(session, role_name)
    if not role:
        raise FederationConfigError(
            f"Role {role_name!r} does not exist in this instance — has RBAC been "
            "initialised?")

    membership = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == user.id,
                                    UserWorkspace.workspace_id == workspace.id))).first()
    if membership is None:
        session.add(UserWorkspace(
            user_id=user.id, workspace_id=workspace.id,
            role=LEGACY_ROLE_LEVEL[role_name], role_id=role.id))
    elif (authoritative_role or policy.group_role_map) and membership.role_id != role.id:
        # access_service still falls back to the legacy string column for rows
        # with no role_id, so the two must never disagree.
        logger.info("[federation] %s: workspace role -> %s", user.email, role_name)
        membership.role_id = role.id
        membership.role = LEGACY_ROLE_LEVEL[role_name]
        session.add(membership)

    await _sync_federated_teams(session, user, workspace, policy, groups)
    await session.flush()


async def _sync_federated_teams(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    policy: FederationPolicy,
    groups: List[str],
) -> None:
    """Join/leave the teams named in FEDERATED_GROUP_TEAM_MAP.

    Teams are the mechanism that actually carries project access, so this is how
    a federated user gets to see anything. Only teams *inside the target
    workspace* are considered: team names are not unique across workspaces, and
    a map entry must never pull a user into another tenant's team.
    """
    mapped_names = set(policy.group_team_map.values())
    if not mapped_names:
        return

    teams = (await session.exec(
        select(Team).where(Team.workspace_id == workspace.id,
                           Team.name.in_(mapped_names)))).all()
    by_name = {t.name: t for t in teams}
    wanted = set(teams_for_groups(policy, groups))

    for name in sorted(wanted - set(by_name)):
        # A renamed or deleted team is a settings error, but refusing the login
        # would take the whole organisation offline over it. Degrade loudly.
        logger.warning(
            "[federation] FEDERATED_GROUP_TEAM_MAP names team %r, which does not "
            "exist in workspace %s — %s will not receive its access",
            name, workspace.id, user.email)

    for name, team in by_name.items():
        existing = (await session.exec(
            select(UserTeam).where(UserTeam.user_id == user.id,
                                   UserTeam.team_id == team.id))).first()
        if name in wanted and existing is None:
            session.add(UserTeam(user_id=user.id, team_id=team.id))
        elif name not in wanted and existing is not None:
            logger.info("[federation] %s: leaving team %s", user.email, name)
            await session.delete(existing)


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

    from app.core.auth import get_password_hash, verify_password

    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or ""
    if not email:
        return
    if not password:
        logger.warning("[bootstrap-admin] ADMIN_EMAIL is set but ADMIN_PASSWORD is empty; skipping")
        return

    user = (await session.exec(select(User).where(User.email == email))).first()
    if user:
        # Heal an EXISTING account to instance-admin only if ADMIN_PASSWORD
        # matches its stored hash. Without this, an attacker who self-registered
        # the (often predictable) ADMIN_EMAIL before the operator set ADMIN_*
        # would be silently promoted to instance admin on the next boot.
        if not verify_password(password, user.hashed_password):
            logger.error(
                "[bootstrap-admin] %s already exists and ADMIN_PASSWORD does not "
                "match its stored password; refusing to grant instance admin", email)
            return
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
