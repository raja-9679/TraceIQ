"""Role lookup and the tenant-scoped role column — workstream F5.

`Role.tenant_id` has existed since the original schema, commented "Null means
system role", but nothing ever creates a tenant-scoped role: no API, no UI. It
is a dead column — and a dead column with teeth, because
`rbac_service.get_role_by_name` matched on name alone and returned whichever row
came back first.

That means the moment anyone *does* create a tenant-scoped role named
"Workspace Admin" — through the API this column is obviously reserved for, or by
hand — `workspace_service.create_workspace` could grant one tenant's custom role
to another tenant's workspace owner. The lookup has to be deterministic before
the feature is built, not after.

    ./run-tests-live.sh tests/integration/test_role_scoping_db.py -q
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings          # noqa: E402
from app.models import Role, Tenant, User                 # noqa: E402
from app.services.rbac_service import rbac_service        # noqa: E402

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


async def _tenant(session, name="Corp") -> Tenant:
    owner = User(email=f"owner-{name}@corp.example.com", full_name="Owner",
                 hashed_password="x")
    session.add(owner)
    await session.flush()
    tenant = Tenant(name=name, owner_id=owner.id)
    session.add(tenant)
    await session.flush()
    return tenant


async def test_system_role_lookup_works(session):
    role = await rbac_service.get_role_by_name(session, "Workspace Admin")
    assert role is not None and role.tenant_id is None


async def test_a_tenant_scoped_role_cannot_shadow_a_system_role(session):
    # The actual danger of the dead column: create_workspace looks up
    # "Workspace Admin" by name and grants whatever comes back. If a tenant's
    # own role could win that lookup, one tenant's custom permissions would be
    # handed to another tenant's workspace owner.
    tenant = await _tenant(session)
    session.add(Role(name="Workspace Admin", tenant_id=tenant.id,
                     description="tenant-scoped impostor"))
    await session.flush()

    role = await rbac_service.get_role_by_name(session, "Workspace Admin")
    assert role.tenant_id is None, "the system role must win a bare name lookup"


async def test_a_tenant_scoped_role_can_be_requested_explicitly(session):
    tenant = await _tenant(session)
    custom = Role(name="Release Manager", tenant_id=tenant.id)
    session.add(custom)
    await session.flush()

    found = await rbac_service.get_role_by_name(session, "Release Manager",
                                                tenant_id=tenant.id)
    assert found is not None and found.tenant_id == tenant.id


async def test_one_tenants_role_is_not_visible_to_another(session):
    a = await _tenant(session, "A")
    b = await _tenant(session, "B")
    session.add(Role(name="Release Manager", tenant_id=a.id))
    await session.flush()

    assert await rbac_service.get_role_by_name(
        session, "Release Manager", tenant_id=b.id) is None


async def test_an_explicit_tenant_lookup_falls_back_to_the_system_role(session):
    # Callers ask for "Workspace Admin" for a tenant that has no override; the
    # system role is the right answer, not None.
    tenant = await _tenant(session)
    role = await rbac_service.get_role_by_name(session, "Workspace Admin",
                                               tenant_id=tenant.id)
    assert role is not None and role.tenant_id is None


async def test_a_tenant_override_wins_an_explicit_tenant_lookup(session):
    # This is the point of the column, once something creates such a role.
    tenant = await _tenant(session)
    session.add(Role(name="Project Editor", tenant_id=tenant.id))
    await session.flush()

    role = await rbac_service.get_role_by_name(session, "Project Editor",
                                               tenant_id=tenant.id)
    assert role.tenant_id == tenant.id


async def test_no_tenant_scoped_roles_exist_after_a_fresh_init(session):
    # Guards the claim in the docs: init_rbac seeds system roles only. If this
    # ever fails, the "Null means system role" invariant has been broken.
    rows = (await session.exec(select(Role).where(Role.tenant_id != None))).all()  # noqa: E711
    assert rows == []
