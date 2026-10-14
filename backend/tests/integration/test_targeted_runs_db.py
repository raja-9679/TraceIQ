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
