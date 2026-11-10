"""The "Get started" checklist on Overview reads each step from the data that
step creates: nothing is stored, and a step cannot be done without having
happened.

    ./run-tests-live.sh tests/integration/test_getting_started_db.py -q
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import create_access_token                    # noqa: E402
from app.core.config import db_url_for, settings                 # noqa: E402
from app.models import (                                          # noqa: E402
    Project, Tenant, TestCase, TestRun, TestSchedule, TestStatus, TestSuite, User,
    UserProjectAccess, UserWorkspace, Workspace, WorkspaceInvitation,
)
from app.services.rbac_service import rbac_service                # noqa: E402

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
    yield mk
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    from httpx import ASGITransport, AsyncClient
    from app.core.database import engine
    from app.main import app
    await engine.dispose()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gs.test") as c:
        yield c
    await engine.dispose()


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


async def _scene(maker):
    async with maker() as s:
        admin = User(email="admin@gs.test", hashed_password="x", full_name="Admin")
        viewer = User(email="viewer@gs.test", hashed_password="x", full_name="Viewer")
        outsider = User(email="out@gs.test", hashed_password="x", full_name="Outsider")
        s.add(admin); s.add(viewer); s.add(outsider)
        await s.flush()
        tenant = Tenant(name="GS", owner_id=admin.id)
        s.add(tenant)
        await s.flush()
        ws = Workspace(name="WS", tenant_id=tenant.id)
        s.add(ws)
        await s.flush()
        s.add(UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin",
                            role_id=(await rbac_service.get_role_by_name(s, "Workspace Admin")).id))
        project = Project(name="Shop", workspace_id=ws.id)
        s.add(project)
        await s.flush()
        await s.commit()
        return {"ws": ws.id, "project": project.id, "admin": admin, "viewer": viewer, "outsider": outsider,
                "ADMIN": _auth(admin), "VIEWER": _auth(viewer), "OUT": _auth(outsider)}


async def _state(client, sc, who="ADMIN"):
    r = await client.get(f"/api/projects/{sc['project']}/getting-started", headers=sc[who])
    assert r.status_code == 200, r.text
    body = r.json()
    return {s["key"]: s["done"] for s in body["steps"]}, body


async def test_steps_follow_what_actually_exists(maker, client):
    sc = await _scene(maker)
    steps, body = await _state(client, sc)
    assert steps == {"test": False, "run": False, "passed": False, "schedule": False, "team": False}
    assert body["done"] == 0 and body["total"] == 5 and body["complete"] is False
    assert body["first_suite_id"] is None and body["latest_run_id"] is None

    async with maker() as s:
        suite = TestSuite(name="Smoke", project_id=sc["project"])
        s.add(suite)
        await s.flush()
        await s.commit()
        suite_id = suite.id
    steps, body = await _state(client, sc)
    assert steps["test"] is False and body["first_suite_id"] == suite_id  # an empty suite is not a test

    async with maker() as s:
        case = TestCase(name="Sign in", test_suite_id=suite_id, project_id=sc["project"], steps=[])
        s.add(case)
        await s.flush()
        # A validation run tries out a proposed repair: not "the user ran a test".
        s.add(TestRun(project_id=sc["project"], test_suite_id=suite_id, status=TestStatus.PASSED, is_validation=True))
        await s.commit()
    steps, _ = await _state(client, sc)
    assert steps == {"test": True, "run": False, "passed": False, "schedule": False, "team": False}

    async with maker() as s:
        run = TestRun(project_id=sc["project"], test_suite_id=suite_id, status=TestStatus.FAILED)
        s.add(run)
        await s.flush()
        await s.commit()
        failed_id = run.id
    steps, body = await _state(client, sc)
    assert steps["run"] is True and steps["passed"] is False and body["latest_run_id"] == failed_id

    async with maker() as s:
        s.add(TestRun(project_id=sc["project"], test_suite_id=suite_id, status=TestStatus.PASSED))
        s.add(TestSchedule(name="nightly", project_id=sc["project"], test_suite_id=suite_id,
                           cron_expression="0 2 * * *"))
        s.add(WorkspaceInvitation(email="new@gs.test", workspace_id=sc["ws"], invited_by_id=sc["admin"].id,
                                  token="tok-gs", expires_at=__import__("datetime").datetime(2030, 1, 1)))
        await s.commit()
    steps, body = await _state(client, sc)
    assert all(steps.values()) and body["complete"] is True and body["done"] == 5


async def test_inviting_is_only_offered_to_people_who_can_invite(maker, client):
    sc = await _scene(maker)
    async with maker() as s:
        s.add(UserWorkspace(user_id=sc["viewer"].id, workspace_id=sc["ws"], role="member",
                            role_id=(await rbac_service.get_role_by_name(s, "Workspace Member")).id))
        s.add(UserProjectAccess(user_id=sc["viewer"].id, project_id=sc["project"], access_level="viewer"))
        await s.commit()
    steps, body = await _state(client, sc, "VIEWER")
    assert "team" not in steps and body["total"] == 4
    # For the admin, another member in the workspace is "a teammate".
    steps, _ = await _state(client, sc)
    assert steps["team"] is True


async def test_no_access_no_answer(maker, client):
    sc = await _scene(maker)
    r = await client.get(f"/api/projects/{sc['project']}/getting-started", headers=sc["OUT"])
    assert r.status_code == 403
    r = await client.get("/api/projects/999999/getting-started", headers=sc["ADMIN"])
    assert r.status_code == 404
    r = await client.get(f"/api/projects/{sc['project']}/getting-started")
    assert r.status_code == 401
