"""Outbound webhook delivery log against a real Postgres (WP15f L3).

    ./run-tests-live.sh tests/integration/test_webhook_delivery_db.py -q
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
    Project, Tenant, TestRun, TestStatus, TestSuite, User, UserWorkspace, WebhookDelivery,
    Workspace, WorkspaceWebhook, WorkspaceWebhookCreate,
)
from app.services.rbac_service import rbac_service           # noqa: E402


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace, webhookdelivery, auditlog CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session):
    admin = User(email="admin@corp.example.com", full_name="A", hashed_password="x", is_verified=True)
    member = User(email="member@corp.example.com", full_name="M", hashed_password="x", is_verified=True)
    session.add_all([admin, member])
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=admin.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="ws", tenant_id=tenant.id)
    other_ws = Workspace(name="other", tenant_id=tenant.id)
    session.add_all([ws, other_ws])
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    ws_member = await rbac_service.get_role_by_name(session, "Workspace Member")
    session.add_all([
        UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=member.id, workspace_id=ws.id, role="member", role_id=ws_member.id),
    ])
    shop, blog = Project(name="Shop", workspace_id=ws.id), Project(name="Blog", workspace_id=ws.id)
    foreign = Project(name="Foreign", workspace_id=other_ws.id)
    session.add_all([shop, blog, foreign])
    await session.flush()
    suite = TestSuite(name="s", project_id=shop.id)
    session.add(suite)
    await session.flush()
    run = TestRun(test_suite_id=suite.id, project_id=shop.id, status=TestStatus.FAILED)
    session.add(run)
    hooks = {
        "all": WorkspaceWebhook(workspace_id=ws.id, name="all", url="https://ci.example.com/all",
                                secret="a", created_by_id=admin.id),
        "shop": WorkspaceWebhook(workspace_id=ws.id, project_id=shop.id, name="shop",
                                 url="https://ci.example.com/shop", secret="b", created_by_id=admin.id),
        "blog": WorkspaceWebhook(workspace_id=ws.id, project_id=blog.id, name="blog",
                                 url="https://ci.example.com/blog", secret="c", created_by_id=admin.id),
        "passed_only": WorkspaceWebhook(workspace_id=ws.id, name="p", url="https://ci.example.com/p",
                                        secret="d", event_filter="run.passed", created_by_id=admin.id),
    }
    session.add_all(hooks.values())
    await session.commit()
    return SimpleNamespace(admin=admin, member=member, ws_id=ws.id, other_ws_id=other_ws.id,
                           shop=shop.id, blog=blog.id, foreign=foreign.id, run_id=run.id,
                           hooks={k: v.id for k, v in hooks.items()})


class Net:
    """requests.post + the send-time guard, recorded."""

    def __init__(self, statuses=None):
        self.statuses, self.calls, self.retries = statuses or {}, [], []

    def post(self, url, **kw):
        self.calls.append((url, kw["headers"]["X-TraceIQ-Delivery"]))
        return SimpleNamespace(status_code=self.statuses.get(url, 200))


@pytest.fixture
def net(monkeypatch):
    n = Net()
    import requests
    from app.core import net_guard
    from app.tasks import outbound_webhook_tasks as owt
    monkeypatch.setattr(requests, "post", n.post)
    monkeypatch.setattr(net_guard, "validate_outbound_url_sync", lambda url, **k: url)
    monkeypatch.setattr(owt.retry_webhook_delivery, "apply_async",
                        lambda args, countdown=None: n.retries.append((args[0], countdown)))
    monkeypatch.setattr(owt.redeliver_webhook_delivery, "delay",
                        lambda row_id: owt.redeliver_webhook_delivery.run(row_id))
    return n


def _sync_rows():
    eng = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(eng) as s:
            return s.exec(select(WebhookDelivery).order_by(WebhookDelivery.id)).all()
    finally:
        eng.dispose()


async def test_only_workspace_wide_and_own_project_hooks_receive_the_run(session, scene, net):
    from app.tasks.outbound_webhook_tasks import dispatch_run_webhooks
    out = dispatch_run_webhooks.run(scene.run_id)
    urls = sorted(u for u, _ in net.calls)
    # "blog" is scoped to another project; "passed_only" filters the event out.
    assert urls == ["https://ci.example.com/all", "https://ci.example.com/shop"]
    assert out["dispatched"] == 2
    rows = _sync_rows()
    assert sorted(r.webhook_id for r in rows) == sorted([scene.hooks["all"], scene.hooks["shop"]])
    assert all(r.status == "ok" and r.attempt == 1 and r.event == "run.failed" for r in rows)
    assert len({r.delivery_id for r in rows}) == 2 and len({r.event_id for r in rows}) == 1


async def test_failure_is_logged_retried_and_redeliverable(session, scene, net):
    from app.tasks import outbound_webhook_tasks as owt
    net.statuses = {"https://ci.example.com/shop": 503}
    owt.dispatch_run_webhooks.run(scene.run_id)
    failed = [r for r in _sync_rows() if r.webhook_id == scene.hooks["shop"]][0]
    assert failed.status == "failed" and failed.status_code == 503 and failed.next_retry_at
    assert net.retries == [(failed.id, 60)]

    # The automatic retry: same delivery id, attempt 2, now succeeding.
    net.statuses = {}
    out = owt.retry_webhook_delivery.run(failed.id)
    assert out["ok"] is True
    shop_rows = [r for r in _sync_rows() if r.webhook_id == scene.hooks["shop"]]
    assert [(r.attempt, r.status) for r in shop_rows] == [(1, "failed"), (2, "ok")]
    assert shop_rows[0].delivery_id == shop_rows[1].delivery_id
    assert shop_rows[0].next_retry_at is None
    # A duplicate retry of the old row is a no-op.
    assert owt.retry_webhook_delivery.run(failed.id) == {"skipped": "superseded"}

    # Manual redelivery through the API (admin), audited, a new manual attempt.
    from app.api import workspace_webhooks as api
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("203.0.113.1", 1)})
    res = await api.redeliver(scene.ws_id, scene.hooks["shop"], failed.id, req,
                              principal=AuthPrincipal(user=scene.admin, api_key=None), session=session)
    assert res["delivery_id"] == failed.delivery_id
    shop_rows = [r for r in _sync_rows() if r.webhook_id == scene.hooks["shop"]]
    assert shop_rows[-1].manual is True and shop_rows[-1].status == "ok"
    assert [d for _, d in net.calls].count(failed.delivery_id) == 3

    listed = await api.list_deliveries(scene.ws_id, scene.hooks["shop"], limit=50, status=None,
                                       principal=AuthPrincipal(user=scene.admin, api_key=None), session=session)
    assert [d["attempt"] for d in listed["deliveries"]] == [1, 2, 1]   # newest first
    assert all("payload" not in d for d in listed["deliveries"])


async def test_member_cannot_manage_or_read_webhooks(session, scene, net):
    from app.api import workspace_webhooks as api
    member = AuthPrincipal(user=scene.member, api_key=None)
    with pytest.raises(HTTPException) as e:
        await api.list_webhooks(scene.ws_id, principal=member, session=session)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await api.create_webhook(scene.ws_id, WorkspaceWebhookCreate(
            name="x", url="https://ci.example.com/x", workspace_id=scene.ws_id),
            principal=member, session=session)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await api.list_deliveries(scene.ws_id, scene.hooks["all"], limit=10, status=None,
                                  principal=member, session=session)
    assert e.value.status_code == 403


async def test_create_refuses_another_workspaces_project(session, scene, monkeypatch):
    from app.api import workspace_webhooks as api
    from app.core import net_guard

    async def ok(url, **k):
        return url
    monkeypatch.setattr(net_guard, "validate_outbound_url", ok)
    with pytest.raises(HTTPException) as e:
        await api.create_webhook(scene.ws_id, WorkspaceWebhookCreate(
            name="x", url="https://ci.example.com/x", workspace_id=scene.ws_id, project_id=scene.foreign),
            principal=AuthPrincipal(user=scene.admin, api_key=None), session=session)
    assert e.value.status_code == 404


async def test_deleting_a_webhook_deletes_its_log_and_other_workspaces_cannot_see_it(session, scene, net):
    from app.api import workspace_webhooks as api
    from app.tasks.outbound_webhook_tasks import dispatch_run_webhooks
    dispatch_run_webhooks.run(scene.run_id)
    admin = AuthPrincipal(user=scene.admin, api_key=None)
    row = [r for r in _sync_rows() if r.webhook_id == scene.hooks["shop"]][0]
    # A delivery id from this workspace addressed through another workspace's path is a 404.
    with pytest.raises(HTTPException) as e:
        await api.redeliver(scene.ws_id, scene.hooks["all"], row.id,
                            Request({"type": "http", "headers": [], "client": ("1.1.1.1", 1)}),
                            principal=admin, session=session)
    assert e.value.status_code == 404
    await api.delete_webhook(scene.ws_id, scene.hooks["shop"], principal=admin, session=session)
    assert all(r.webhook_id != scene.hooks["shop"] for r in _sync_rows())


async def test_project_delete_cascades_through_scoped_webhook_log(session, scene, net):
    from app.services.entity_delete import cascade_delete
    from app.tasks.outbound_webhook_tasks import dispatch_run_webhooks
    dispatch_run_webhooks.run(scene.run_id)
    await cascade_delete(session, "project", [scene.shop])
    await session.commit()
    remaining = _sync_rows()
    assert all(r.webhook_id != scene.hooks["shop"] for r in remaining)
    assert any(r.webhook_id == scene.hooks["all"] for r in remaining)   # workspace-wide hook kept


async def test_retention_sweep(session, scene, net):
    from app.tasks.cleanup_tasks import purge_webhook_deliveries
    from app.tasks.outbound_webhook_tasks import dispatch_run_webhooks
    dispatch_run_webhooks.run(scene.run_id)
    await session.exec(text("UPDATE webhookdelivery SET created_at = now() - interval '31 days' "
                            f"WHERE webhook_id = {scene.hooks['all']}"))
    await session.commit()
    assert purge_webhook_deliveries.run() == 1
    assert [r.webhook_id for r in _sync_rows()] == [scene.hooks["shop"]]
