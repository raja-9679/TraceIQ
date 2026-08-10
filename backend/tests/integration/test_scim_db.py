"""SCIM 2.0 endpoints against a real Postgres — workstream F2.

`tests/test_scim.py` covers the wire format. This covers the part a security
reviewer actually asks about: when Okta says `active: false`, does the person
lose access *now*?

Driven over real HTTP through the ASGI app, so the bearer-token guard, the
routing and the response bodies are all exercised — not just the service
functions. Opt-in like the other live tests:

    ./run-tests-live.sh tests/integration/test_scim_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import NamedTuple

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings              # noqa: E402
from app.models import (                                       # noqa: E402
    RefreshToken, Role, Team, Tenant, User, UserTeam, UserWorkspace, Workspace,
)
from app.services import instance_settings as insvc            # noqa: E402

SCIM_TOKEN = "scim-test-token-not-a-real-secret"
_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False),
                                 future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text(_WIPE))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    """The app under test, driven over ASGI.

    `app.core.database.engine` is module-level and its asyncpg pool binds to
    whichever event loop first used it. Tests here are function-scoped, so the
    pool has to be disposed around each one or the second test fails with
    "attached to a different loop".
    """
    from app.core.database import engine
    from app.main import app

    await engine.dispose()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://scim.test",
                           headers={"Authorization": f"Bearer {SCIM_TOKEN}"}) as c:
        yield c
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_settings_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


def _configure(monkeypatch, **overrides):
    overrides.setdefault("SCIM_TOKEN", SCIM_TOKEN)
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: dict(overrides))
    insvc.invalidate_cache()


class Ws(NamedTuple):
    """Plain ids, not ORM rows.

    `session.rollback()` (see `_resync`) expires every instance in the session,
    so a Workspace object captured before a resync raises MissingGreenlet the
    next time an attribute is touched. Ids are immune.
    """
    id: int
    tenant_id: int


async def _workspace(session, name="Corp Workspace",
                     owner_email="owner@corp.example.com") -> Ws:
    owner = User(email=owner_email, full_name="Owner",
                 hashed_password="x", is_verified=True)
    session.add(owner)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=owner.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name=name, tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    await session.commit()
    return Ws(id=ws.id, tenant_id=tenant.id)


async def _setup(session, monkeypatch, **overrides) -> Ws:
    ws = await _workspace(session)
    _configure(monkeypatch, FEDERATED_WORKSPACE_ID=str(ws.id), **overrides)
    return ws


def _new_user_body(email="dana@corp.example.com", external_id="okta-1"):
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": email,
        "externalId": external_id,
        "name": {"givenName": "Dana", "familyName": "Federated"},
        "emails": [{"value": email, "primary": True}],
        "active": True,
    }


async def _fetch(session, email) -> User | None:
    await _resync(session)
    return (await session.exec(select(User).where(User.email == email))).first()


async def _resync(session):
    """End this session's transaction so rows the app committed become visible.

    The fixture session and the app's request sessions are different
    transactions. expire_all() is not enough — it only forgets loaded state, it
    does not start a new snapshot, and re-loading an expired instance from an
    async session outside a query raises MissingGreenlet.
    """
    await session.rollback()
    session.expire_all()


# --- The guard ---------------------------------------------------------------

async def test_scim_is_closed_when_no_token_is_configured(session, monkeypatch, client):
    # An unconfigured SCIM surface must not be an open provisioning API. 404
    # rather than 401 so it doesn't advertise itself either.
    await _workspace(session)
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: {})
    insvc.invalidate_cache()
    r = await client.get("/scim/v2/Users")
    assert r.status_code == 404


async def test_a_wrong_token_is_refused(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    r = await client.get("/scim/v2/Users",
                         headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401


async def test_a_missing_token_is_refused(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    r = await client.get("/scim/v2/Users", headers={"Authorization": ""})
    assert r.status_code == 401


async def test_scim_needs_a_target_workspace(session, monkeypatch, client):
    # SCIM has nowhere to put people without FEDERATED_WORKSPACE_ID. Minting a
    # tenant per user is exactly what F1 removed, so this fails closed.
    await _workspace(session)
    _configure(monkeypatch)  # token set, no workspace
    r = await client.post("/scim/v2/Users", json=_new_user_body())
    assert r.status_code == 503


# --- Provisioning ------------------------------------------------------------

async def test_create_user_lands_in_the_target_workspace(session, monkeypatch, client):
    ws = await _setup(session, monkeypatch)
    r = await client.post("/scim/v2/Users", json=_new_user_body())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["userName"] == "dana@corp.example.com"
    assert body["active"] is True
    assert body["externalId"] == "okta-1"

    user = await _fetch(session, "dana@corp.example.com")
    assert user is not None
    membership = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == user.id,
                                    UserWorkspace.workspace_id == ws.id))).first()
    assert membership is not None


async def test_created_user_gets_no_tenant_of_their_own(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    before = len((await session.exec(select(Tenant))).all())
    await client.post("/scim/v2/Users", json=_new_user_body())
    await _resync(session)
    assert len((await session.exec(select(Tenant))).all()) == before


async def test_created_user_has_no_usable_password(session, monkeypatch, client):
    # The IdP is the only way in. A blank or predictable hash would be a second,
    # unmanaged door into the account.
    await _setup(session, monkeypatch)
    await client.post("/scim/v2/Users", json=_new_user_body())
    user = await _fetch(session, "dana@corp.example.com")
    from app.core.auth import verify_password
    assert user.hashed_password and not verify_password("", user.hashed_password)


async def test_creating_a_duplicate_returns_409_uniqueness(session, monkeypatch, client):
    # Without scimType=uniqueness Okta retries the create forever.
    await _setup(session, monkeypatch)
    await client.post("/scim/v2/Users", json=_new_user_body())
    r = await client.post("/scim/v2/Users", json=_new_user_body())
    assert r.status_code == 409
    assert r.json()["scimType"] == "uniqueness"


async def test_creating_an_existing_local_user_adopts_them(session, monkeypatch, client):
    # Turning SCIM on for an instance that already has accounts must not 409
    # every one of them forever, and must not create a second row for the same
    # person. The existing account is adopted: linked, not duplicated.
    ws = await _setup(session, monkeypatch)
    existing = User(email="legacy@corp.example.com", full_name="Legacy",
                    hashed_password="x", is_verified=True)
    session.add(existing)
    await session.flush()
    await session.commit()

    r = await client.post("/scim/v2/Users",
                          json=_new_user_body("legacy@corp.example.com", "okta-9"))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == str(existing.id)

    await _resync(session)
    adopted = await _fetch(session, "legacy@corp.example.com")
    assert adopted.scim_external_id == "okta-9"
    assert (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == adopted.id,
                                    UserWorkspace.workspace_id == ws.id))).first()


# --- Deprovisioning: the reason F2 exists ------------------------------------

async def test_patch_active_false_deactivates(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()

    r = await client.patch(f"/scim/v2/Users/{created['id']}", json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "value": {"active": False}}]})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert (await _fetch(session, "dana@corp.example.com")).is_active is False


async def test_entra_stringified_false_deactivates(session, monkeypatch, client):
    # bool("False") is True. If this test passes only because the string was
    # coerced, a deprovisioned Entra user would stay active.
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()

    r = await client.patch(f"/scim/v2/Users/{created['id']}", json={
        "Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
    assert r.status_code == 200
    assert (await _fetch(session, "dana@corp.example.com")).is_active is False


async def test_deactivation_revokes_live_refresh_tokens(session, monkeypatch, client):
    # is_active is re-checked on every request, so access tokens die within
    # minutes — but a live refresh token would keep minting new ones. This is
    # the "keeps their refresh token indefinitely" half of the finding.
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    user_id = int(created["id"])
    session.add(RefreshToken(user_id=user_id, hashed_token="h1", family_id="f1",
                             expires_at=datetime.utcnow() + timedelta(days=30)))
    await session.commit()

    await client.patch(f"/scim/v2/Users/{user_id}", json={
        "Operations": [{"op": "replace", "path": "active", "value": False}]})

    await _resync(session)
    tokens = (await session.exec(
        select(RefreshToken).where(RefreshToken.user_id == user_id))).all()
    assert tokens and all(t.revoked_at is not None for t in tokens)


async def test_delete_deactivates_rather_than_destroying(session, monkeypatch, client):
    # Hard deletion would orphan runs, results and audit history — and IdPs
    # issue DELETE routinely on offboarding. Soft deactivation is both
    # reversible and auditable.
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()

    r = await client.delete(f"/scim/v2/Users/{created['id']}")
    assert r.status_code == 204
    user = await _fetch(session, "dana@corp.example.com")
    assert user is not None and user.is_active is False


async def test_reactivation_works(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    await client.delete(f"/scim/v2/Users/{created['id']}")

    r = await client.patch(f"/scim/v2/Users/{created['id']}", json={
        "Operations": [{"op": "replace", "path": "active", "value": True}]})
    assert r.status_code == 200
    assert (await _fetch(session, "dana@corp.example.com")).is_active is True


async def test_deactivation_is_audited(session, monkeypatch, client):
    # "Who removed this person's access, and when" is the question an auditor
    # asks. It has to survive in the append-only log.
    from app.models import AuditLog
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    await client.delete(f"/scim/v2/Users/{created['id']}")

    await _resync(session)
    rows = (await session.exec(
        select(AuditLog).where(AuditLog.entity_type == "user"))).all()
    actions = {r.action for r in rows}
    assert "scim_deactivate" in actions
    assert any(r.actor_label and "scim" in r.actor_label.lower() for r in rows)


# --- Reads -------------------------------------------------------------------

async def test_lookup_by_username_filter(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    await client.post("/scim/v2/Users", json=_new_user_body())

    r = await client.get('/scim/v2/Users?filter=userName eq "dana@corp.example.com"')
    assert r.status_code == 200
    body = r.json()
    assert body["totalResults"] == 1
    assert body["Resources"][0]["userName"] == "dana@corp.example.com"


async def test_lookup_miss_is_an_empty_list_not_a_404(session, monkeypatch, client):
    # This is how an IdP asks "does this user exist?" before creating them.
    await _setup(session, monkeypatch)
    r = await client.get('/scim/v2/Users?filter=userName eq "nobody@corp.example.com"')
    assert r.status_code == 200
    assert r.json()["totalResults"] == 0


async def test_listing_is_scoped_to_the_target_workspace(session, monkeypatch, client):
    # A SCIM client authenticated for this instance must not enumerate accounts
    # that belong to some other workspace's users.
    ws = await _setup(session, monkeypatch)
    outsider = User(email="outsider@other.example.com", full_name="Outsider",
                    hashed_password="x")
    session.add(outsider)
    await session.flush()
    other_ws = Workspace(name="Other", tenant_id=ws.tenant_id)
    session.add(other_ws)
    await session.flush()
    session.add(UserWorkspace(user_id=outsider.id, workspace_id=other_ws.id,
                              role="member"))
    await session.commit()

    await client.post("/scim/v2/Users", json=_new_user_body())
    body = (await client.get("/scim/v2/Users")).json()
    emails = {r["userName"] for r in body["Resources"]}
    assert "dana@corp.example.com" in emails
    assert "outsider@other.example.com" not in emails


async def test_get_by_id(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    r = await client.get(f"/scim/v2/Users/{created['id']}")
    assert r.status_code == 200 and r.json()["id"] == created["id"]


async def test_get_unknown_id_is_404(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    r = await client.get("/scim/v2/Users/424242")
    assert r.status_code == 404


async def test_get_a_user_outside_the_workspace_is_404(session, monkeypatch, client):
    ws = await _setup(session, monkeypatch)
    outsider = User(email="outsider@other.example.com", full_name="Outsider",
                    hashed_password="x")
    session.add(outsider)
    await session.flush()
    await session.commit()
    r = await client.get(f"/scim/v2/Users/{outsider.id}")
    assert r.status_code == 404


async def test_service_provider_config_is_advertised(session, monkeypatch, client):
    # Okta probes this before its first sync.
    await _setup(session, monkeypatch)
    r = await client.get("/scim/v2/ServiceProviderConfig")
    assert r.status_code == 200
    assert r.json()["patch"]["supported"] is True


# --- PUT ---------------------------------------------------------------------

async def test_put_replaces_the_resource(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()

    body = _new_user_body()
    body["name"] = {"givenName": "Dana", "familyName": "Renamed"}
    body["active"] = False
    r = await client.put(f"/scim/v2/Users/{created['id']}", json=body)
    assert r.status_code == 200
    user = await _fetch(session, "dana@corp.example.com")
    assert user.full_name == "Dana Renamed"
    assert user.is_active is False


# --- Groups → teams -----------------------------------------------------------

async def test_create_group_creates_a_team_in_the_workspace(session, monkeypatch, client):
    ws = await _setup(session, monkeypatch)
    r = await client.post("/scim/v2/Groups", json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "QA Team", "externalId": "okta-grp-1"})
    assert r.status_code == 201, r.text

    await _resync(session)
    team = (await session.exec(select(Team).where(Team.name == "QA Team"))).first()
    assert team is not None and team.workspace_id == ws.id


async def test_group_membership_grants_team_membership(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    user = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    group = (await client.post("/scim/v2/Groups", json={
        "displayName": "QA Team"})).json()

    r = await client.patch(f"/scim/v2/Groups/{group['id']}", json={
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": user["id"]}]}]})
    assert r.status_code == 200

    await _resync(session)
    rows = (await session.exec(
        select(UserTeam).where(UserTeam.user_id == int(user["id"])))).all()
    assert len(rows) == 1


async def test_removing_a_group_member_removes_team_membership(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    user = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    group = (await client.post("/scim/v2/Groups", json={"displayName": "QA Team"})).json()
    await client.patch(f"/scim/v2/Groups/{group['id']}", json={
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": user["id"]}]}]})

    r = await client.patch(f"/scim/v2/Groups/{group['id']}", json={
        "Operations": [{"op": "remove", "path": f'members[value eq "{user["id"]}"]'}]})
    assert r.status_code == 200

    await _resync(session)
    rows = (await session.exec(
        select(UserTeam).where(UserTeam.user_id == int(user["id"])))).all()
    assert rows == []


async def test_group_read_lists_members(session, monkeypatch, client):
    await _setup(session, monkeypatch)
    user = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    group = (await client.post("/scim/v2/Groups", json={"displayName": "QA Team"})).json()
    await client.patch(f"/scim/v2/Groups/{group['id']}", json={
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": user["id"]}]}]})

    r = await client.get(f"/scim/v2/Groups/{group['id']}")
    assert r.status_code == 200
    assert r.json()["members"] == [{"value": user["id"]}]


async def test_deleting_a_group_does_not_delete_its_users(session, monkeypatch, client):
    # Deleting a group in the IdP means "this grouping is gone", not "fire these
    # people". Cascading into user deletion here would be catastrophic and
    # irreversible.
    await _setup(session, monkeypatch)
    user = (await client.post("/scim/v2/Users", json=_new_user_body())).json()
    group = (await client.post("/scim/v2/Groups", json={"displayName": "QA Team"})).json()
    await client.patch(f"/scim/v2/Groups/{group['id']}", json={
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": user["id"]}]}]})

    r = await client.delete(f"/scim/v2/Groups/{group['id']}")
    assert r.status_code == 204

    await _resync(session)
    assert await _fetch(session, "dana@corp.example.com") is not None
    assert (await session.exec(select(Team).where(Team.name == "QA Team"))).first() is None


async def test_group_in_another_workspace_is_invisible(session, monkeypatch, client):
    ws = await _setup(session, monkeypatch)
    other = Workspace(name="Other", tenant_id=ws.tenant_id)
    session.add(other)
    await session.flush()
    foreign = Team(name="Foreign", workspace_id=other.id)
    session.add(foreign)
    await session.flush()
    await session.commit()

    assert (await client.get(f"/scim/v2/Groups/{foreign.id}")).status_code == 404
    body = (await client.get("/scim/v2/Groups")).json()
    assert "Foreign" not in {g["displayName"] for g in body["Resources"]}


# --- Role assignment ---------------------------------------------------------

async def test_created_user_gets_the_configured_default_role(session, monkeypatch, client):
    ws = await _workspace(session)
    _configure(monkeypatch, FEDERATED_WORKSPACE_ID=str(ws.id),
               FEDERATED_DEFAULT_ROLE="Workspace Admin")
    created = (await client.post("/scim/v2/Users", json=_new_user_body())).json()

    await _resync(session)
    membership = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == int(created["id"])))).first()
    role = await session.get(Role, membership.role_id)
    assert role.name == "Workspace Admin"
