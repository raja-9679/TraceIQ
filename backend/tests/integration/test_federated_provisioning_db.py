"""Federated provisioning against a real Postgres — workstream F1.

The pure policy decisions are unit-tested in `tests/test_federated_provisioning.py`.
What cannot be proven there is the part that actually matters: that an SSO login
lands the user in the *configured* workspace instead of minting a tenant, that
two federated users end up in the SAME tenant, and that group mapping is
re-applied on later logins rather than only at creation.

Those are ORM-shaped facts about rows and relationships, so they need a
database. The unit suite deliberately has none (see tests/conftest.py), and CI
has no Postgres service yet — wiring that up is workstream I1/I2. Until then
this module is opt-in:

    TRACEIQ_LIVE_DB=1 DATABASE_URL=postgresql+asyncpg://... pytest \\
        tests/integration/test_federated_provisioning_db.py

`info/HANDOFF.md` documents the scratch-database recipe. Point this at a
scratch database, never at a real one — the fixture TRUNCATEs the tenancy
tables between tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings           # noqa: E402
from app.models import (                                    # noqa: E402
    Role, Team, Tenant, User, UserSystemRole, UserTeam, UserWorkspace, Workspace,
    WorkspaceInvitation,
)
from app.services import instance_settings as insvc         # noqa: E402
from app.services.federation import FederationConfigError   # noqa: E402
from app.services.user_provisioning import (                # noqa: E402
    FederatedProvisioningDenied,
    provision_federated_user,
    sync_federated_access,
)

# Truncating `tenant` with CASCADE would take the `role` table with it
# (Role.tenant_id is a FK, and TRUNCATE CASCADE truncates the whole referencing
# table, not the matching rows), so RBAC is re-seeded after every wipe.
_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    # loop_scope must match the test's loop: pytest.ini sets
    # asyncio_default_fixture_loop_scope=session, and an engine created on the
    # session loop cannot be awaited from a function-scoped test ("attached to a
    # different loop"). The installed pytest-asyncio (0.25) ignores
    # asyncio_default_test_loop_scope, so the tests are function-scoped.
    engine = create_async_engine(
        db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text(_WIPE))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_settings_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


def _configure(monkeypatch, **overrides):
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: dict(overrides))
    insvc.invalidate_cache()


async def _target_workspace(session, *, teams=(), slug="corp") -> Workspace:
    """A tenant + workspace that already exists, as it would in a real install
    (created by whoever set the instance up) before SSO is switched on."""
    owner = User(email=f"owner@{slug}.example.com", full_name="Owner",
                 hashed_password="x", is_verified=True)
    session.add(owner)
    await session.flush()
    tenant = Tenant(name=f"{slug} tenant", owner_id=owner.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name=f"{slug} workspace", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    for name in teams:
        session.add(Team(name=name, workspace_id=ws.id))
    await session.flush()
    return ws


async def _membership(session, user_id, workspace_id) -> UserWorkspace | None:
    return (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == user_id,
                                    UserWorkspace.workspace_id == workspace_id))).first()


async def _role_name(session, uw: UserWorkspace) -> str | None:
    if uw is None or uw.role_id is None:
        return None
    return (await session.get(Role, uw.role_id)).name


async def _team_names(session, user_id) -> list[str]:
    rows = (await session.exec(
        select(Team.name).join(UserTeam, UserTeam.team_id == Team.id)
        .where(UserTeam.user_id == user_id))).all()
    return sorted(rows)


async def _provision(session, monkeypatch, email, groups=(), **overrides):
    _configure(monkeypatch, **overrides)
    return await provision_federated_user(
        session, email=email, full_name=email.split("@")[0],
        hashed_password="unusable", groups=list(groups), commit=False)


# --- The bug F1 exists to fix -----------------------------------------------

async def test_federated_user_joins_the_configured_workspace(session, monkeypatch):
    ws = await _target_workspace(session)
    user = await _provision(session, monkeypatch, "alice@corp.example.com",
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id))

    uw = await _membership(session, user.id, ws.id)
    assert uw is not None, "federated user must be a member of the target workspace"
    assert await _role_name(session, uw) == "Workspace Member"


async def test_federated_user_gets_no_tenant_of_their_own(session, monkeypatch):
    ws = await _target_workspace(session)
    before = len((await session.exec(select(Tenant))).all())

    await _provision(session, monkeypatch, "alice@corp.example.com",
                     FEDERATED_PROVISIONING_MODE="workspace",
                     FEDERATED_WORKSPACE_ID=str(ws.id))

    assert len((await session.exec(select(Tenant))).all()) == before, (
        "provisioning a federated user must not create a Tenant — that is the "
        "bug that turned a 500-person SSO rollout into 500 tenants")


async def test_federated_user_is_not_a_tenant_admin(session, monkeypatch):
    ws = await _target_workspace(session)
    user = await _provision(session, monkeypatch, "alice@corp.example.com",
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id))

    roles = (await session.exec(
        select(UserSystemRole).where(UserSystemRole.user_id == user.id))).all()
    assert roles == [], "an IdP login must not confer tenant administration"


async def test_two_federated_users_share_one_workspace(session, monkeypatch):
    ws = await _target_workspace(session)
    alice = await _provision(session, monkeypatch, "alice@corp.example.com",
                             FEDERATED_PROVISIONING_MODE="workspace",
                             FEDERATED_WORKSPACE_ID=str(ws.id))
    bob = await _provision(session, monkeypatch, "bob@corp.example.com",
                           FEDERATED_PROVISIONING_MODE="workspace",
                           FEDERATED_WORKSPACE_ID=str(ws.id))

    assert await _membership(session, alice.id, ws.id) is not None
    assert await _membership(session, bob.id, ws.id) is not None


# --- Failing closed ----------------------------------------------------------

async def test_workspace_mode_pointing_at_nothing_is_refused(session, monkeypatch):
    # Deleted or mistyped workspace id. Falling back to a per-user tenant here
    # would silently restore the bug in a deployment that thinks it is fixed.
    with pytest.raises(FederationConfigError):
        await _provision(session, monkeypatch, "alice@corp.example.com",
                         FEDERATED_PROVISIONING_MODE="workspace",
                         FEDERATED_WORKSPACE_ID="424242")

    assert (await session.exec(
        select(User).where(User.email == "alice@corp.example.com"))).first() is None


async def test_deny_mode_refuses_to_create_an_account(session, monkeypatch):
    await _target_workspace(session)
    with pytest.raises(FederatedProvisioningDenied):
        await _provision(session, monkeypatch, "stranger@corp.example.com",
                         FEDERATED_PROVISIONING_MODE="deny")

    assert (await session.exec(
        select(User).where(User.email == "stranger@corp.example.com"))).first() is None


async def test_standalone_mode_is_unchanged(session, monkeypatch):
    # Existing installs must upgrade without their SSO accounts changing shape.
    before = len((await session.exec(select(Tenant))).all())
    user = await _provision(session, monkeypatch, "solo@corp.example.com")

    after = (await session.exec(select(Tenant))).all()
    assert len(after) == before + 1
    roles = (await session.exec(
        select(UserSystemRole).where(UserSystemRole.user_id == user.id))).all()
    assert len(roles) == 1, "standalone users stay Tenant Admin of their own tenant"


# --- Group → role ------------------------------------------------------------

async def test_mapped_group_grants_workspace_admin(session, monkeypatch):
    ws = await _target_workspace(session)
    user = await _provision(session, monkeypatch, "root@corp.example.com",
                            groups=["traceiq-admins"],
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id),
                            FEDERATED_GROUP_ROLE_MAP="traceiq-admins=Workspace Admin")

    uw = await _membership(session, user.id, ws.id)
    assert await _role_name(session, uw) == "Workspace Admin"
    # access_service still reads the legacy string column for rows without a
    # role_id, so both must agree.
    assert uw.role == "admin"


async def test_losing_the_group_removes_the_role_on_next_login(session, monkeypatch):
    ws = await _target_workspace(session)
    cfg = dict(FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id),
               FEDERATED_GROUP_ROLE_MAP="traceiq-admins=Workspace Admin")
    user = await _provision(session, monkeypatch, "demoted@corp.example.com",
                            groups=["traceiq-admins"], **cfg)

    _configure(monkeypatch, **cfg)
    await sync_federated_access(session, user, groups=["qa"], commit=False)

    uw = await _membership(session, user.id, ws.id)
    assert await _role_name(session, uw) == "Workspace Member", (
        "create-only mapping means removing someone from the admin group in the "
        "IdP never takes their TraceIQ admin away")


async def test_gaining_the_group_grants_the_role_on_next_login(session, monkeypatch):
    ws = await _target_workspace(session)
    cfg = dict(FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id),
               FEDERATED_GROUP_ROLE_MAP="traceiq-admins=Workspace Admin")
    user = await _provision(session, monkeypatch, "promoted@corp.example.com", **cfg)

    _configure(monkeypatch, **cfg)
    await sync_federated_access(session, user, groups=["traceiq-admins"], commit=False)

    uw = await _membership(session, user.id, ws.id)
    assert await _role_name(session, uw) == "Workspace Admin"


async def test_without_a_group_map_a_manual_promotion_survives(session, monkeypatch):
    # No group map configured = the IdP says nothing about roles, so TraceIQ's
    # own grant is the only source of truth and must not be reverted on login.
    ws = await _target_workspace(session)
    cfg = dict(FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id))
    user = await _provision(session, monkeypatch, "manual@corp.example.com", **cfg)

    admin = (await session.exec(select(Role).where(Role.name == "Workspace Admin"))).first()
    uw = await _membership(session, user.id, ws.id)
    uw.role_id, uw.role = admin.id, "admin"
    session.add(uw)
    await session.flush()

    _configure(monkeypatch, **cfg)
    await sync_federated_access(session, user, groups=[], commit=False)

    assert await _role_name(session, await _membership(session, user.id, ws.id)) \
        == "Workspace Admin"


async def test_sync_adds_a_missing_membership(session, monkeypatch):
    # An account that predates SSO (invited, or SCIM-provisioned into the
    # instance) still has to end up in the federated workspace when it logs in.
    ws = await _target_workspace(session)
    user = User(email="preexisting@corp.example.com", full_name="Pre",
                hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()

    _configure(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id))
    await sync_federated_access(session, user, groups=[], commit=False)

    assert await _membership(session, user.id, ws.id) is not None


# --- Group → team ------------------------------------------------------------

async def test_mapped_group_joins_the_team(session, monkeypatch):
    ws = await _target_workspace(session, teams=["QA Team"])
    user = await _provision(session, monkeypatch, "tester@corp.example.com",
                            groups=["qa"],
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id),
                            FEDERATED_GROUP_TEAM_MAP="qa=QA Team")

    assert await _team_names(session, user.id) == ["QA Team"]


async def test_losing_the_group_leaves_the_team(session, monkeypatch):
    ws = await _target_workspace(session, teams=["QA Team"])
    cfg = dict(FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id),
               FEDERATED_GROUP_TEAM_MAP="qa=QA Team")
    user = await _provision(session, monkeypatch, "tester@corp.example.com",
                            groups=["qa"], **cfg)

    _configure(monkeypatch, **cfg)
    await sync_federated_access(session, user, groups=[], commit=False)

    assert await _team_names(session, user.id) == []


async def test_unmapped_teams_are_left_alone(session, monkeypatch):
    # Teams TraceIQ manages itself are not the IdP's business — only teams that
    # appear in the map are synced.
    ws = await _target_workspace(session, teams=["QA Team", "Hand Picked"])
    cfg = dict(FEDERATED_PROVISIONING_MODE="workspace",
               FEDERATED_WORKSPACE_ID=str(ws.id),
               FEDERATED_GROUP_TEAM_MAP="qa=QA Team")
    user = await _provision(session, monkeypatch, "tester@corp.example.com",
                            groups=["qa"], **cfg)

    picked = (await session.exec(select(Team).where(Team.name == "Hand Picked"))).first()
    session.add(UserTeam(user_id=user.id, team_id=picked.id))
    await session.flush()

    _configure(monkeypatch, **cfg)
    await sync_federated_access(session, user, groups=[], commit=False)

    assert await _team_names(session, user.id) == ["Hand Picked"]


async def test_a_renamed_team_does_not_break_login(session, monkeypatch):
    # A team named in the map but absent from the workspace is a settings
    # problem. Refusing the login would take the whole organisation offline
    # because someone renamed a team, so it degrades to a logged warning.
    ws = await _target_workspace(session)
    user = await _provision(session, monkeypatch, "tester@corp.example.com",
                            groups=["qa"],
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id),
                            FEDERATED_GROUP_TEAM_MAP="qa=Gone Team")

    assert await _membership(session, user.id, ws.id) is not None
    assert await _team_names(session, user.id) == []


async def test_a_team_outside_the_target_workspace_is_ignored(session, monkeypatch):
    # Team names are not unique across workspaces; a map entry must never pull a
    # user into another tenant's team.
    ws = await _target_workspace(session)
    other = await _target_workspace(session, teams=["QA Team"], slug="other")
    assert other.id != ws.id

    user = await _provision(session, monkeypatch, "tester@corp.example.com",
                            groups=["qa"],
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id),
                            FEDERATED_GROUP_TEAM_MAP="qa=QA Team")

    assert await _team_names(session, user.id) == []


# --- Saving the policy -------------------------------------------------------

async def test_saving_an_unusable_policy_is_rejected(session, monkeypatch):
    # The login path fails closed, so the admin has to hear about a typo now
    # rather than when the organisation tries to log in.
    from fastapi import HTTPException

    from app.api.instance_settings import SettingsUpdate, update_instance_settings
    from app.models import InstanceSetting

    _configure(monkeypatch)
    admin = User(email="admin@corp.example.com", full_name="Admin",
                 hashed_password="x", is_instance_admin=True)
    session.add(admin)
    await session.flush()

    with pytest.raises(HTTPException) as raised:
        await update_instance_settings(
            SettingsUpdate(values={"FEDERATED_PROVISIONING_MODE": "workspace"}),
            session=session, admin=admin)
    assert raised.value.status_code == 400

    stored = (await session.exec(
        select(InstanceSetting)
        .where(InstanceSetting.key == "FEDERATED_PROVISIONING_MODE"))).first()
    assert stored is None, "a rejected policy must not be persisted"


async def test_saving_a_workspace_id_that_does_not_exist_is_rejected(session, monkeypatch):
    # By far the likeliest mistake, and the one whose failure mode is worst:
    # every federated login refused with a 503 until someone fixes it.
    from fastapi import HTTPException

    from app.api.instance_settings import SettingsUpdate, update_instance_settings

    _configure(monkeypatch)
    admin = User(email="admin@corp.example.com", full_name="Admin",
                 hashed_password="x", is_instance_admin=True)
    session.add(admin)
    await session.flush()

    with pytest.raises(HTTPException) as raised:
        await update_instance_settings(
            SettingsUpdate(values={"FEDERATED_PROVISIONING_MODE": "workspace",
                                   "FEDERATED_WORKSPACE_ID": 424242}),
            session=session, admin=admin)
    assert raised.value.status_code == 400
    assert "424242" in str(raised.value.detail)


async def test_saving_a_valid_policy_persists_it(session, monkeypatch):
    from app.api.instance_settings import SettingsUpdate, update_instance_settings
    from app.models import InstanceSetting

    ws = await _target_workspace(session)
    _configure(monkeypatch)
    admin = User(email="admin@corp.example.com", full_name="Admin",
                 hashed_password="x", is_instance_admin=True)
    session.add(admin)
    await session.flush()

    await update_instance_settings(
        SettingsUpdate(values={"FEDERATED_PROVISIONING_MODE": "workspace",
                               "FEDERATED_WORKSPACE_ID": ws.id}),
        session=session, admin=admin)

    rows = {r.key: r.value for r in (await session.exec(
        select(InstanceSetting))).all()}
    assert rows["FEDERATED_PROVISIONING_MODE"] == "workspace"
    assert rows["FEDERATED_WORKSPACE_ID"] == str(ws.id)


# --- Invitations -------------------------------------------------------------

async def test_pending_invitations_are_still_honoured(session, monkeypatch):
    # An invite issued before the person's first SSO login must not be lost —
    # it is often how they get project access.
    ws = await _target_workspace(session)
    invited_ws = Workspace(name="Other", tenant_id=ws.tenant_id)
    session.add(invited_ws)
    await session.flush()
    owner = (await session.exec(select(User).where(User.email == "owner@corp.example.com"))).first()
    session.add(WorkspaceInvitation(
        email="invitee@corp.example.com", workspace_id=invited_ws.id, role="member",
        invited_by_id=owner.id, token="tok-fed-1",
        expires_at=datetime.utcnow() + timedelta(days=7)))
    await session.flush()

    user = await _provision(session, monkeypatch, "invitee@corp.example.com",
                            FEDERATED_PROVISIONING_MODE="workspace",
                            FEDERATED_WORKSPACE_ID=str(ws.id))

    assert await _membership(session, user.id, ws.id) is not None
    assert await _membership(session, user.id, invited_ws.id) is not None
