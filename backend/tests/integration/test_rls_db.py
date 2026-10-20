"""Row-level security as a second wall — WP15a, against a real Postgres.

tests/integration/test_tenant_isolation_db.py proves the Python checks. This
proves what happens when one of them is missing: the same cross-tenant reads
and writes, made with NO application check in the way (straight ORM / SQL, or
the HTTP endpoint with access_service patched to say yes), in a session the
way get_current_principal scopes it for tenant B — and Postgres refuses tenant
A's rows.

It also proves the other half of the decision: sessions that never bind a
scope (Celery, beat, migrations, public endpoints) see everything, even with
the policies installed and RLS enabled.

    ./run-tests-live.sh tests/integration/test_rls_db.py -q
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal, create_access_token  # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    ApiKey, Project, ProjectSecret, SecurityFinding, Tenant, TestCase,
    TestCaseResult, TestRun, TestStatus, TestSuite, User, UserProjectAccess,
    UserSystemRole, UserWorkspace, Workspace,
)
from app.services import rls                                  # noqa: E402
from app.services.rbac_service import rbac_service            # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def maker():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    mk = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with mk() as s:
        await s.exec(text(_WIPE))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
    rls.install()
    yield mk
    await engine.dispose()


class Side(NamedTuple):
    workspace_id: int
    project_id: int
    project2_id: int
    suite_id: int
    case_id: int
    run_id: int
    result_id: int
    secret_id: int
    finding_id: int
    admin_id: int
    guest_id: int
    key_id: int
    project_key_id: int


async def _side(mk, slug: str) -> Side:
    async with mk() as s:
        admin = User(email=f"admin@{slug}.example.com", full_name="Admin",
                     hashed_password="x", is_verified=True)
        guest = User(email=f"guest@{slug}.example.com", full_name="Guest",
                     hashed_password="x", is_verified=True)
        s.add_all([admin, guest])
        await s.flush()
        tenant = Tenant(name=slug, owner_id=admin.id)
        s.add(tenant)
        await s.flush()
        ws = Workspace(name=f"{slug} ws", tenant_id=tenant.id)
        s.add(ws)
        await s.flush()
        ws_admin = await rbac_service.get_role_by_name(s, "Workspace Admin")
        tenant_admin = await rbac_service.get_role_by_name(s, "Tenant Admin")
        s.add_all([
            UserSystemRole(user_id=admin.id, role_id=tenant_admin.id, tenant_id=tenant.id),
            UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin",
                          role_id=ws_admin.id),
        ])
        p1 = Project(name=f"{slug} web", workspace_id=ws.id)
        p2 = Project(name=f"{slug} api", workspace_id=ws.id)
        s.add_all([p1, p2])
        await s.flush()
        # A guest: no workspace membership, one direct project grant.
        s.add(UserProjectAccess(user_id=guest.id, project_id=p1.id, access_level="viewer"))
        suite = TestSuite(name=f"{slug} suite", project_id=p1.id)
        s.add(suite)
        await s.flush()
        case = TestCase(name=f"{slug} case", test_suite_id=suite.id, project_id=p1.id, steps=[])
        run = TestRun(project_id=p1.id, test_suite_id=suite.id, status=TestStatus.FAILED)
        s.add_all([case, run])
        await s.flush()
        result = TestCaseResult(test_run_id=run.id, test_name="pay", status=TestStatus.FAILED,
                                duration_ms=1.0, error_message=f"{slug} stack trace")
        secret = ProjectSecret(project_id=p1.id, key="PASSWORD", value_encrypted="v1:x")
        finding = SecurityFinding(run_id=run.id, project_id=p1.id, category="dast",
                                  severity="high", title=f"{slug} finding")
        key = ApiKey(workspace_id=ws.id, name=f"{slug}-ci", prefix=f"tiq_{slug[:3]}w",
                     hashed_key=f"h-{slug}-w", created_by_id=admin.id)
        pkey = ApiKey(workspace_id=ws.id, project_id=p2.id, name=f"{slug}-p2",
                      prefix=f"tiq_{slug[:3]}p", hashed_key=f"h-{slug}-p", created_by_id=admin.id)
        s.add_all([result, secret, finding, key, pkey])
        await s.flush()
        out = Side(ws.id, p1.id, p2.id, suite.id, case.id, run.id, result.id, secret.id,
                   finding.id, admin.id, guest.id, key.id, pkey.id)
        await s.commit()
        return out


@pytest_asyncio.fixture(loop_scope="function")
async def sides(maker):
    return await _side(maker, "acme"), await _side(maker, "globex")


@pytest.fixture
def enforce(monkeypatch):
    monkeypatch.setattr(rls, "current_mode", lambda: "enforce")


async def _bound(mk, user_id: int, *, key_id: int | None = None) -> AsyncSession:
    """A session scoped exactly as get_current_principal scopes a request."""
    s = mk()
    user = await s.get(User, user_id)
    key = await s.get(ApiKey, key_id) if key_id else None
    await s.commit()
    await rls.bind_principal(s, AuthPrincipal(user=user, api_key=key))
    return s


# ---------------------------------------------------------------------------
# The installation itself
# ---------------------------------------------------------------------------

async def test_every_planned_table_has_its_policy_and_rls_enabled(maker):
    async with maker() as s:
        rows = (await s.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
            "AND p.polname = 'traceiq_tenant') FROM pg_class c "
            "WHERE c.relname = ANY(:t) AND c.relkind = 'r'"), {"t": list(rls.RLS_POLICIES)})).all()
    state = {r[0]: r[1:] for r in rows}
    assert set(state) == set(rls.RLS_POLICIES)
    for table, (enabled, forced, has_policy) in state.items():
        assert enabled and has_policy, table
        # FORCE would put the policy quals into every owner query (Celery,
        # beat, pg_dump) — only the fallback mechanism, only when enforcing.
        assert not forced, table


async def test_the_restricted_role_is_usable_by_the_app_user(maker):
    async with maker() as s:
        mech = await s.run_sync(lambda ss: rls.detect_sync(ss.connection()))
    assert mech.kind == "role", mech
    assert mech.role.endswith("_rls")


# ---------------------------------------------------------------------------
# Sessions that never bind a scope are unaffected
# ---------------------------------------------------------------------------

async def test_an_unscoped_session_sees_every_tenant(maker, sides, enforce):
    # Celery, beat, migrations, scripts and public endpoints never bind, so
    # even with RLS_MODE=enforce they read across tenants as before.
    async with maker() as s:
        for model in (Project, TestSuite, TestCase, TestRun, TestCaseResult,
                      ProjectSecret, SecurityFinding):
            n = len((await s.exec(select(model))).all())
            assert n >= 2, model.__tablename__


async def test_off_mode_binds_nothing_and_filters_nothing(maker, sides, monkeypatch):
    monkeypatch.setattr(rls, "current_mode", lambda: "off")
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        assert rls.get_scope(s) is None
        assert await s.get(TestRun, acme.run_id) is not None
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Enforce: the second wall, with no Python check in the way
# ---------------------------------------------------------------------------

async def test_enforce_hides_the_other_tenants_rows_from_direct_queries(maker, sides, enforce):
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        assert rls.get_scope(s).workspace_ids == (globex.workspace_id,)
        # By primary key — the "id came from the URL and nobody checked" bug.
        for model, a_id, b_id in [
            (Project, acme.project_id, globex.project_id),
            (TestSuite, acme.suite_id, globex.suite_id),
            (TestCase, acme.case_id, globex.case_id),
            (TestRun, acme.run_id, globex.run_id),
            (TestCaseResult, acme.result_id, globex.result_id),
            (ProjectSecret, acme.secret_id, globex.secret_id),
            (SecurityFinding, acme.finding_id, globex.finding_id),
        ]:
            assert (await s.exec(select(model).where(model.id == a_id))).first() is None, \
                model.__tablename__
            assert (await s.exec(select(model).where(model.id == b_id))).first() is not None, \
                model.__tablename__
        # The "forgot the WHERE" bug: a list with no tenant filter at all.
        runs = (await s.exec(select(TestRun))).all()
        assert {r.project_id for r in runs} == {globex.project_id}
        # Raw SQL is covered too — the policy is in the database, not the ORM.
        leaked = (await s.execute(text(
            "SELECT count(*) FROM testcaseresult r JOIN testrun t ON t.id = r.test_run_id "
            "WHERE r.error_message LIKE 'acme%'"))).scalar()
        assert leaked == 0
        secrets = (await s.execute(text("SELECT key FROM projectsecret"))).scalars().all()
        assert secrets == ["PASSWORD"]
    finally:
        await s.close()


async def test_the_scope_survives_commit(maker, sides, enforce):
    # Endpoints commit mid-request; SET LOCAL ends with the transaction, so the
    # after_begin listener has to re-apply it to the next one.
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        await s.commit()
        assert await s.get(TestRun, acme.run_id) is None
        await s.rollback()
        assert (await s.execute(text("SELECT count(*) FROM testrun"))).scalar() == 1
        assert (await s.execute(text("SELECT current_user"))).scalar() == rls._cached_mechanism().role
    finally:
        await s.close()


async def test_enforce_refuses_writes_into_the_other_tenant(maker, sides, enforce):
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        s.add(TestRun(project_id=acme.project_id, status=TestStatus.PENDING))
        with pytest.raises((DBAPIError, ProgrammingError)) as exc:
            await s.flush()
        assert "row-level security" in str(exc.value)
        await s.rollback()
        # UPDATE / DELETE of rows it cannot see touch nothing.
        n = (await s.execute(text(
            "UPDATE projectsecret SET value_encrypted = 'stolen' WHERE project_id = :p"),
            {"p": acme.project_id})).rowcount
        assert n == 0
        n = (await s.execute(text("DELETE FROM testcaseresult WHERE id = :i"),
                             {"i": acme.result_id})).rowcount
        assert n == 0
        # Moving one of its own rows INTO the other tenant is refused too.
        with pytest.raises((DBAPIError, ProgrammingError)):
            await s.execute(text("UPDATE testsuite SET project_id = :a WHERE id = :b"),
                            {"a": acme.project_id, "b": globex.suite_id})
        await s.rollback()
    finally:
        await s.close()
    async with maker() as owner:
        sec = await owner.get(ProjectSecret, acme.secret_id)
        assert sec.value_encrypted == "v1:x"
        assert await owner.get(TestCaseResult, acme.result_id) is not None


async def test_a_guest_with_one_project_grant_reaches_that_workspace_only(maker, sides, enforce):
    acme, globex = sides
    s = await _bound(maker, acme.guest_id)
    try:
        assert rls.get_scope(s).workspace_ids == (acme.workspace_id,)
        assert await s.get(TestRun, acme.run_id) is not None
        assert await s.get(TestRun, globex.run_id) is None
    finally:
        await s.close()


async def test_an_api_key_is_held_to_its_workspace_and_project(maker, sides, enforce):
    acme, globex = sides
    s = await _bound(maker, globex.admin_id, key_id=globex.project_key_id)
    try:
        scope = rls.get_scope(s)
        assert scope.project_ids == (globex.project2_id,)
        # The key's own creator can see project 1, the project-scoped key can't.
        assert await s.get(TestRun, globex.run_id) is None
        assert await s.get(Project, globex.project_id) is None
        assert await s.get(Project, globex.project2_id) is not None
    finally:
        await s.close()
    s = await _bound(maker, globex.admin_id, key_id=globex.key_id)
    try:
        assert await s.get(TestRun, globex.run_id) is not None
        assert await s.get(TestRun, acme.run_id) is None
    finally:
        await s.close()


async def test_bypass_restores_the_owner_view_mid_transaction(maker, sides, enforce):
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        assert await s.get(TestRun, acme.run_id) is None
        await rls.bypass(s, "instance_admin")
        assert (await s.execute(text("SELECT count(*) FROM testrun"))).scalar() == 2
        await s.commit()
        assert (await s.execute(text("SELECT count(*) FROM testrun"))).scalar() == 2
    finally:
        await s.close()


async def test_creating_a_workspace_extends_the_scope_for_the_rest_of_the_request(
        maker, sides, enforce):
    from app.services.workspace_service import workspace_service
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        ws = await workspace_service.create_workspace(
            name="fresh", owner_id=globex.admin_id, session=s, commit=False)
        s.add(Project(name="fresh project", workspace_id=ws.id))
        await s.flush()   # would violate the policy without rls.extend()
        await s.commit()
        assert ws.id in rls.get_scope(s).workspace_ids
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Log mode: counted, never refused
# ---------------------------------------------------------------------------

async def test_log_mode_counts_a_cross_tenant_load_without_refusing_it(
        maker, sides, monkeypatch):
    monkeypatch.setattr(rls, "current_mode", lambda: "log")
    monkeypatch.setattr(rls, "sample_percent", lambda: 100)
    await rls.reset_violation_counts()
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        run = await s.get(TestRun, acme.run_id)        # the "forgot its check" bug
        assert run is not None                         # log mode refuses nothing
        own = await s.get(TestRun, globex.run_id)
        assert own is not None
        results = (await s.exec(select(TestCaseResult))).all()
        assert len(results) == 2
    finally:
        await rls.finish(s)
        await s.close()
    counts = await rls.violation_counts()
    assert counts["by_table"].get("testrun") == 1
    assert counts["by_table"].get("testcaseresult") == 1
    await rls.reset_violation_counts()


async def test_log_mode_counts_a_cross_tenant_write(maker, sides, monkeypatch):
    monkeypatch.setattr(rls, "current_mode", lambda: "log")
    monkeypatch.setattr(rls, "sample_percent", lambda: 100)
    await rls.reset_violation_counts()
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        s.add(TestSuite(name="planted", project_id=acme.project_id))
        await s.commit()
    finally:
        await rls.finish(s)
        await s.close()
    assert (await rls.violation_counts())["by_table"].get("testsuite") == 1
    await rls.reset_violation_counts()


async def test_log_mode_reports_nothing_for_a_clean_request(maker, sides, monkeypatch):
    monkeypatch.setattr(rls, "current_mode", lambda: "log")
    monkeypatch.setattr(rls, "sample_percent", lambda: 100)
    await rls.reset_violation_counts()
    acme, globex = sides
    s = await _bound(maker, globex.admin_id)
    try:
        await s.get(TestRun, globex.run_id)
        (await s.exec(select(TestSuite).where(TestSuite.project_id == globex.project_id))).all()
    finally:
        await rls.finish(s)
        await s.close()
    assert (await rls.violation_counts())["total"] == 0


# ---------------------------------------------------------------------------
# Through the real request path
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(loop_scope="function")
async def client():
    from httpx import ASGITransport, AsyncClient
    from app.core.database import engine
    from app.main import app
    await engine.dispose()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://rls.test") as c:
        yield c
    await engine.dispose()


def _bearer(email: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


async def test_http_with_the_python_check_bypassed_postgres_still_denies(
        maker, sides, client, enforce, monkeypatch):
    acme, globex = sides
    from app.services.access_service import AccessService

    async def yes(*a, **k):
        return True
    # The first wall knocked down: every project check now says yes.
    monkeypatch.setattr(AccessService, "has_project_access", staticmethod(yes))
    h = _bearer("admin@globex.example.com")
    own = await client.get(f"/api/runs/{globex.run_id}", headers=h)
    assert own.status_code == 200, own.text
    other = await client.get(f"/api/runs/{acme.run_id}", headers=h)
    assert other.status_code == 404, other.text


async def test_http_without_rls_the_bypassed_check_would_have_leaked(
        maker, sides, client, monkeypatch):
    # The control for the test above: same bypass, RLS off → the row comes
    # back. Without this, the 404 above could be a broken fixture.
    monkeypatch.setattr(rls, "current_mode", lambda: "off")
    acme, globex = sides
    from app.services.access_service import AccessService

    async def yes(*a, **k):
        return True
    monkeypatch.setattr(AccessService, "has_project_access", staticmethod(yes))
    r = await client.get(f"/api/runs/{acme.run_id}", headers=_bearer("admin@globex.example.com"))
    assert r.status_code == 200


async def test_http_instance_admin_endpoints_are_not_scoped(maker, sides, client, enforce):
    acme, globex = sides
    # acme is the first tenant, so its Tenant Admin is the instance admin.
    r = await client.get("/api/admin/rls/status", headers=_bearer("admin@acme.example.com"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "enforce"
    assert body["mechanism"] == "role"
    assert body["policies_installed"] is True and body["enforcing"] is True
    r = await client.get("/api/admin/rls/status", headers=_bearer("admin@globex.example.com"))
    assert r.status_code == 403


async def test_http_own_tenant_lists_still_work_under_enforce(maker, sides, client, enforce):
    acme, globex = sides
    h = _bearer("admin@globex.example.com")
    r = await client.get(f"/api/runs?project_id={globex.project_id}", headers=h)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/suites?project_id={globex.project_id}", headers=h)
    assert r.status_code == 200, r.text
    names = [x["name"] for x in r.json()]
    assert "globex suite" in names and "acme suite" not in names
