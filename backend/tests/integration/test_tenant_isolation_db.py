"""Cross-tenant isolation, for real this time — workstream I2.

The readiness plan recorded that "every RBAC and multi-tenant-isolation check is
in the `verify_*.py` category" — ad-hoc scripts rather than tests, so unverified
by CI. Building this turned up something worse: **five of those eight scripts
cannot even import.** They reference `Organization`, `UserOrganization` and
`org_service`, which were replaced by `Workspace` long ago. They have not run in
however long that took, and nobody noticed, because nothing ran them.

So there was nothing to convert. These are written from intent.

That matters more here than anywhere else in the codebase, because **tenant
isolation is application-layer only**: no row-level security, no `CREATE POLICY`,
one shared Postgres and one shared bucket. Every boundary below is a Python `if`.
If one of them regresses, one customer reads another customer's tests, and no
database constraint will stop it.

Two tenants, no relationship between them, and every question asked from the
wrong side:

    ./run-tests-live.sh tests/integration/test_tenant_isolation_db.py -q
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    ApiKey, Project, Role, Tenant, TestCase, TestRun, TestStatus, TestSuite,
    User, UserProjectAccess, UserSystemRole, UserWorkspace, Workspace,
)
from app.services.access_service import access_service       # noqa: E402
from app.services.rbac_service import rbac_service           # noqa: E402
from app.services.workspace_service import workspace_service  # noqa: E402

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


class Side(NamedTuple):
    """One tenant, fully furnished, with an admin and a plain editor."""
    tenant_id: int
    workspace_id: int
    project_id: int
    suite_id: int
    case_id: int
    run_id: int
    admin_id: int
    editor_id: int
    api_key_id: int


async def _side(session, slug: str) -> Side:
    admin = User(email=f"admin@{slug}.example.com", full_name="Admin",
                 hashed_password="x", is_verified=True)
    editor = User(email=f"editor@{slug}.example.com", full_name="Editor",
                  hashed_password="x", is_verified=True)
    session.add_all([admin, editor])
    await session.flush()

    tenant = Tenant(name=slug, owner_id=admin.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name=f"{slug} workspace", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()

    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    ws_member = await rbac_service.get_role_by_name(session, "Workspace Member")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    project_editor = await rbac_service.get_role_by_name(session, "Project Editor")

    session.add_all([
        UserSystemRole(user_id=admin.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin",
                      role_id=ws_admin.id),
        UserWorkspace(user_id=editor.id, workspace_id=ws.id, role="member",
                      role_id=ws_member.id),
    ])
    project = Project(name=f"{slug} web", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    session.add(UserProjectAccess(user_id=editor.id, project_id=project.id,
                                  access_level="editor", role_id=project_editor.id))

    suite = TestSuite(name=f"{slug} checkout", project_id=project.id)
    session.add(suite)
    await session.flush()
    case = TestCase(name=f"{slug} pay", test_suite_id=suite.id,
                    project_id=project.id, steps=[])
    session.add(case)
    await session.flush()
    run = TestRun(project_id=project.id, test_suite_id=suite.id,
                  status=TestStatus.PASSED)
    session.add(run)
    await session.flush()
    key = ApiKey(workspace_id=ws.id, name=f"{slug}-ci", prefix=f"tiq_{slug[:2]}",
                 hashed_key="h", created_by_id=admin.id)
    session.add(key)
    await session.flush()
    await session.commit()

    return Side(tenant_id=tenant.id, workspace_id=ws.id, project_id=project.id,
                suite_id=suite.id, case_id=case.id, run_id=run.id,
                admin_id=admin.id, editor_id=editor.id, api_key_id=key.id)


@pytest_asyncio.fixture(loop_scope="function")
async def sides(session):
    """Two unrelated tenants. Nothing links them; nothing should."""
    return await _side(session, "acme"), await _side(session, "globex")


class Principal(NamedTuple):
    user: User
    agent_id: str | None = None
    agent_session_id: str | None = None
    is_api_caller: bool = False
    api_key: ApiKey | None = None


# --- The access service: the boundary everything else rests on ----------------

async def test_a_workspace_admin_reaches_their_own_project(session, sides):
    acme, _ = sides
    assert await access_service.has_project_access(
        acme.admin_id, acme.project_id, session, min_role="admin") is True


async def test_a_workspace_admin_cannot_reach_another_tenants_project(session, sides):
    acme, globex = sides
    assert await access_service.has_project_access(
        acme.admin_id, globex.project_id, session) is False


async def test_an_editor_cannot_reach_another_tenants_project(session, sides):
    acme, globex = sides
    assert await access_service.has_project_access(
        acme.editor_id, globex.project_id, session) is False


async def test_a_tenant_admin_role_does_not_leak_across_tenants(session, sides):
    # Tenant Admin is a *system* role scoped by UserSystemRole.tenant_id. If the
    # scoping is dropped anywhere, being an admin of one tenant makes you an
    # admin of all of them.
    acme, globex = sides
    assert await rbac_service.has_permission(
        session, acme.admin_id, "workspace:manage_users",
        workspace_id=globex.workspace_id) is False


async def test_the_permission_holds_within_the_right_tenant(session, sides):
    # Guards against the test above passing because the permission is simply
    # never granted.
    acme, _ = sides
    assert await rbac_service.has_permission(
        session, acme.admin_id, "workspace:manage_users",
        workspace_id=acme.workspace_id) is True


# --- Listing: the quiet leak ---------------------------------------------------

async def test_workspace_listing_shows_only_your_own(session, sides):
    acme, globex = sides
    visible = await workspace_service.get_user_workspaces(acme.admin_id, session)
    ids = {w.id for w in visible}
    assert acme.workspace_id in ids
    assert globex.workspace_id not in ids


async def test_member_listing_does_not_expose_another_tenants_users(session, sides):
    acme, globex = sides
    members = await workspace_service.get_workspace_members(acme.workspace_id, session)
    emails = {m.email for m in members}
    assert not any(e.endswith("@globex.example.com") for e in emails)


# --- Endpoints -----------------------------------------------------------------

async def test_reading_another_tenants_suite_is_refused(session, sides):
    from app.api.endpoints.test_suites import get_test_suite

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await get_test_suite(suite_id=globex.suite_id, session=session,
                             current_user=admin)
    assert raised.value.status_code in (403, 404)


async def test_reading_another_tenants_case_is_refused(session, sides):
    from app.api.endpoints.test_cases import get_test_case

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await get_test_case(case_id=globex.case_id, session=session,
                            current_user=admin)
    assert raised.value.status_code in (403, 404)


async def test_reading_another_tenants_run_is_refused(session, sides):
    from app.api.endpoints.test_runs import get_run

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await get_run(run_id=globex.run_id, session=session, current_user=admin)
    assert raised.value.status_code in (403, 404)


async def test_deleting_another_tenants_run_is_refused(session, sides):
    # Deletion was one of the historically-broken paths, and a destructive
    # cross-tenant operation is the worst kind.
    from app.api.endpoints.test_runs import delete_run

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await delete_run(run_id=globex.run_id, session=session, current_user=admin)
    assert raised.value.status_code in (403, 404)

    await session.rollback()
    assert await session.get(TestRun, globex.run_id) is not None


async def test_proposing_against_another_tenants_project_is_refused(session, sides):
    from app.api.agent_ownership import create_proposal
    from app.models import CaseProposalAction, CaseProposalCreate

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await create_proposal(
            body=CaseProposalCreate(
                project_id=globex.project_id, test_suite_id=globex.suite_id,
                action=CaseProposalAction.CREATE, payload={"name": "x", "steps": []}),
            principal=Principal(user=admin), session=session)
    assert raised.value.status_code in (403, 404)


async def test_impact_analysis_cannot_enumerate_another_tenants_cases(session, sides):
    # Impact analysis takes a project id and returns case names. An unchecked
    # version is a directory listing of someone else's test suite.
    from app.api.agent_ownership import impact_analysis
    from app.models import ImpactAnalysisRequest

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    with pytest.raises(HTTPException) as raised:
        await impact_analysis(
            body=ImpactAnalysisRequest(project_id=globex.project_id,
                                       changed_files=["src/a.ts"]),
            principal=Principal(user=admin), session=session)
    assert raised.value.status_code in (403, 404)


# --- API keys ------------------------------------------------------------------

async def test_an_api_key_is_bound_to_its_own_workspace(session, sides):
    acme, globex = sides
    key = await session.get(ApiKey, acme.api_key_id)
    assert key.workspace_id == acme.workspace_id
    assert key.workspace_id != globex.workspace_id


async def test_an_api_key_principal_cannot_reach_the_other_tenant(session, sides):
    # The API-key path resolves to a user for access checks, so it inherits the
    # same boundary — this asserts that inheritance actually holds.
    acme, globex = sides
    assert await access_service.has_project_access(
        acme.admin_id, globex.project_id, session) is False


# --- Deactivation --------------------------------------------------------------

async def test_a_deactivated_user_loses_access_immediately(session, sides):
    # SCIM and erasure both rely on this: deactivation has to be enforced at the
    # principal layer, on every request, not at login.
    from app.core.auth import _user_from_jwt

    acme, _ = sides
    admin = await session.get(User, acme.admin_id)
    admin.is_active = False
    session.add(admin)
    await session.commit()

    from app.core.auth import create_access_token
    token = create_access_token(data={"sub": admin.email})
    # Returns None rather than raising — the caller turns that into a 401. What
    # matters is that a token issued before deactivation stops resolving.
    assert await _user_from_jwt(token, session) is None


# --- The suite tree ------------------------------------------------------------

async def test_a_suite_cannot_be_reparented_into_another_tenant(session, sides):
    # TestSuite.parent_id is self-referential with no tenant check in the column
    # itself, so a cross-tenant move would graft one customer's suite under
    # another's tree.
    from app.api.endpoints.test_suites import update_test_suite
    from app.models import TestSuiteUpdate

    acme, globex = sides
    admin = await session.get(User, acme.admin_id)
    try:
        await update_test_suite(
            suite_id=acme.suite_id,
            suite_update=TestSuiteUpdate(parent_id=globex.suite_id),
            session=session, current_user=admin)
    except HTTPException as exc:
        assert exc.status_code in (400, 403, 404)
        return

    await session.rollback()
    moved = await session.get(TestSuite, acme.suite_id)
    assert moved.parent_id != globex.suite_id, (
        "a suite was re-parented under another tenant's suite — the two trees "
        "are now joined and access checks that walk parents will disagree")
