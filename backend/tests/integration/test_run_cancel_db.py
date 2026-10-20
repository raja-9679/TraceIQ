"""Run cancel against a real Postgres (WP15f L1).

    ./run-tests-live.sh tests/integration/test_run_cancel_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                      # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    AuditLog, Project, Tenant, TestRun, TestStatus, TestSuite, User, UserProjectAccess,
    UserSystemRole, UserWorkspace, Workspace,
)
from app.services import run_completion                      # noqa: E402
from app.services.rbac_service import rbac_service           # noqa: E402


class FakeRedis:
    def __init__(self, fail=False):
        self.keys, self.fail = {}, fail

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.keys[key] = (value, ex)


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace, auditlog CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session):
    admin = User(email="owner@corp.example.com", full_name="Owner", hashed_password="x", is_verified=True)
    viewer = User(email="viewer@corp.example.com", full_name="Viewer", hashed_password="x", is_verified=True)
    outsider = User(email="out@else.example.com", full_name="Out", hashed_password="x", is_verified=True)
    session.add_all([admin, viewer, outsider])
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=admin.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    member = await rbac_service.get_role_by_name(session, "Workspace Member")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=admin.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=viewer.id, workspace_id=ws.id, role="member", role_id=member.id),
    ])
    project = Project(name="Shop", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    session.add(UserProjectAccess(user_id=viewer.id, project_id=project.id, access_level="viewer"))
    suite = TestSuite(name="Checkout", project_id=project.id)
    session.add(suite)
    await session.flush()
    runs = {}
    for name, st in (("pending", TestStatus.PENDING), ("running", TestStatus.RUNNING),
                     ("passed", TestStatus.PASSED)):
        r = TestRun(test_suite_id=suite.id, project_id=project.id, status=st)
        session.add(r)
        await session.flush()
        runs[name] = r.id
    await session.commit()
    return SimpleNamespace(admin=admin, viewer=viewer, outsider=outsider, ws_id=ws.id,
                           project_id=project.id, runs=runs)


def _request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "client": ("203.0.113.9", 4000)})


@pytest.fixture
def plumbing(monkeypatch):
    redis = FakeRedis()
    from app.core import redis as core_redis
    monkeypatch.setattr(core_redis.RedisClient, "get_instance", classmethod(lambda cls: redis))
    queued = []
    monkeypatch.setattr(run_completion, "queue_side_effects",
                        lambda run_id, payload=None, **k: queued.append(run_id) or [])
    return SimpleNamespace(redis=redis, queued=queued)


async def _cancel(session, run_id, user, api_key=None):
    from app.api.endpoints import test_runs
    return await test_runs.cancel_test_run(
        run_id=run_id, request=_request(), session=session,
        principal=AuthPrincipal(user=user, api_key=api_key))


async def test_editor_cancels_a_running_run(session, scene, plumbing):
    rid = scene.runs["running"]
    out = await _cancel(session, rid, scene.admin)
    assert out["status"] == "cancelled" and out["workers_notified"] is True
    run = await session.get(TestRun, rid)
    await session.refresh(run)
    assert run.status == TestStatus.CANCELLED
    assert run.cancelled_at is not None and run.finalized_at is not None
    # Workers see the key; it outlives any queue wait.
    value, ttl = plumbing.redis.keys[f"run:cancelled:{rid}"]
    assert value == "1" and ttl >= 24 * 3600
    # Side effects once (outbound run.cancelled webhook; notifications skip it).
    assert plumbing.queued == [rid]
    audit = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "test_run", AuditLog.action == "cancel"))).all()
    assert len(audit) == 1 and audit[0].entity_id == rid
    assert audit[0].changes["previous_status"] == "running"


async def test_pending_run_cancels_too(session, scene, plumbing):
    out = await _cancel(session, scene.runs["pending"], scene.admin)
    assert out["status"] == "cancelled"


async def test_finished_run_is_409_and_unchanged_and_not_audited(session, scene, plumbing):
    rid = scene.runs["passed"]
    with pytest.raises(HTTPException) as exc:
        await _cancel(session, rid, scene.admin)
    assert exc.value.status_code == 409
    assert exc.value.detail["status"] == "passed"
    run = await session.get(TestRun, rid)
    await session.refresh(run)
    assert run.status == TestStatus.PASSED
    assert plumbing.redis.keys == {} and plumbing.queued == []
    audit = (await session.exec(select(AuditLog).where(AuditLog.action == "cancel"))).all()
    assert audit == []


async def test_second_cancel_is_409(session, scene, plumbing):
    rid = scene.runs["running"]
    await _cancel(session, rid, scene.admin)
    with pytest.raises(HTTPException) as exc:
        await _cancel(session, rid, scene.admin)
    assert exc.value.status_code == 409 and exc.value.detail["status"] == "cancelled"
    assert plumbing.queued == [rid]


@pytest.mark.parametrize("who", ["viewer", "outsider"])
async def test_viewer_and_outsider_cannot_cancel(session, scene, plumbing, who):
    rid = scene.runs["running"]
    with pytest.raises(HTTPException) as exc:
        await _cancel(session, rid, getattr(scene, who))
    assert exc.value.status_code == 403
    run = await session.get(TestRun, rid)
    await session.refresh(run)
    assert run.status == TestStatus.RUNNING


async def test_project_scoped_key_for_another_project_is_refused(session, scene, plumbing):
    key = SimpleNamespace(id=1, project_id=scene.project_id + 999, workspace_id=scene.ws_id)
    with pytest.raises(HTTPException) as exc:
        await _cancel(session, scene.runs["running"], scene.admin, api_key=key)
    assert exc.value.status_code == 403


async def test_redis_down_still_cancels(session, scene, monkeypatch, plumbing):
    from app.core import redis as core_redis
    monkeypatch.setattr(core_redis.RedisClient, "get_instance", classmethod(lambda cls: FakeRedis(fail=True)))
    out = await _cancel(session, scene.runs["running"], scene.admin)
    assert out["status"] == "cancelled" and out["workers_notified"] is False


async def test_late_results_never_uncancel(session, scene, plumbing, monkeypatch):
    rid = scene.runs["running"]
    await _cancel(session, rid, scene.admin)
    from app.tasks import result_aggregator
    sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(sync_engine) as s:
            run = s.get(TestRun, rid)
            result_aggregator.update_run_from_progress(
                run, rid, {"total": "1", "completed": "1", "passed": "1", "failed": "0"}, s)
            s.add(run)
            s.commit()
            assert s.get(TestRun, rid).status == TestStatus.CANCELLED
    finally:
        sync_engine.dispose()
    assert plumbing.queued == [rid]


async def test_dispatch_skips_a_cancelled_run(session, scene, plumbing, monkeypatch):
    rid = scene.runs["pending"]
    await _cancel(session, rid, scene.admin)
    from app import worker
    called = []
    monkeypatch.setattr(worker, "dispatch_cases_to_queue", lambda *a, **k: called.append(a))
    worker.run_test_suite(rid)
    run = await session.get(TestRun, rid)
    await session.refresh(run)
    assert run.status == TestStatus.CANCELLED and called == []
