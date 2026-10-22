"""Issue-tracker configs, run tickets and plan assignment against a real
Postgres: who may write a tracker config (and that it is audited without the
secret), that its URL goes through net_guard, that a run's ticket only goes to
the run's own workspace's tracker, and who may assign a plan.

    ./run-tests-live.sh tests/integration/test_issue_trackers_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

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

from app.api import billing as billing_api                    # noqa: E402
from app.api import issue_trackers as api                     # noqa: E402
from app.core.auth import AuthPrincipal                       # noqa: E402
from app.core.config import db_url_for, settings             # noqa: E402
from app.models import (                                      # noqa: E402
    AuditLog, IssueTicket, IssueTicketCreate, IssueTrackerConfig, IssueTrackerConfigCreate,
    IssueTrackerConfigUpdate, Plan, Project, Tenant, TestRun, TestStatus, TestSuite, User,
    UserWorkspace, Workspace, WorkspaceSubscription,
)
from app.services.rbac_service import rbac_service            # noqa: E402

SECRET = "jira-token-do-not-log"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace, auditlog, plan CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session):
    users = {k: User(email=f"{k}@corp.example.com", full_name=k, hashed_password="x", is_verified=True)
             for k in ("owner", "admin", "member", "legacy")}
    session.add_all(users.values())
    await session.flush()
    # A first tenant owned by someone else, so none of the actors below is the
    # instance admin by the first-tenant fallback.
    first = Tenant(name="Operator", owner_id=users["owner"].id)
    session.add(first)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=users["admin"].id)
    session.add(tenant)
    await session.flush()
    ws, other = Workspace(name="ws", tenant_id=tenant.id), Workspace(name="other", tenant_id=tenant.id)
    session.add_all([ws, other])
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    ws_member = await rbac_service.get_role_by_name(session, "Workspace Member")
    session.add_all([
        # The admin is in BOTH workspaces: the cross-workspace ticket case.
        UserWorkspace(user_id=users["admin"].id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=users["admin"].id, workspace_id=other.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=users["member"].id, workspace_id=ws.id, role="member", role_id=ws_member.id),
        # Deprecated string says admin; the RBAC role says member.
        UserWorkspace(user_id=users["legacy"].id, workspace_id=ws.id, role="admin", role_id=ws_member.id),
    ])
    project = Project(name="Shop", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    suite = TestSuite(name="s", project_id=project.id)
    session.add(suite)
    await session.flush()
    run = TestRun(test_suite_id=suite.id, project_id=project.id, status=TestStatus.FAILED)
    session.add(run)
    trackers = {k: IssueTrackerConfig(workspace_id=w.id, provider="jira", name=k,
                                      base_url="https://jira.example.com", auth_secret_encrypted="x")
                for k, w in (("own", ws), ("foreign", other))}
    session.add_all(trackers.values())
    await session.commit()
    return SimpleNamespace(users={k: v.id for k, v in users.items()}, ws=ws.id, other=other.id,
                           project=project.id, run=run.id,
                           trackers={k: v.id for k, v in trackers.items()})


async def _p(session, scene, who) -> AuthPrincipal:
    return AuthPrincipal(user=await session.get(User, scene.users[who]), api_key=None)


@pytest.fixture
def public_dns(monkeypatch):
    """Save-time net_guard without real DNS: example.com hosts count as public."""
    from app.core import net_guard

    async def validate(url, **kw):
        if "example.com" in url:
            return url
        return await net_guard.validate_outbound_url(url, **kw)
    monkeypatch.setattr(api, "validate_outbound_url", validate)


def _body(**kw) -> IssueTrackerConfigCreate:
    return IssueTrackerConfigCreate(**{"provider": "jira", "name": "Jira", "base_url": "https://jira.example.com",
                                       "auth_user": "bot@corp.example.com", "auth_secret": SECRET,
                                       "settings": {"project_key": "QA"}, **kw})


async def _audit(session, entity_type):
    session.expire_all()
    return (await session.exec(select(AuditLog).where(AuditLog.entity_type == entity_type)
                               .order_by(AuditLog.id))).all()


@pytest.mark.asyncio(loop_scope="function")
async def test_tracker_writes_need_a_workspace_admin_and_are_audited(session, scene, public_dns):
    for who in ("member", "legacy"):
        with pytest.raises(HTTPException) as e:
            await api.create_tracker(scene.ws, _body(), session=session, principal=await _p(session, scene, who))
        assert e.value.status_code == 403
        with pytest.raises(HTTPException) as e:
            await api.update_tracker(scene.ws, scene.trackers["own"], IssueTrackerConfigUpdate(enabled=False),
                                     session=session, principal=await _p(session, scene, who))
        assert e.value.status_code == 403
        with pytest.raises(HTTPException) as e:
            await api.delete_tracker(scene.ws, scene.trackers["own"], session=session,
                                     principal=await _p(session, scene, who))
        assert e.value.status_code == 403
    # Members can still list (the ticket dialog picks from this list).
    assert len(await api.list_trackers(scene.ws, session=session,
                                       principal=await _p(session, scene, "member"))) == 1

    admin = await _p(session, scene, "admin")
    made = await api.create_tracker(scene.ws, _body(), session=session, principal=admin)
    await api.update_tracker(scene.ws, made.id, IssueTrackerConfigUpdate(auth_secret="rotated-" + SECRET),
                             session=session, principal=admin)
    await api.delete_tracker(scene.ws, made.id, session=session, principal=admin)
    rows = await _audit(session, "issue_tracker")
    assert [r.action for r in rows] == ["create", "update", "delete"]
    assert all(r.workspace_id == scene.ws and r.user_id == scene.users["admin"] for r in rows)
    assert rows[1].changes.get("credential") == "changed"
    assert SECRET not in str([r.changes for r in rows])


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("url", ["http://169.254.169.254/", "http://127.0.0.1:8000", "file:///etc/passwd"])
async def test_tracker_url_goes_through_net_guard(session, scene, public_dns, url):
    admin = await _p(session, scene, "admin")
    with pytest.raises(HTTPException) as e:
        await api.create_tracker(scene.ws, _body(base_url=url), session=session, principal=admin)
    assert e.value.status_code == 400 and "rejected" in e.value.detail
    with pytest.raises(HTTPException) as e:
        await api.update_tracker(scene.ws, scene.trackers["own"], IssueTrackerConfigUpdate(base_url=url),
                                 session=session, principal=admin)
    assert e.value.status_code == 400
    session.expire_all()
    assert (await session.get(IssueTrackerConfig, scene.trackers["own"])).base_url == "https://jira.example.com"
    assert len((await session.exec(select(IssueTrackerConfig))).all()) == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_a_run_ticket_goes_only_to_the_runs_own_workspace(session, scene, monkeypatch):
    from app.tasks import ticket_tasks
    queued = []
    monkeypatch.setattr(ticket_tasks.create_issue_ticket, "delay", lambda *a, **k: queued.append(a))
    admin = await _p(session, scene, "admin")
    with pytest.raises(HTTPException) as e:
        await api.create_ticket(scene.run, IssueTicketCreate(config_id=scene.trackers["foreign"]),
                                session=session, principal=admin)
    assert e.value.status_code == 404
    assert not (await session.exec(select(IssueTicket))).all() and not queued

    out = await api.create_ticket(scene.run, IssueTicketCreate(config_id=scene.trackers["own"]),
                                  session=session, principal=admin)
    assert out.status == "pending" and len(queued) == 1


async def _plans(session):
    free = Plan(name="free", display_name="Free", price_cents=0, limits={})
    pro = Plan(name="pro", display_name="Pro", price_cents=4900, limits={}, stripe_price_id="price_x")
    session.add_all([free, pro])
    await session.commit()


@pytest.fixture
def no_redis(monkeypatch):
    async def zero(workspace_id):
        return 0
    monkeypatch.setattr(billing_api.billing_svc, "ai_generations_today", zero)


async def _plan_of(session, ws_id):
    session.expire_all()
    sub = (await session.exec(select(WorkspaceSubscription).where(
        WorkspaceSubscription.workspace_id == ws_id))).first()
    return (await session.get(Plan, sub.plan_id)).name if sub else None


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_assignment_with_stripe_configured(session, scene, monkeypatch, no_redis):
    await _plans(session)
    monkeypatch.setattr(billing_api.settings, "STRIPE_SECRET_KEY", "sk_test_x")

    async def user(who):
        return await session.get(User, scene.users[who])

    # The deprecated role string no longer grants anything.
    for who in ("member", "legacy"):
        with pytest.raises(HTTPException) as e:
            await billing_api.assign_plan(scene.ws, {"plan_name": "free"}, session=session,
                                          current_user=await user(who))
        assert e.value.status_code == 403
    # A workspace admin can't skip checkout for a paid plan...
    with pytest.raises(HTTPException) as e:
        await billing_api.assign_plan(scene.ws, {"plan_name": "pro"}, session=session,
                                      current_user=await user("admin"))
    assert e.value.status_code == 403
    assert await _plan_of(session, scene.ws) is None
    # ...but can pick a free one.
    await billing_api.assign_plan(scene.ws, {"plan_name": "free"}, session=session,
                                  current_user=await user("admin"))
    assert await _plan_of(session, scene.ws) == "free"
    # The instance admin (operator) can comp a paid plan.
    op = await user("owner")
    op.is_instance_admin = True
    session.add(op)
    session.add(UserWorkspace(user_id=op.id, workspace_id=scene.ws, role="member",
                              role_id=(await rbac_service.get_role_by_name(session, "Workspace Member")).id))
    await session.commit()
    await billing_api.assign_plan(scene.ws, {"plan_name": "pro"}, session=session, current_user=op)
    assert await _plan_of(session, scene.ws) == "pro"

    rows = await _audit(session, "workspace_subscription")
    assert [(r.action, r.changes["plan"], r.user_id) for r in rows] == [
        ("assign_plan", "free", scene.users["admin"]), ("assign_plan", "pro", op.id)]
    assert all(r.workspace_id == scene.ws for r in rows)


@pytest.mark.asyncio(loop_scope="function")
async def test_without_a_payment_provider_paid_plans_still_need_the_instance_admin(session, scene, monkeypatch, no_redis):
    await _plans(session)
    monkeypatch.setattr(billing_api.settings, "STRIPE_SECRET_KEY", "")
    admin = await session.get(User, scene.users["admin"])
    # A workspace admin raising their own quotas is the hole: refused.
    with pytest.raises(HTTPException) as e:
        await billing_api.assign_plan(scene.ws, {"plan_name": "pro"}, session=session, current_user=admin)
    assert e.value.status_code == 403
    await billing_api.assign_plan(scene.ws, {"plan_name": "free"}, session=session, current_user=admin)
    assert await _plan_of(session, scene.ws) == "free"
    # The operator needs no membership to comp a plan.
    op = await session.get(User, scene.users["owner"])
    op.is_instance_admin = True
    session.add(op)
    await session.commit()
    await billing_api.assign_plan(scene.ws, {"plan_name": "pro"}, session=session, current_user=op)
    assert await _plan_of(session, scene.ws) == "pro"
