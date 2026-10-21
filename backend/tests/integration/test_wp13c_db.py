"""WP13c against a real Postgres: demo workspace, notification guard, test-pack
import and its access control.

    TRACEIQ_SCRATCH_DB=traceiq_wp13c ./run-tests-live.sh tests/integration/test_wp13c_db.py -q
"""
from __future__ import annotations

import os
import socket
from types import SimpleNamespace
from typing import NamedTuple

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                       # noqa: E402
from app.core.config import db_url_for, settings             # noqa: E402
from app.core.secrets import decrypt_secret                   # noqa: E402
from app.models import (                                      # noqa: E402
    FailureCluster, FlakeRecord, MonitorCheck, Persona, Project, ProjectEnvironment,
    ProjectSecret, Requirement, SecurityFinding, Tenant, TestCase, TestRun, TestSchedule,
    TestSuite, User, UserProjectAccess, UserSystemRole, UserWorkspace, Workspace,
    WorkspaceWebhook,
)
from app.services import demo_workspace as dw                 # noqa: E402
from app.services.purge import PURGE_PLAN                     # noqa: E402
from app.services.rbac_service import rbac_service            # noqa: E402


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
    user: User          # workspace admin of "Corp"
    viewer: User        # viewer on `project`
    outsider: User      # member of nothing
    workspace_id: int
    project_id: int
    other_project_id: int


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session) -> Scene:
    user = User(email="dev@corp.example.com", full_name="Dev", hashed_password="x", is_verified=True)
    viewer = User(email="view@corp.example.com", full_name="View", hashed_password="x")
    outsider = User(email="out@else.example.com", full_name="Out", hashed_password="x")
    session.add_all([user, viewer, outsider])
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
    session.add(UserProjectAccess(user_id=viewer.id, project_id=project.id, access_level="viewer"))
    await session.commit()
    return Scene(user=user, viewer=viewer, outsider=outsider, workspace_id=ws.id,
                 project_id=project.id, other_project_id=other.id)


async def _table_counts(session):
    out = {}
    for step in PURGE_PLAN:
        out[step.table] = int((await session.exec(text(f"SELECT count(*) FROM {step.table}"))).one()[0])
    return out


# --- Demo workspace ------------------------------------------------------------

async def test_demo_seeds_history_the_reports_can_show(session, scene):
    out = await dw.create_demo_workspace(session, scene.user)
    ws = await session.get(Workspace, out["workspace_id"])
    assert ws.is_demo is True and ws.demo_owner_id == scene.user.id
    assert ws.tenant_id is not None                      # lands in the user's tenant
    assert len(out["projects"]) == 2
    web, api = out["projects"][0]["id"], out["projects"][1]["id"]

    from app.api import analytics, reliability_reports, triage
    trends = await analytics.project_trends(web, days=30, session=session, current_user=scene.user)
    days_with_runs = [b for b in trends["buckets"] if b["total_runs"]]
    assert len(days_with_runs) >= 29
    assert sum(b["failed"] for b in trends["buckets"]) > 0
    assert sum(b["passed"] for b in trends["buckets"]) > sum(b["failed"] for b in trends["buckets"])

    clusters = await triage.list_clusters(api, status=None, category=None, session=session,
                                          current_user=scene.user)
    timeout = [c for c in clusters if c.category == "timeout"]
    assert len(timeout) == 1 and timeout[0].occurrence_count >= 4
    assert await triage.list_clusters(web, status=None, category=None, session=session,
                                      current_user=scene.user)
    # Every failing demo result is in a cluster, and each count is its results.
    from app.models import FailureCluster, TestCaseResult, TestRun, TestStatus
    failed = (await session.exec(select(TestCaseResult).join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                                 .where(TestRun.project_id.in_([web, api]),
                                        TestCaseResult.status == TestStatus.FAILED))).all()
    assert failed and all(r.cluster_id for r in failed)
    rows = (await session.exec(select(FailureCluster).where(FailureCluster.project_id.in_([web, api])))).all()
    assert {c.id: c.occurrence_count for c in rows} == {
        c.id: sum(1 for r in failed if r.cluster_id == c.id) for c in rows}
    assert all(c.last_run_id for c in rows)
    flake = await reliability_reports.flake_trend(
        web, days=30, top=8, principal=AuthPrincipal(user=scene.user, api_key=None), session=session)
    assert flake["daily"] and flake["cases"] and flake["missing"] is None

    findings = (await session.exec(select(SecurityFinding).where(SecurityFinding.project_id == web))).all()
    assert len(findings) == 1 and findings[0].run_id is not None
    assert (await session.exec(select(FlakeRecord))).all()
    reqs = (await session.exec(select(Requirement).where(Requirement.project_id.in_([web, api])))).all()
    assert len(reqs) == 3
    assert len((await session.exec(select(Persona).where(Persona.workspace_id == ws.id))).all()) == 2
    sched = (await session.exec(select(TestSchedule).where(TestSchedule.project_id == api))).one()
    assert sched.is_monitor is True and sched.is_active is False       # never fires
    checks = (await session.exec(select(MonitorCheck).where(MonitorCheck.schedule_id == sched.id))).all()
    assert len(checks) == 120 and any(not c.is_up for c in checks)
    monitor_runs = (await session.exec(select(TestRun).where(
        TestRun.agent_id == f"schedule:{sched.id}"))).all()
    assert len(monitor_runs) == 120
    secret = (await session.exec(select(ProjectSecret).where(ProjectSecret.project_id == web,
                                                             ProjectSecret.key == "ALICE_PASSWORD"))).one()
    assert secret.value_encrypted != "wonderland" and decrypt_secret(secret.value_encrypted) == "wonderland"


async def test_one_demo_per_user(session, scene):
    outsider_id = scene.outsider.id
    first = await dw.create_demo_workspace(session, scene.user)
    with pytest.raises(dw.DemoExists) as exc:
        await dw.create_demo_workspace(session, scene.user)
    assert exc.value.workspace_id == first["workspace_id"]
    await session.rollback()
    # Someone else can still have their own.
    outsider = await session.get(User, outsider_id)
    other = await dw.create_demo_workspace(session, outsider)
    assert other["workspace_id"] != first["workspace_id"]
    assert (await dw.describe_demo(session, outsider_id))["workspace_id"] == other["workspace_id"]


async def test_delete_leaves_no_rows_and_clears_the_object_prefixes(session, scene, monkeypatch):
    from app.core import storage
    deleted = []
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: deleted.append(p) or 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: deleted.append(k))

    before = await _table_counts(session)
    out = await dw.create_demo_workspace(session, scene.user)
    ws_id = out["workspace_id"]
    run_ids = (await session.exec(text(
        "SELECT r.id FROM testrun r JOIN project p ON p.id = r.project_id WHERE p.workspace_id = :w"),
        params={"w": ws_id})).all()
    assert len(run_ids) > 200

    me = SimpleNamespace(id=scene.user.id)
    report = await dw.delete_demo_workspace(session, me)
    assert report["workspace_id"] == ws_id
    session.expire_all()
    assert await session.get(Workspace, ws_id) is None
    after = await _table_counts(session)
    leftovers = {t: after[t] - before[t] for t in after if after[t] != before[t]}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"
    assert {f"runs/{rid}/" for (rid,) in run_ids} <= set(deleted)
    # The user's real workspace is untouched.
    assert await session.get(Project, scene.project_id) is not None
    assert await dw.delete_demo_workspace(session, me) is None


async def test_demo_never_notifies(session, scene, monkeypatch):
    out = await dw.create_demo_workspace(session, scene.user)
    api = out["projects"][1]["id"]
    session.add(WorkspaceWebhook(workspace_id=out["workspace_id"], name="ci", url="https://hooks.example.com/x", created_by_id=scene.user.id,
                                 secret="s", is_active=True))
    await session.commit()
    run = (await session.exec(select(TestRun).where(TestRun.project_id == api).limit(1))).one()

    from app.services import paging
    from app.tasks import monitor_tasks, notification_tasks as nt, outbound_webhook_tasks as ow
    monkeypatch.setattr(nt.insvc, "effective", lambda key: True)

    def boom(*a, **kw):
        raise AssertionError("a demo workspace must never deliver anything")
    monkeypatch.setattr(nt, "post_webhook", boom)
    monkeypatch.setattr(nt, "send_email_notification", boom)
    monkeypatch.setattr(monitor_tasks, "post_webhook", boom)
    monkeypatch.setattr(monitor_tasks, "_send_alert_email", boom)
    import requests
    monkeypatch.setattr(requests, "post", boom)

    with Session(nt.sync_engine) as s:
        assert dw.is_demo_project_sync(s, api) is True
        assert dw.is_demo_project_sync(s, scene.project_id) is False
        project = s.get(Project, api)
        project.notification_settings = {"email_enabled": True, "slack_enabled": True}  # even if re-enabled
        cfg = nt.get_notification_settings(project, s)
        assert cfg["enabled"] is False and cfg["email_enabled"] is False and cfg["slack_enabled"] is False
        sched = s.exec(select(TestSchedule).where(TestSchedule.project_id == api)).one()
        sched.alert_emails = ["ops@corp.example.com"]
        monitor_tasks._send_monitor_alert(s, sched, s.get(TestRun, run.id), "down", 3)
        assert paging.page(s, project_id=api, source="run", action="trigger", dedup_key="k",
                           summary="x") == []

    nt.send_run_notifications.run(run.id, {})
    assert ow.dispatch_run_webhooks.run(run.id) == {"dispatched": 0, "skipped": "demo workspace"}


# --- Test packs -------------------------------------------------------------------

def _no_network(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("pack import must never contact a target")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


async def test_pack_import_creates_suite_cases_variables_and_encrypted_secrets(session, scene, monkeypatch):
    from app.api import test_packs as api
    from app.api.endpoints import test_suites
    session.add(ProjectEnvironment(project_id=scene.project_id, name="staging", is_default=True,
                                   variables={"SCIM_TEST_DOMAIN": "corp.example.com", "KEEP": "1"}))
    await session.commit()
    _no_network(monkeypatch)
    body = api.PackImportRequest(variables={"SCIM_BASE_URL": "https://scim.corp.example.com/v2",
                                            "SCIM_TOKEN": "tok-123"})
    out = await api.import_test_pack(scene.project_id, "scim-2", body, None,
                                     current_user=scene.user, session=session)
    monkeypatch.undo()

    assert out["suite_name"] == "SCIM 2.0 provisioning pack v1.0.0" and out["case_count"] == 5
    assert out["secrets"]["stored"] == ["SCIM_TOKEN"] and out["missing"] == []
    assert out["variables"]["kept_existing"] == ["SCIM_TEST_DOMAIN"]      # a default never overwrites
    suite = await session.get(TestSuite, out["suite_id"])
    assert suite.project_id == scene.project_id and suite.settings["test_pack"]["version"] == "1.0.0"
    cases = (await session.exec(select(TestCase).where(TestCase.test_suite_id == suite.id))).all()
    assert len(cases) == 5 and all(c.description and "scim" in c.tags for c in cases)
    env = (await session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == scene.project_id))).one()
    assert env.variables == {"SCIM_TEST_DOMAIN": "corp.example.com", "KEEP": "1",
                             "SCIM_BASE_URL": "https://scim.corp.example.com/v2"}
    assert "SCIM_TOKEN" not in env.variables
    secret = (await session.exec(select(ProjectSecret).where(ProjectSecret.project_id == scene.project_id))).one()
    assert secret.value_encrypted != "tok-123" and decrypt_secret(secret.value_encrypted) == "tok-123"

    # Round trip: the export carries descriptions, tags and the declarations, no secret value.
    exported = await test_suites.export_test_suite(suite.id, session=session, current_user=scene.user)
    assert exported["format_version"] == 2
    assert {c["name"] for c in exported["test_cases"]} == {c.name for c in cases}
    assert all(c["description"] and c["tags"] for c in exported["test_cases"])
    names = {v["name"]: v for v in exported["variables"]}
    assert set(names) == {"SCIM_BASE_URL", "SCIM_TOKEN", "SCIM_TEST_DOMAIN"}
    assert "tok-123" not in str(exported)

    # Same pack + version twice in one project is a clash, not a duplicate.
    with pytest.raises(HTTPException) as exc:
        await api.import_test_pack(scene.project_id, "scim-2", body, None,
                                   current_user=scene.user, session=session)
    assert exc.value.status_code == 409


async def test_pack_import_without_an_environment_creates_one_and_reports_missing(session, scene):
    from app.api import test_packs as api
    out = await api.import_test_pack(scene.other_project_id, "outbound-webhooks",
                                     api.PackImportRequest(), None,
                                     current_user=scene.user, session=session)
    assert out["environment"]["name"] == "default"
    assert out["missing"] == ["APP_API_BASE", "APP_API_TOKEN"]
    env = await session.get(ProjectEnvironment, out["environment"]["id"])
    assert env.is_default and env.variables["WEBHOOK_EVENT"] == "order.created"
    assert not (await session.exec(select(ProjectSecret).where(
        ProjectSecret.project_id == scene.other_project_id))).all()


async def test_pack_import_needs_editor_on_that_project(session, scene):
    from app.api import test_packs as api
    body = api.PackImportRequest(variables={"SCIM_TOKEN": "x"})
    for who in (scene.viewer, scene.outsider):
        with pytest.raises(HTTPException) as exc:
            await api.import_test_pack(scene.project_id, "scim-2", body, None, current_user=who, session=session)
        assert exc.value.status_code == 403
    # The viewer's project grant does not reach the sibling project either.
    with pytest.raises(HTTPException) as exc:
        await api.import_test_pack(scene.other_project_id, "scim-2", body, None,
                                   current_user=scene.viewer, session=session)
    assert exc.value.status_code == 403
    assert not (await session.exec(select(ProjectSecret))).all()


async def test_pack_import_into_a_foreign_environment_is_refused(session, scene):
    from app.api import test_packs as api
    env = ProjectEnvironment(project_id=scene.other_project_id, name="x", variables={})
    session.add(env)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await api.import_test_pack(scene.project_id, "sso-oidc",
                                   api.PackImportRequest(environment_id=env.id), None,
                                   current_user=scene.user, session=session)
    assert exc.value.status_code == 404
