"""SCIM 2.0 provisioning endpoints — workstream F2.

The gap this closes: TraceIQ had no deprovisioning path of any kind. Someone
removed from Okta or Entra kept their account, their sessions and their refresh
token indefinitely. SSO without SCIM automates only the joining half.

Mounted at both `/scim/v2/...` and `/api/scim/v2/...`. The duplicate is not
tidiness — the shipped nginx only proxies `/api/`, so a compose or all-in-one
deployment can only reach the `/api` variant from outside, while a split
deployment pointing the IdP straight at the backend will use the bare one.

Design decisions worth not re-litigating:

* **Users land in `FEDERATED_WORKSPACE_ID`** — the same target as SSO/LDAP (see
  `app/services/federation.py`). SCIM refuses to run without it rather than
  minting a tenant per user, which is precisely the bug F1 removed.
* **DELETE deactivates, it never destroys.** IdPs issue DELETE routinely on
  offboarding, and a hard delete would orphan runs, results and audit history
  irreversibly. `active: false` and DELETE are the same operation here.
* **Deactivation revokes refresh tokens.** `is_active` is re-checked on every
  request so access tokens die within minutes, but a live refresh token would
  keep minting new ones — that is the half of the finding that actually leaves
  a door open.
* **A POST for an email that already exists adopts that account.** Turning SCIM
  on for an instance with existing users must not 409 every one of them
  forever, and must never create a second row for the same person.
* **Everything is scoped to the target workspace**, so a SCIM credential cannot
  enumerate or mutate accounts outside it.
"""
from __future__ import annotations

import logging
import secrets as pysecrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_password_hash
from app.core.database import get_session
from app.models import RefreshToken, Team, User, UserTeam, UserWorkspace, Workspace
from app.services.federation import LEGACY_ROLE_LEVEL, FederationConfigError, resolve_policy
from app.services.instance_settings import effective
from app.services.rbac_service import rbac_service
from app.services.scim import (
    ScimError,
    group_to_scim,
    list_response,
    parse_filter,
    parse_patch,
    user_to_scim,
)

logger = logging.getLogger(__name__)

# Re-exported so app/main.py can register the exception handler without
# reaching into the service layer.
__all__ = ["router", "ScimError"]

router = APIRouter()

ACTOR_LABEL = "scim (identity provider)"


def _error(exc: ScimError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.to_dict())


async def scim_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Bearer-token guard.

    With no `SCIM_TOKEN` configured the surface answers 404: an unconfigured
    provisioning API must not be an open one, and 404 avoids advertising that it
    exists at all. Comparison is constant-time — the token is a bearer
    credential with full user-management authority.
    """
    token = str(effective("SCIM_TOKEN") or "").strip()
    if not token:
        raise ScimError(404, "SCIM provisioning is not enabled on this instance")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not pysecrets.compare_digest(presented, token):
        raise ScimError(401, "Invalid SCIM credentials")
    return ACTOR_LABEL


async def _target_workspace(session: AsyncSession) -> Workspace:
    """The workspace SCIM provisions into — the same one SSO/LDAP use."""
    try:
        policy = resolve_policy()
    except FederationConfigError as exc:
        raise ScimError(503, f"Federation is misconfigured: {exc}")
    if policy.workspace_id is None:
        raise ScimError(
            503,
            "SCIM needs FEDERATED_WORKSPACE_ID set (Instance (Admin) → Federated "
            "provisioning) — it will not create a tenant per user")
    workspace = await session.get(Workspace, policy.workspace_id)
    if workspace is None:
        raise ScimError(503, f"FEDERATED_WORKSPACE_ID {policy.workspace_id} does not exist")
    return workspace


async def _default_role_id(session: AsyncSession) -> tuple[Optional[int], str]:
    policy = resolve_policy()
    role = await rbac_service.get_role_by_name(session, policy.default_role)
    if not role:
        raise ScimError(503, f"Role {policy.default_role!r} does not exist in this instance")
    return role.id, policy.default_role


async def _member_ids(session: AsyncSession, workspace_id: int) -> List[int]:
    rows = await session.exec(
        select(UserWorkspace.user_id).where(UserWorkspace.workspace_id == workspace_id))
    return list(rows.all())


async def _scoped_user(session: AsyncSession, user_id: str,
                       workspace_id: int) -> User:
    """Look up a user, but only inside the target workspace. A SCIM credential
    for this instance has no business reading or mutating anyone else."""
    try:
        numeric = int(user_id)
    except (TypeError, ValueError):
        raise ScimError(404, f"No user with id {user_id}")
    user = await session.get(User, numeric)
    if user is None:
        raise ScimError(404, f"No user with id {user_id}")
    member = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == numeric,
                                    UserWorkspace.workspace_id == workspace_id))).first()
    if member is None:
        raise ScimError(404, f"No user with id {user_id}")
    return user


async def _scoped_team(session: AsyncSession, group_id: str,
                       workspace_id: int) -> Team:
    try:
        numeric = int(group_id)
    except (TypeError, ValueError):
        raise ScimError(404, f"No group with id {group_id}")
    team = await session.get(Team, numeric)
    if team is None or team.workspace_id != workspace_id:
        raise ScimError(404, f"No group with id {group_id}")
    return team


def _name_from_body(body: Dict[str, Any], fallback: str) -> str:
    name = body.get("name") or {}
    if isinstance(name, dict):
        formatted = name.get("formatted")
        if formatted:
            return str(formatted)
        parts = [name.get("givenName"), name.get("familyName")]
        joined = " ".join(str(p) for p in parts if p)
        if joined:
            return joined
    if body.get("displayName"):
        return str(body["displayName"])
    return fallback


def _username_from_body(body: Dict[str, Any]) -> str:
    username = (body.get("userName") or "").strip().lower()
    if not username:
        emails = body.get("emails") or []
        for entry in emails:
            if isinstance(entry, dict) and entry.get("value"):
                username = str(entry["value"]).strip().lower()
                break
    if not username or "@" not in username:
        raise ScimError(400, "userName must be an email address",
                        scim_type="invalidValue")
    return username


async def _revoke_sessions(session: AsyncSession, user_id: int) -> int:
    """Kill every live refresh token. Without this, deactivation only stops new
    logins — an existing session keeps rotating its refresh token and stays
    alive for the full refresh window."""
    from datetime import datetime

    live = (await session.exec(
        select(RefreshToken).where(RefreshToken.user_id == user_id,
                                   RefreshToken.revoked_at == None))).all()  # noqa: E711
    now = datetime.utcnow()
    for token in live:
        token.revoked_at = now
        session.add(token)
    return len(live)


async def _set_active(session: AsyncSession, user: User, active: bool,
                      workspace_id: int, request: Request) -> None:
    from app.services.audit import record as audit_record

    if user.is_active == active:
        return
    user.is_active = active
    session.add(user)
    revoked = 0
    if not active:
        revoked = await _revoke_sessions(session, user.id)
    await audit_record(
        session,
        entity_type="user",
        entity_id=user.id,
        action="scim_activate" if active else "scim_deactivate",
        workspace_id=workspace_id,
        actor_type="service",
        actor_label=ACTOR_LABEL,
        request=request,
        changes={"active": active, "refresh_tokens_revoked": revoked},
    )
    logger.info("[scim] %s %s (revoked %d refresh tokens)",
                "activated" if active else "deactivated", user.email, revoked)


# --- Discovery ---------------------------------------------------------------

@router.get("/scim/v2/ServiceProviderConfig")
async def service_provider_config(_: str = Depends(scim_principal)):
    """Okta probes this before its first sync."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://github.com/raja-9679/TraceIQ/blob/main/docs/ENTERPRISE_AUTH.md",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "OAuth Bearer Token",
            "description": "Static token configured in Instance (Admin) → Federated provisioning",
        }],
        "meta": {"resourceType": "ServiceProviderConfig"},
    }


# --- Users -------------------------------------------------------------------

@router.get("/scim/v2/Users")
async def list_users(
    request: Request,
    filter: Optional[str] = None,  # noqa: A002 — the SCIM query parameter is `filter`
    startIndex: int = 1,  # noqa: N803 — SCIM spells it this way
    count: int = 100,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    parsed = parse_filter(filter)

    query = (select(User)
             .join(UserWorkspace, UserWorkspace.user_id == User.id)
             .where(UserWorkspace.workspace_id == workspace.id))
    if parsed:
        attribute, value = parsed
        if attribute == "userName":
            query = query.where(User.email == value.strip().lower())
        elif attribute == "externalId":
            query = query.where(User.scim_external_id == value)
        else:
            raise ScimError(400, f"Cannot filter Users on {attribute}",
                            scim_type="invalidFilter")

    users = (await session.exec(query)).all()
    total = len(users)
    start = max(1, startIndex)
    window = users[start - 1: start - 1 + max(0, count)]
    return list_response([user_to_scim(u) for u in window],
                         total=total, start_index=start, count=count)


@router.post("/scim/v2/Users")
async def create_user(
    request: Request,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    from app.services.audit import record as audit_record

    workspace = await _target_workspace(session)
    username = _username_from_body(body)
    external_id = body.get("externalId")
    full_name = _name_from_body(body, username.split("@")[0])
    active = body.get("active")
    active = True if active is None else bool(active)

    existing = (await session.exec(select(User).where(User.email == username))).first()
    if existing:
        member = (await session.exec(
            select(UserWorkspace).where(UserWorkspace.user_id == existing.id,
                                        UserWorkspace.workspace_id == workspace.id))).first()
        if member and existing.scim_external_id:
            # Already ours and already linked — a genuine duplicate create.
            raise ScimError(409, f"{username} already exists", scim_type="uniqueness")
        # Adopt: link the IdP's id and make sure they are in the workspace.
        if external_id:
            existing.scim_external_id = str(external_id)
            session.add(existing)
        if not member:
            role_id, role_name = await _default_role_id(session)
            session.add(UserWorkspace(user_id=existing.id, workspace_id=workspace.id,
                                      role=LEGACY_ROLE_LEVEL[role_name], role_id=role_id))
        await audit_record(
            session, entity_type="user", entity_id=existing.id,
            action="scim_adopt", workspace_id=workspace.id,
            actor_type="service", actor_label=ACTOR_LABEL, request=request,
            changes={"external_id": external_id})
        await session.commit()
        await session.refresh(existing)
        return JSONResponse(status_code=200, content=user_to_scim(existing))

    role_id, role_name = await _default_role_id(session)
    user = User(
        email=username,
        full_name=full_name,
        # Unusable by construction: the IdP is the only way in, and a blank or
        # predictable hash would be a second, unmanaged door.
        hashed_password=get_password_hash(pysecrets.token_urlsafe(32)),
        is_verified=True,
        is_active=active,
        scim_external_id=str(external_id) if external_id else None,
    )
    session.add(user)
    await session.flush()
    session.add(UserWorkspace(user_id=user.id, workspace_id=workspace.id,
                              role=LEGACY_ROLE_LEVEL[role_name], role_id=role_id))

    from app.services.workspace_service import workspace_service
    await workspace_service.process_pending_invitations(username, user.id, session)

    await audit_record(
        session, entity_type="user", entity_id=user.id, action="scim_create",
        workspace_id=workspace.id, actor_type="service", actor_label=ACTOR_LABEL,
        request=request, changes={"email": username, "role": role_name})
    await session.commit()
    await session.refresh(user)
    logger.info("[scim] provisioned %s into workspace %s", username, workspace.id)
    return JSONResponse(status_code=201, content=user_to_scim(user))


@router.get("/scim/v2/Users/{user_id}")
async def get_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    return user_to_scim(await _scoped_user(session, user_id, workspace.id))


@router.put("/scim/v2/Users/{user_id}")
async def replace_user(
    request: Request,
    user_id: str,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    user = await _scoped_user(session, user_id, workspace.id)

    user.full_name = _name_from_body(body, user.full_name)
    if body.get("externalId"):
        user.scim_external_id = str(body["externalId"])
    session.add(user)
    if "active" in body:
        await _set_active(session, user, bool(body["active"]), workspace.id, request)
    await session.commit()
    await session.refresh(user)
    return user_to_scim(user)


@router.patch("/scim/v2/Users/{user_id}")
async def patch_user(
    request: Request,
    user_id: str,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    user = await _scoped_user(session, user_id, workspace.id)
    ops = parse_patch(body)

    if ops.display_name:
        user.full_name = ops.display_name
        session.add(user)
    if ops.active is not None:
        await _set_active(session, user, ops.active, workspace.id, request)
    await session.commit()
    await session.refresh(user)
    return user_to_scim(user)


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
async def delete_user(
    request: Request,
    user_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    """Deactivate, never destroy — see the module docstring."""
    workspace = await _target_workspace(session)
    user = await _scoped_user(session, user_id, workspace.id)
    await _set_active(session, user, False, workspace.id, request)
    await session.commit()
    return Response(status_code=204)


# --- Groups → teams -----------------------------------------------------------

@router.get("/scim/v2/Groups")
async def list_groups(
    filter: Optional[str] = None,  # noqa: A002
    startIndex: int = 1,  # noqa: N803
    count: int = 100,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    parsed = parse_filter(filter)

    query = select(Team).where(Team.workspace_id == workspace.id)
    if parsed:
        attribute, value = parsed
        if attribute == "displayName":
            query = query.where(Team.name == value)
        elif attribute == "externalId":
            query = query.where(Team.scim_external_id == value)
        else:
            raise ScimError(400, f"Cannot filter Groups on {attribute}",
                            scim_type="invalidFilter")

    teams = (await session.exec(query)).all()
    start = max(1, startIndex)
    window = teams[start - 1: start - 1 + max(0, count)]
    resources = []
    for team in window:
        members = (await session.exec(
            select(UserTeam.user_id).where(UserTeam.team_id == team.id))).all()
        resources.append(group_to_scim(team_id=team.id, display_name=team.name,
                                       member_ids=list(members),
                                       external_id=team.scim_external_id))
    return list_response(resources, total=len(teams), start_index=start, count=count)


@router.post("/scim/v2/Groups")
async def create_group(
    request: Request,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    from app.services.audit import record as audit_record

    workspace = await _target_workspace(session)
    display_name = (body.get("displayName") or "").strip()
    if not display_name:
        raise ScimError(400, "displayName is required", scim_type="invalidValue")

    existing = (await session.exec(
        select(Team).where(Team.workspace_id == workspace.id,
                           Team.name == display_name))).first()
    if existing:
        # Adopt a team of the same name rather than creating a duplicate — an
        # operator who already made "QA Team" by hand means that team.
        if body.get("externalId"):
            existing.scim_external_id = str(body["externalId"])
            session.add(existing)
            await session.commit()
        team = existing
        status = 200
    else:
        team = Team(name=display_name, workspace_id=workspace.id,
                    scim_external_id=str(body["externalId"]) if body.get("externalId") else None)
        session.add(team)
        await session.flush()
        await audit_record(
            session, entity_type="team", entity_id=team.id, action="scim_create",
            workspace_id=workspace.id, actor_type="service", actor_label=ACTOR_LABEL,
            request=request, changes={"name": display_name})
        status = 201

    member_ids = await _apply_members(session, team, workspace.id,
                                      replace=[m.get("value") for m in (body.get("members") or [])
                                               if isinstance(m, dict)])
    await session.commit()
    return JSONResponse(status_code=status, content=group_to_scim(
        team_id=team.id, display_name=team.name, member_ids=member_ids,
        external_id=team.scim_external_id))


async def _apply_members(session: AsyncSession, team: Team, workspace_id: int, *,
                         add: Optional[List[str]] = None,
                         remove: Optional[List[str]] = None,
                         replace: Optional[List[str]] = None) -> List[int]:
    """Add/remove/replace team membership, ignoring ids outside the workspace."""
    async def _valid(raw_ids) -> List[int]:
        out = []
        for raw in raw_ids or []:
            try:
                numeric = int(raw)
            except (TypeError, ValueError):
                continue
            member = (await session.exec(
                select(UserWorkspace).where(
                    UserWorkspace.user_id == numeric,
                    UserWorkspace.workspace_id == workspace_id))).first()
            if member:
                out.append(numeric)
            else:
                logger.warning("[scim] group member %s is not in workspace %s — ignored",
                               raw, workspace_id)
        return out

    current = set((await session.exec(
        select(UserTeam.user_id).where(UserTeam.team_id == team.id))).all())

    if replace is not None and (replace or add is None and remove is None):
        wanted = set(await _valid(replace))
        for user_id in wanted - current:
            session.add(UserTeam(user_id=user_id, team_id=team.id))
        for user_id in current - wanted:
            row = (await session.exec(
                select(UserTeam).where(UserTeam.user_id == user_id,
                                       UserTeam.team_id == team.id))).first()
            if row:
                await session.delete(row)
        current = wanted
    else:
        for user_id in await _valid(add):
            if user_id not in current:
                session.add(UserTeam(user_id=user_id, team_id=team.id))
                current.add(user_id)
        for user_id in await _valid(remove):
            row = (await session.exec(
                select(UserTeam).where(UserTeam.user_id == user_id,
                                       UserTeam.team_id == team.id))).first()
            if row:
                await session.delete(row)
                current.discard(user_id)

    await session.flush()
    return sorted(current)


@router.get("/scim/v2/Groups/{group_id}")
async def get_group(
    group_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    team = await _scoped_team(session, group_id, workspace.id)
    members = (await session.exec(
        select(UserTeam.user_id).where(UserTeam.team_id == team.id))).all()
    return group_to_scim(team_id=team.id, display_name=team.name,
                         member_ids=list(members), external_id=team.scim_external_id)


@router.patch("/scim/v2/Groups/{group_id}")
async def patch_group(
    group_id: str,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    team = await _scoped_team(session, group_id, workspace.id)
    ops = parse_patch(body)

    if ops.display_name:
        team.name = ops.display_name
        session.add(team)
    members = await _apply_members(session, team, workspace.id,
                                   add=ops.add_members, remove=ops.remove_members,
                                   replace=ops.replace_members)
    await session.commit()
    return group_to_scim(team_id=team.id, display_name=team.name,
                         member_ids=members, external_id=team.scim_external_id)


@router.put("/scim/v2/Groups/{group_id}")
async def replace_group(
    group_id: str,
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    workspace = await _target_workspace(session)
    team = await _scoped_team(session, group_id, workspace.id)
    if body.get("displayName"):
        team.name = str(body["displayName"]).strip()
        session.add(team)
    members = await _apply_members(
        session, team, workspace.id,
        replace=[m.get("value") for m in (body.get("members") or [])
                 if isinstance(m, dict)])
    await session.commit()
    return group_to_scim(team_id=team.id, display_name=team.name,
                         member_ids=members, external_id=team.scim_external_id)


@router.delete("/scim/v2/Groups/{group_id}", status_code=204)
async def delete_group(
    request: Request,
    group_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(scim_principal),
):
    """Delete the team. Deliberately does NOT touch its members' accounts —
    deleting a group in the IdP means "this grouping is gone", not "fire these
    people", and cascading into user deletion would be irreversible."""
    from app.services.audit import record as audit_record

    workspace = await _target_workspace(session)
    team = await _scoped_team(session, group_id, workspace.id)

    for row in (await session.exec(
            select(UserTeam).where(UserTeam.team_id == team.id))).all():
        await session.delete(row)
    from app.models import TeamProjectAccess
    for row in (await session.exec(
            select(TeamProjectAccess).where(TeamProjectAccess.team_id == team.id))).all():
        await session.delete(row)
    await audit_record(
        session, entity_type="team", entity_id=team.id, action="scim_delete",
        workspace_id=workspace.id, actor_type="service", actor_label=ACTOR_LABEL,
        request=request, changes={"name": team.name})
    await session.delete(team)
    await session.commit()
    return Response(status_code=204)
