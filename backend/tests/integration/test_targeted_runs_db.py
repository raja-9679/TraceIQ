"""Targeted runs against a real Postgres.

    ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q
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

from app.core.auth import AuthPrincipal                      # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    ExecutionMode, FlakeRecord, Project, Tenant, TestCase, TestRun, TestSuite,
    User, UserSystemRole, UserWorkspace, Workspace,
)
from app.services.rbac_service import rbac_service           # noqa: E402
from app.services.test_service import test_service           # noqa: E402


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


class Scene(NamedTuple):
    user: User
    project_id: int
    other_project_id: int
    root_id: int          # root suite (SEPARATE)
    child_id: int         # child of root (CONTINUOUS)
    sibling_id: int       # another top-level suite in the same project
    root_cases: list      # 2 cases in root
    child_cases: list     # 2 cases in child
    sibling_case: int
    foreign_case: int     # case in another project


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session) -> Scene:
    user = User(email="dev@corp.example.com", full_name="Dev", hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=user.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=user.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
    ])
    project = Project(name="Shop", workspace_id=ws.id)
    other = Project(name="Other", workspace_id=ws.id)
    session.add_all([project, other])
    await session.flush()
    root = TestSuite(name="Checkout", project_id=project.id, execution_mode=ExecutionMode.SEPARATE,
                     settings={"headers": {"X-Suite": "root"}})
    sibling = TestSuite(name="Search", project_id=project.id)
    foreign_suite = TestSuite(name="Elsewhere", project_id=other.id)
    session.add_all([root, sibling, foreign_suite])
    await session.flush()
    child = TestSuite(name="Payments", project_id=project.id, parent_id=root.id,
                      execution_mode=ExecutionMode.CONTINUOUS,
                      settings={"headers": {"X-Suite": "child"}})
    session.add(child)
    await session.flush()

    def case(name, suite, proj):
        return TestCase(name=name, test_suite_id=suite.id, project_id=proj.id, steps=[])
    rc = [case("cart", root, project), case("address", root, project)]
    cc = [case("card", child, project), case("upi", child, project)]
    sc = case("query", sibling, project)
    fc = case("other", foreign_suite, other)
    session.add_all(rc + cc + [sc, fc])
    await session.commit()
    return Scene(user=user, project_id=project.id, other_project_id=other.id,
                 root_id=root.id, child_id=child.id, sibling_id=sibling.id,
                 root_cases=[c.id for c in rc], child_cases=[c.id for c in cc],
                 sibling_case=sc.id, foreign_case=fc.id)


async def test_subtree_covers_every_descendant_in_any_mode(session, scene):
    ids = await test_service.subtree_suite_ids(scene.root_id, session)
    assert set(ids) == {scene.root_id, scene.child_id}
    cases = await test_service.subtree_cases(scene.root_id, session)
    assert {c.id for c in cases} == set(scene.root_cases + scene.child_cases)


from app.api.endpoints import test_runs            # noqa: E402


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    """Runs are created and committed; nothing is sent to Celery."""
    from app import worker
    monkeypatch.setattr(worker.run_test_suite, "delay", lambda *a, **kw: None)


async def _create(session, scene, *, case_ids=None, case_id=None, tags=None, browser=None):
    return await test_runs.create_run(
        suite_id=scene.root_id, case_id=case_id, browser=browser, device=None, tags=tags,
        context=test_runs.RunCreateContext(case_ids=case_ids, git_commit="abc123"),
        session=session, principal=AuthPrincipal(user=scene.user, api_key=None))


async def test_targeted_runs_only_the_selected_cases(session, scene):
    picked = [scene.root_cases[0], scene.child_cases[1]]
    runs = await _create(session, scene, case_ids=picked, browser=["chromium"])
    assert sorted(r.test_case_id for r in runs) == sorted(picked)
    assert {r.selection_mode for r in runs} == {"targeted"}
    assert {r.selection_total for r in runs} == {4}   # root + child cases


async def test_each_case_uses_its_own_suite_settings(session, scene):
    runs = await _create(session, scene, case_ids=[scene.child_cases[0]], browser=["chromium"])
    assert runs[0].test_suite_id == scene.child_id
    assert runs[0].request_headers.get("X-Suite") == "child"
    assert "Payments" in (runs[0].suite_name or "")


async def test_duplicates_are_collapsed(session, scene):
    c = scene.root_cases[0]
    runs = await _create(session, scene, case_ids=[c, c, c], browser=["chromium"])
    assert [r.test_case_id for r in runs] == [c]


async def test_rejects_cases_outside_the_suite(session, scene):
    for bad in (scene.sibling_case, scene.foreign_case, 999999):
        with pytest.raises(HTTPException) as exc:
            await _create(session, scene, case_ids=[scene.root_cases[0], bad])
        assert exc.value.status_code == 400
        assert str(bad) in str(exc.value.detail)
    await session.rollback()
    assert (await session.exec(select(TestRun))).all() == []


async def test_rejects_empty_oversized_and_ambiguous_selections(session, scene):
    for kwargs in ({"case_ids": []},
                   {"case_ids": list(range(1, 502))},
                   {"case_ids": [scene.root_cases[0]], "case_id": scene.root_cases[1]},
                   {"case_ids": [scene.root_cases[0]], "tags": ["smoke"]}):
        with pytest.raises(HTTPException) as exc:
            await _create(session, scene, **kwargs)
        assert exc.value.status_code == 400


async def test_quarantined_cases_are_left_out(session, scene):
    q, ok = scene.root_cases
    session.add(FlakeRecord(test_case_id=q, is_quarantined=True))
    await session.commit()
    runs = await _create(session, scene, case_ids=[q, ok], browser=["chromium"])
    assert [r.test_case_id for r in runs] == [ok]


async def test_all_quarantined_selection_is_refused(session, scene):
    q = scene.root_cases[0]
    session.add(FlakeRecord(test_case_id=q, is_quarantined=True))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _create(session, scene, case_ids=[q])
    assert exc.value.status_code == 400
    assert "quarantined" in exc.value.detail


async def test_full_and_single_case_runs_are_stamped(session, scene):
    full = await _create(session, scene, browser=["chromium"])
    assert full and {r.selection_mode for r in full} == {"full"}
    assert {r.selection_total for r in full} == {4}
    single = await _create(session, scene, case_id=scene.root_cases[0], browser=["chromium"])
    assert {r.selection_mode for r in single} == {"case"}


from app.api import agent_ownership                 # noqa: E402
from app.models import ImpactAnalysisRequest        # noqa: E402


async def _map(session, case_id, paths):
    case = await session.get(TestCase, case_id)
    case.code_paths = paths
    session.add(case)
    await session.commit()


async def _impact(session, scene, files, suite_id=None):
    return await agent_ownership.impact_analysis(
        ImpactAnalysisRequest(project_id=scene.project_id, changed_files=files, suite_id=suite_id),
        principal=AuthPrincipal(user=scene.user, api_key=None), session=session)


async def test_suite_scope_limits_matches(session, scene):
    await _map(session, scene.root_cases[0], ["src/cart/"])
    await _map(session, scene.sibling_case, ["src/cart/"])
    scoped = await _impact(session, scene, ["src/cart/a.ts"], suite_id=scene.root_id)
    assert [c.id for c in scoped.matched_cases] == [scene.root_cases[0]]
    unscoped = await _impact(session, scene, ["src/cart/a.ts"])
    assert {c.id for c in unscoped.matched_cases} == {scene.root_cases[0], scene.sibling_case}


async def test_file_matched_only_outside_suite_is_unmatched(session, scene):
    await _map(session, scene.sibling_case, ["src/search/"])
    r = await _impact(session, scene, ["src/search/q.ts"], suite_id=scene.root_id)
    assert r.matched_cases == []
    assert r.unmatched_files == ["src/search/q.ts"]


async def test_suite_scope_counts_unmapped_cases_in_scope_only(session, scene):
    r = await _impact(session, scene, ["x.ts"], suite_id=scene.root_id)
    assert r.cases_without_code_paths == 4


async def test_run_creation_never_loads_full_case_rows_for_the_scope(session, scene, monkeypatch):
    """Sizing the scope must not materialise every case's steps (hot path)."""
    async def boom(*a, **kw):
        raise AssertionError("subtree_cases loads full rows; use ids only")
    monkeypatch.setattr(test_service, "subtree_cases", boom)
    full = await _create(session, scene, browser=["chromium"])
    assert {r.selection_total for r in full} == {4}
    picked = await _create(session, scene, case_ids=[scene.child_cases[0]], browser=["chromium"])
    assert [r.test_case_id for r in picked] == [scene.child_cases[0]]


async def test_run_search_is_case_insensitive_and_literal(session, scene):
    session.add_all([
        TestRun(project_id=scene.project_id, test_suite_id=scene.root_id,
                suite_name="Checkout Flow", test_case_name="Pay_by card"),
        TestRun(project_id=scene.project_id, test_suite_id=scene.root_id,
                suite_name="Search", test_case_name="PayXby card"),
    ])
    await session.commit()

    async def names(q):
        out = await test_runs.get_runs(project_id=scene.project_id, search=q, limit=50,
                                       offset=0, status=None, browser=None, device=None,
                                       git_commit=None, git_branch=None, triggered_by=None,
                                       agent_id=None, session=session, current_user=scene.user)
        return sorted(r.test_case_name for r in out["runs"])

    assert await names("checkout") == ["Pay_by card"]
    assert await names("PAY") == ["PayXby card", "Pay_by card"]
    # `_` is a literal underscore, not LIKE's any-character wildcard.
    assert await names("pay_by") == ["Pay_by card"]
