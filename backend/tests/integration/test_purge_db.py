"""Cascading workspace purge against a real Postgres — workstream G1.

`tests/test_purge_plan.py` proves the plan is *complete*. This proves it
*executes*: the statements are ordered so no foreign key blows up, the audit
trail survives, another workspace is untouched, and the dry run counts without
deleting.

    ./run-tests-live.sh tests/integration/test_purge_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import NamedTuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    ApiKey, AuditLog, CaseProposal, CaseProposalAction, Persona, Project,
    ProjectSecret, Team, Tenant, TestCase, TestCaseResult, TestCaseRevision,
    TestRun, TestStatus, TestSuite, User, UserTeam, UserWorkspace,
    VisualBaseline, Workspace, WorkspaceInvitation, WorkspaceWebhook,
)
from app.services.purge import purge_workspace              # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False),
                                 future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text(_WIPE))
        # Audit rows have no FK to workspace, so the wipe above leaves them.
        # The retention flag is the only way to delete them (append-only
        # trigger) — see migration c8d9e0f1a2b3.
        await s.exec(text("SET LOCAL traceiq.audit_retention = 'on'"))
        await s.exec(text("DELETE FROM auditlog"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


class Scene(NamedTuple):
    workspace_id: int
    other_workspace_id: int
    project_id: int
    case_id: int
    run_id: int
    user_id: int


async def _populate(session) -> Scene:
    """One fully-furnished workspace plus a second one that must survive."""
    user = User(email="owner@corp.example.com", full_name="Owner",
                hashed_password="x")
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()

    ws = Workspace(name="Doomed", tenant_id=tenant.id)
    other = Workspace(name="Survivor", tenant_id=tenant.id)
    session.add_all([ws, other])
    await session.flush()

    project = Project(name="Web", workspace_id=ws.id)
    other_project = Project(name="Other Web", workspace_id=other.id)
    session.add_all([project, other_project])
    await session.flush()

    suite = TestSuite(name="Checkout", project_id=project.id)
    session.add(suite)
    await session.flush()
    child = TestSuite(name="Nested", project_id=project.id, parent_id=suite.id)
    session.add(child)
    await session.flush()

    case = TestCase(name="pay", test_suite_id=child.id, project_id=project.id,
                    steps=[{"type": "goto", "value": "/"}])
    session.add(case)
    await session.flush()

    run = TestRun(project_id=project.id, test_suite_id=suite.id,
                  status=TestStatus.PASSED)
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_case_id=case.id,
                               test_name="pay", status=TestStatus.PASSED,
                               duration_ms=12.0))
    team = Team(name="QA", workspace_id=ws.id)
    session.add(team)
    await session.flush()
    session.add_all([
        UserWorkspace(user_id=user.id, workspace_id=ws.id, role="admin"),
        UserTeam(user_id=user.id, team_id=team.id),
        ProjectSecret(project_id=project.id, key="PASSWORD", value_encrypted="x"),
        Persona(name="shopper", workspace_id=ws.id, project_id=project.id),
        ApiKey(workspace_id=ws.id, name="ci", prefix="tiq_ab", hashed_key="h",
               created_by_id=user.id),
        WorkspaceWebhook(workspace_id=ws.id, name="ci-hook",
                         url="https://example.com/hook", secret="s",
                         created_by_id=user.id),
        WorkspaceInvitation(email="new@corp.example.com", workspace_id=ws.id,
                            invited_by_id=user.id, token="tok-purge",
                            expires_at=datetime.utcnow() + timedelta(days=1)),
        VisualBaseline(test_case_id=case.id, step_id="s1",
                       image_url="baselines/1/s1/chromium-default.png"),
        TestCaseRevision(test_case_id=case.id, revision=1, snapshot={"name": "pay"},
                         changed_by_id=user.id),
        CaseProposal(project_id=project.id, action=CaseProposalAction.CREATE,
                     payload={"name": "x"}, created_by_id=user.id),
    ])
    await session.flush()
    await session.commit()
    return Scene(workspace_id=ws.id, other_workspace_id=other.id,
                 project_id=project.id, case_id=case.id, run_id=run.id,
                 user_id=user.id)


async def _resync(session):
    await session.rollback()
    session.expire_all()


async def _count(session, sql: str) -> int:
    result = await session.exec(text(sql))
    return int(result.one()[0])


# --- Dry run -----------------------------------------------------------------

async def test_dry_run_reports_without_deleting(session):
    scene = await _populate(session)
    report = await purge_workspace(session, scene.workspace_id, dry_run=True)

    assert report.dry_run is True
    assert report.total_rows > 0
    assert report.rows["project"] == 1
    assert report.rows["testcase"] == 1

    await _resync(session)
    assert await session.get(Workspace, scene.workspace_id) is not None
    assert await session.get(TestCase, scene.case_id) is not None


async def test_dry_run_counts_the_object_prefixes_it_would_remove(session):
    scene = await _populate(session)
    report = await purge_workspace(session, scene.workspace_id, dry_run=True)
    # one run prefix + one baseline key
    assert report.objects_deleted >= 2


# --- The real thing ----------------------------------------------------------

async def test_purge_removes_the_workspace_and_its_tree(session):
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    assert await session.get(Workspace, scene.workspace_id) is None
    assert await session.get(Project, scene.project_id) is None
    assert await session.get(TestCase, scene.case_id) is None
    assert await session.get(TestRun, scene.run_id) is None


async def test_purge_leaves_nothing_behind_in_any_table(session):
    # The original finding: everything except the workspace row and its teams
    # survived. This asserts the absence table by table rather than trusting the
    # report.
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    ws = scene.workspace_id
    leftovers = {}
    for table, sql in {
        "project": f"SELECT count(*) FROM project WHERE workspace_id = {ws}",
        "testsuite": "SELECT count(*) FROM testsuite",
        "testcase": "SELECT count(*) FROM testcase",
        "testrun": "SELECT count(*) FROM testrun",
        "testcaseresult": "SELECT count(*) FROM testcaseresult",
        "projectsecret": "SELECT count(*) FROM projectsecret",
        "persona": "SELECT count(*) FROM persona",
        "apikey": "SELECT count(*) FROM apikey",
        "workspacewebhook": "SELECT count(*) FROM workspacewebhook",
        "workspaceinvitation": "SELECT count(*) FROM workspaceinvitation",
        "visualbaseline": "SELECT count(*) FROM visualbaseline",
        "testcaserevision": "SELECT count(*) FROM testcaserevision",
        "caseproposal": "SELECT count(*) FROM caseproposal",
        "team": f"SELECT count(*) FROM team WHERE workspace_id = {ws}",
        "userteam": "SELECT count(*) FROM userteam",
        "userworkspace": f"SELECT count(*) FROM userworkspace WHERE workspace_id = {ws}",
    }.items():
        count = await _count(session, sql)
        if count:
            leftovers[table] = count
    assert not leftovers, f"purge left rows behind: {leftovers}"


async def test_purge_does_not_touch_another_workspace(session):
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    assert await session.get(Workspace, scene.other_workspace_id) is not None
    survivors = (await session.exec(
        select(Project).where(Project.workspace_id == scene.other_workspace_id))).all()
    assert len(survivors) == 1, "the other workspace's project must survive"


async def test_purge_does_not_delete_the_user(session):
    # Users are not workspace-scoped: the same person can be a member of several
    # workspaces, and deleting the account here would take their access to all
    # of them.
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)
    assert await session.get(User, scene.user_id) is not None


# --- Audit trail -------------------------------------------------------------

async def test_the_audit_trail_survives_the_purge(session):
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    rows = (await session.exec(
        select(AuditLog).where(AuditLog.workspace_id == scene.workspace_id))).all()
    assert rows, ("audit history must outlive the workspace — 'what happened in "
                  "the workspace that was deleted' is a question an auditor asks")


async def test_the_purge_itself_is_recorded(session):
    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    rows = (await session.exec(
        select(AuditLog).where(AuditLog.entity_type == "workspace",
                               AuditLog.action == "purge"))).all()
    assert len(rows) == 1
    assert rows[0].changes.get("name") == "Doomed"


async def test_the_audit_chain_still_verifies_after_a_purge(session):
    # The purge appends an entry and then deletes a great many rows. If the
    # ordering broke the hash chain, every later verification would report
    # tampering.
    from app.services.audit import verify_chain

    scene = await _populate(session)
    await purge_workspace(session, scene.workspace_id)
    await _resync(session)

    rows = (await session.exec(select(AuditLog).order_by(AuditLog.id))).all()
    entries = [{
        "entity_type": r.entity_type, "entity_id": r.entity_id, "action": r.action,
        "user_id": r.user_id, "workspace_id": r.workspace_id,
        "timestamp": r.timestamp, "changes": r.changes,
        "prev_hash": r.prev_hash, "row_hash": r.row_hash,
    } for r in rows]
    ok, problem = verify_chain(entries)
    assert ok, f"purge broke the audit chain: {problem}"


# --- Idempotency / edge cases -------------------------------------------------

async def test_purging_an_empty_workspace_works(session):
    user = User(email="solo@corp.example.com", full_name="Solo", hashed_password="x")
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Solo", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Empty", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    await session.commit()
    ws_id = ws.id

    report = await purge_workspace(session, ws_id)
    assert report.rows.get("workspace") == 1
    await _resync(session)
    assert await session.get(Workspace, ws_id) is None


async def test_purging_a_missing_workspace_raises(session):
    with pytest.raises(ValueError):
        await purge_workspace(session, 987654)
