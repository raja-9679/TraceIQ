"""Failure triage against a real Postgres: a resolved cluster reopens when it
fails again, and a cluster's ticket can only go to its own workspace's tracker.

    ./run-tests-live.sh tests/integration/test_triage_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                      # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    FailureCluster, IssueTicket, IssueTrackerConfig, Project, Tenant, TestCaseResult, TestRun,
    TestStatus, TestSuite, User, UserWorkspace, Workspace,
)
from app.services.rbac_service import rbac_service           # noqa: E402

ERROR = "Expected status 200 but got 502"


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
    user = User(email="ed@corp.example.com", full_name="E", hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws, other = Workspace(name="ws", tenant_id=tenant.id), Workspace(name="other", tenant_id=tenant.id)
    session.add_all([ws, other])
    await session.flush()
    admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    # A member of BOTH workspaces: the case the cross-workspace check is for.
    session.add_all([UserWorkspace(user_id=user.id, workspace_id=w.id, role="admin", role_id=admin.id)
                     for w in (ws, other)])
    project = Project(name="Shop", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    suite = TestSuite(name="s", project_id=project.id)
    session.add(suite)
    await session.flush()
    trackers = {k: IssueTrackerConfig(workspace_id=w.id, provider="jira", name=k,
                                      base_url="https://jira.example.com", auth_secret_encrypted="x")
                for k, w in (("own", ws), ("foreign", other))}
    session.add_all(trackers.values())
    await session.commit()
    return SimpleNamespace(user_id=user.id, project=project.id, suite=suite.id,
                           trackers={k: v.id for k, v in trackers.items()})


async def _failing_run(session, scene) -> int:
    run = TestRun(test_suite_id=scene.suite, project_id=scene.project, status=TestStatus.FAILED)
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name="checkout", status=TestStatus.FAILED,
                               duration_ms=1.0, error_message=ERROR))
    await session.commit()
    return run.id


@pytest.fixture
def cluster_task(monkeypatch):
    from app.tasks import triage_tasks
    monkeypatch.setattr(triage_tasks, "sync_engine",
                        create_engine(db_url_for(settings.DATABASE_URL, sync=True)))
    return triage_tasks.cluster_run_failures


async def _cluster(session) -> FailureCluster:
    session.expire_all()
    return (await session.exec(select(FailureCluster))).one()


@pytest.mark.asyncio(loop_scope="function")
async def test_a_resolved_cluster_reopens_when_it_fails_again(session, scene, cluster_task):
    from datetime import datetime
    cluster_task(await _failing_run(session, scene))
    c = await _cluster(session)
    assert (c.status, c.occurrence_count) == ("open", 1)

    c.status, c.resolved_at, c.first_triaged_at = "resolved", datetime.utcnow(), datetime.utcnow()
    session.add(c)
    await session.commit()
    cluster_task(await _failing_run(session, scene))
    c = await _cluster(session)
    assert (c.status, c.occurrence_count, c.resolved_at) == ("open", 2, None)
    assert c.first_triaged_at is not None          # the diagnosis still happened


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("status", ["ignored", "investigating"])
async def test_ignored_and_investigating_clusters_keep_their_status(session, scene, cluster_task, status):
    cluster_task(await _failing_run(session, scene))
    c = await _cluster(session)
    c.status = status
    session.add(c)
    await session.commit()
    cluster_task(await _failing_run(session, scene))
    c = await _cluster(session)
    assert (c.status, c.occurrence_count) == (status, 2)


@pytest.mark.asyncio(loop_scope="function")
async def test_a_ticket_goes_only_to_the_clusters_own_workspace(session, scene, cluster_task, monkeypatch):
    from app.api import triage
    from app.tasks import ticket_tasks
    queued = []
    monkeypatch.setattr(ticket_tasks.create_issue_ticket, "delay", lambda *a, **k: queued.append(a))
    cluster_task(await _failing_run(session, scene))
    c = await _cluster(session)
    principal = AuthPrincipal(user=await session.get(User, scene.user_id), api_key=None)

    with pytest.raises(HTTPException) as e:
        await triage.create_cluster_ticket(c.id, {"config_id": scene.trackers["foreign"]},
                                           session=session, principal=principal)
    assert e.value.status_code == 404
    assert not (await session.exec(select(IssueTicket))).all() and not queued

    out = await triage.create_cluster_ticket(c.id, {"config_id": scene.trackers["own"]},
                                             session=session, principal=principal)
    assert out["status"] == "pending" and len(queued) == 1
