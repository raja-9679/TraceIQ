"""Per-entity deletes against a real Postgres.

Project, suite, case and run deletes were each written before tables that
reference them existed, and nobody went back. They 500'd on foreign keys:

* project delete, for any project with test cases (`testcase_project_id_fkey`);
* suite delete, once any of its cases had a revision
  (`testcaserevision_test_case_id_fkey`) — i.e. almost always;
* run delete, for any run that produced a heal proposal
  (`selectorhealproposal_source_run_id_fkey`) — and the retention sweep, which
  deletes runs the same way, stalled on such a run batch after batch.

`tests/test_entity_delete_plan.py` proves every reference has a declared
policy. This proves the policies *execute*: each endpoint is driven through its
real handler against a scene that has a row in every referencing table, and the
surviving data (sibling project/suite, audit trail, nullified references) is
checked as carefully as the deleted data.

    ./run-tests-live.sh tests/integration/test_entity_delete_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import NamedTuple

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    AuthSession, CaseProposal, CaseProposalAction, FailureCluster, FlakeRecord,
    MonitorCheck, Project, ProjectEnvironment, ProjectSecret, RequirementLink,
    SecurityFinding, SelectorHealProposal, Tenant, TestCase, TestCaseResult,
    TestCaseRevision, TestRun, TestSchedule, TestStatus, TestSuite, User,
    UserSystemRole, UserWorkspace, VisualBaseline, Workspace,
)
from app.services.rbac_service import rbac_service           # noqa: E402
from tests.integration.denied import DENIED  # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False),
                                 future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text(_WIPE))
        await s.exec(text("SET LOCAL traceiq.audit_retention = 'on'"))
        await s.exec(text("DELETE FROM auditlog"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


class Tree(NamedTuple):
    project_id: int
    suite_id: int
    child_suite_id: int
    case_id: int
    run_id: int          # suite run, has a result, heal proposal, finding...
    case_run_id: int     # single-case run of case_id
    heal_id: int
    proposal_id: int     # CaseProposal sourced from run_id, targeting suite_id
    monitor_check_id: int
    cluster_id: int


class Scene(NamedTuple):
    admin: User
    workspace_id: int
    doomed: Tree
    sibling: Tree        # a second project in the SAME workspace


async def _tree(session, ws_id: int, user_id: int, name: str,
                *, schedule: bool = True) -> Tree:
    """A project with a row in every table that references its entities."""
    project = Project(name=name, workspace_id=ws_id)
    session.add(project)
    await session.flush()
    env = ProjectEnvironment(project_id=project.id, name="staging")
    suite = TestSuite(name=f"{name} checkout", project_id=project.id)
    session.add_all([env, suite])
    await session.flush()
    child = TestSuite(name=f"{name} nested", project_id=project.id,
                      parent_id=suite.id)
    session.add(child)
    await session.flush()
    case = TestCase(name=f"{name} pay", test_suite_id=child.id,
                    project_id=project.id, steps=[{"type": "goto", "value": "/"}])
    session.add(case)
    await session.flush()

    run = TestRun(project_id=project.id, test_suite_id=suite.id,
                  environment_id=env.id, status=TestStatus.PASSED)
    case_run = TestRun(project_id=project.id, test_suite_id=child.id,
                       test_case_id=case.id, status=TestStatus.FAILED)
    cluster = FailureCluster(project_id=project.id, signature=f"sig-{name}",
                             title="boom", category="assertion")
    session.add_all([run, case_run, cluster])
    await session.flush()

    heal = SelectorHealProposal(test_case_id=case.id, step_id="s1",
                                new_selector="#pay", source_run_id=run.id)
    proposal = CaseProposal(project_id=project.id,
                            action=CaseProposalAction.CREATE,
                            payload={"name": "x"}, created_by_id=user_id,
                            test_suite_id=suite.id, source_run_id=run.id)
    session.add_all([
        heal, proposal,
        TestCaseResult(test_run_id=run.id, test_case_id=case.id,
                       test_name="pay", status=TestStatus.PASSED,
                       duration_ms=12.0, cluster_id=cluster.id),
        TestCaseResult(test_run_id=case_run.id, test_case_id=case.id,
                       test_name="pay", status=TestStatus.FAILED,
                       duration_ms=10.0),
        TestCaseRevision(test_case_id=case.id, revision=1,
                         snapshot={"name": "pay"}, changed_by_id=user_id),
        VisualBaseline(test_case_id=case.id, step_id="s1",
                       image_url=f"baselines/{case.id}/s1/chromium-default.png"),
        FlakeRecord(test_case_id=case.id),
        RequirementLink(test_case_id=case.id, project_id=project.id, ref="REQ-1"),
        AuthSession(project_id=project.id, captured_by_case_id=case.id),
        SecurityFinding(run_id=run.id, project_id=project.id,
                        category="missing-header", severity="low", title="csp"),
        ProjectSecret(project_id=project.id, key="PASSWORD", value_encrypted="x"),
    ])
    await session.flush()

    monitor_check_id = 0
    if schedule:
        # Schedules sit on a separate suite so that suite/case deletes, which
        # 409 on a referencing schedule by design, are not blocked here.
        sched_suite = TestSuite(name=f"{name} monitored", project_id=project.id)
        session.add(sched_suite)
        await session.flush()
        sched = TestSchedule(name="hourly", project_id=project.id,
                             test_suite_id=sched_suite.id, cron_expression="0 * * * *",
                             is_monitor=True)
        session.add(sched)
        await session.flush()
        check = MonitorCheck(schedule_id=sched.id, run_id=run.id,
                             status=TestStatus.PASSED, is_up=True)
        session.add(check)
        await session.flush()
        monitor_check_id = check.id

    return Tree(project_id=project.id, suite_id=suite.id, child_suite_id=child.id,
                case_id=case.id, run_id=run.id, case_run_id=case_run.id,
                heal_id=heal.id, proposal_id=proposal.id,
                monitor_check_id=monitor_check_id, cluster_id=cluster.id)


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session) -> Scene:
    admin = User(email="admin@corp.example.com", full_name="Admin",
                 hashed_password="x", is_verified=True)
    session.add(admin)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=admin.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp workspace", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=admin.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin",
                      role_id=ws_admin.id),
    ])
    await session.flush()
    doomed = await _tree(session, ws.id, admin.id, "doomed")
    sibling = await _tree(session, ws.id, admin.id, "sibling")
    await session.commit()
    return Scene(admin=admin, workspace_id=ws.id, doomed=doomed, sibling=sibling)


async def _resync(session):
    await session.rollback()
    session.expire_all()


async def _scalar(session, sql: str, **params):
    return (await session.exec(text(sql), params=params)).one()[0]


async def _assert_tree_intact(session, tree: Tree):
    """The sibling must come through every delete untouched."""
    for table, ident in [("project", tree.project_id), ("testsuite", tree.suite_id),
                         ("testsuite", tree.child_suite_id), ("testcase", tree.case_id),
                         ("testrun", tree.run_id), ("testrun", tree.case_run_id),
                         ("selectorhealproposal", tree.heal_id),
                         ("caseproposal", tree.proposal_id),
                         ("failurecluster", tree.cluster_id)]:
        assert await _scalar(session, f"SELECT count(*) FROM {table} WHERE id = :i",
                             i=ident) == 1, f"sibling {table} {ident} was deleted"
    assert await _scalar(
        session, "SELECT count(*) FROM testcaserevision WHERE test_case_id = :c",
        c=tree.case_id) == 1
    assert await _scalar(
        session, "SELECT count(*) FROM testcaseresult r JOIN testrun t "
                 "ON t.id = r.test_run_id WHERE t.project_id = :p",
        p=tree.project_id) == 2
    assert await _scalar(
        session, "SELECT source_run_id FROM selectorhealproposal WHERE id = :i",
        i=tree.heal_id) == tree.run_id


# --- 1. Project delete --------------------------------------------------------

async def test_project_delete_with_cases_succeeds_and_cascades(session, scene):
    from app.api.projects import delete_project

    d = scene.doomed
    result = await delete_project(project_id=d.project_id, session=session,
                                  current_user=scene.admin)
    assert result["status"] == "success"
    await _resync(session)

    leftovers = {}
    for table, where in {
        "project": f"id = {d.project_id}",
        "testsuite": f"project_id = {d.project_id}",
        "testcase": f"project_id = {d.project_id}",
        "testrun": f"project_id = {d.project_id}",
        "testcaserevision": f"test_case_id = {d.case_id}",
        "testcaseresult": f"test_run_id IN ({d.run_id}, {d.case_run_id})",
        "selectorhealproposal": f"id = {d.heal_id}",
        "caseproposal": f"project_id = {d.project_id}",
        "visualbaseline": f"test_case_id = {d.case_id}",
        "flakerecord": f"test_case_id = {d.case_id}",
        "requirementlink": f"test_case_id = {d.case_id}",
        "authsession": f"project_id = {d.project_id}",
        "securityfinding": f"project_id = {d.project_id}",
        "projectsecret": f"project_id = {d.project_id}",
        "projectenvironment": f"project_id = {d.project_id}",
        "failurecluster": f"project_id = {d.project_id}",
        "testschedule": f"project_id = {d.project_id}",
        "monitorcheck": f"id = {d.monitor_check_id}",
    }.items():
        count = await _scalar(session, f"SELECT count(*) FROM {table} WHERE {where}")
        if count:
            leftovers[table] = count
    assert not leftovers, f"project delete left rows behind: {leftovers}"


async def test_project_delete_leaves_the_sibling_project_alone(session, scene):
    from app.api.projects import delete_project

    await delete_project(project_id=scene.doomed.project_id, session=session,
                         current_user=scene.admin)
    await _resync(session)
    await _assert_tree_intact(session, scene.sibling)
    assert await session.get(Workspace, scene.workspace_id) is not None


async def test_project_delete_is_audited_and_keeps_audit_history(session, scene):
    from app.api.projects import delete_project

    pid = scene.doomed.project_id
    await delete_project(project_id=pid, session=session, current_user=scene.admin)
    await _resync(session)
    assert await _scalar(
        session, "SELECT count(*) FROM auditlog WHERE entity_type = 'project' "
                 "AND entity_id = :p AND action = 'delete'", p=pid) == 1


async def test_project_delete_still_requires_permission(session, scene):
    from app.api.projects import delete_project

    outsider = User(email="nobody@corp.example.com", full_name="Nobody",
                    hashed_password="x", is_verified=True)
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_project(project_id=scene.doomed.project_id, session=session,
                             current_user=outsider)
    assert exc.value.status_code == 403
    await _resync(session)
    assert await session.get(Project, scene.doomed.project_id) is not None


# --- 2. Suite delete ----------------------------------------------------------

async def test_suite_delete_with_revisioned_case_succeeds(session, scene):
    from app.api.endpoints.test_suites import delete_test_suite

    d = scene.doomed
    result = await delete_test_suite(suite_id=d.suite_id, session=session,
                                     current_user=scene.admin)
    assert result["status"] == "success"
    assert result["suites_deleted"] == 2
    await _resync(session)

    assert await session.get(TestSuite, d.suite_id) is None
    assert await session.get(TestSuite, d.child_suite_id) is None
    assert await session.get(TestCase, d.case_id) is None
    assert await session.get(TestRun, d.run_id) is None
    assert await session.get(TestRun, d.case_run_id) is None
    for table in ("testcaserevision", "requirementlink", "visualbaseline",
                  "flakerecord", "selectorhealproposal"):
        assert await _scalar(
            session, f"SELECT count(*) FROM {table} WHERE test_case_id = :c",
            c=d.case_id) == 0, table
    # A CREATE proposal aimed at the deleted suite can never be applied.
    assert await session.get(CaseProposal, d.proposal_id) is None
    # The project's auth session survives the case that captured it.
    auth = (await session.exec(text(
        "SELECT captured_by_case_id FROM authsession WHERE project_id = :p"),
        params={"p": d.project_id})).all()
    assert auth == [(None,)]
    # The project and its other suite survive.
    assert await session.get(Project, d.project_id) is not None
    await _assert_tree_intact(session, scene.sibling)


async def test_suite_delete_still_409s_on_a_schedule(session, scene):
    from app.api.endpoints.test_suites import delete_test_suite

    d = scene.doomed
    session.add(TestSchedule(name="nightly", project_id=d.project_id,
                             test_suite_id=d.child_suite_id,
                             cron_expression="0 0 * * *"))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_test_suite(suite_id=d.suite_id, session=session,
                                current_user=scene.admin)
    assert exc.value.status_code == 409
    await _resync(session)
    assert await session.get(TestCase, d.case_id) is not None


# --- 3. Run delete ------------------------------------------------------------

async def test_run_delete_with_heal_proposal_succeeds(session, scene):
    from app.api.endpoints.test_runs import delete_run

    d = scene.doomed
    result = await delete_run(run_id=d.run_id, session=session,
                              current_user=scene.admin)
    assert result["status"] == "success"
    await _resync(session)

    assert await session.get(TestRun, d.run_id) is None
    assert await _scalar(session, "SELECT count(*) FROM testcaseresult "
                                  "WHERE test_run_id = :r", r=d.run_id) == 0
    assert await _scalar(session, "SELECT count(*) FROM securityfinding "
                                  "WHERE run_id = :r", r=d.run_id) == 0
    # Proposals and monitor history outlive the run; only the link goes.
    heal = await session.get(SelectorHealProposal, d.heal_id)
    assert heal is not None and heal.source_run_id is None
    proposal = await session.get(CaseProposal, d.proposal_id)
    assert proposal is not None and proposal.source_run_id is None
    check = await session.get(MonitorCheck, d.monitor_check_id)
    assert check is not None and check.run_id is None
    # Nothing else in the project moved.
    assert await session.get(TestRun, d.case_run_id) is not None
    assert await session.get(TestCase, d.case_id) is not None
    await _assert_tree_intact(session, scene.sibling)


async def test_bulk_run_delete_with_heal_proposal_succeeds(session, scene):
    from app.api.endpoints.test_runs import delete_runs

    d = scene.doomed
    result = await delete_runs(run_ids=[d.run_id, d.case_run_id], all=False,
                               session=session, current_user=scene.admin)
    assert result["status"] == "success"
    await _resync(session)
    assert await session.get(TestRun, d.run_id) is None
    assert await session.get(TestRun, d.case_run_id) is None
    assert (await session.get(SelectorHealProposal, d.heal_id)).source_run_id is None
    await _assert_tree_intact(session, scene.sibling)


async def test_delete_all_runs_with_heal_proposal_succeeds(session, scene):
    from app.api.endpoints.test_runs import delete_runs

    result = await delete_runs(run_ids=None, all=True, session=session,
                               current_user=scene.admin)
    assert result["status"] == "success"
    await _resync(session)
    assert await _scalar(session, "SELECT count(*) FROM testrun") == 0
    assert await _scalar(session, "SELECT count(*) FROM selectorhealproposal") == 2


async def test_run_delete_still_requires_editor(session, scene):
    from app.api.endpoints.test_runs import delete_run

    outsider = User(email="nobody@corp.example.com", full_name="Nobody",
                    hashed_password="x", is_verified=True)
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_run(run_id=scene.doomed.run_id, session=session,
                         current_user=outsider)
    assert exc.value.status_code in DENIED


async def test_retention_purges_a_run_with_a_heal_proposal(session, scene):
    # purge_old_runs deleted runs the same way and swallowed the IntegrityError,
    # so one such run in a batch rolled back the whole batch, every pass.
    from app.tasks.cleanup_tasks import purge_old_runs

    d = scene.doomed
    await session.exec(text(
        "UPDATE project SET data_policy = CAST(:p AS JSON) WHERE id = :i"),
        params={"p": '{"retention_days": 1}', "i": d.project_id})
    await session.exec(text(
        "UPDATE testrun SET created_at = :t WHERE project_id = :i"),
        params={"t": datetime.utcnow() - timedelta(days=10), "i": d.project_id})
    await session.commit()

    assert purge_old_runs() == 2
    await _resync(session)
    assert await session.get(TestRun, d.run_id) is None
    assert (await session.get(SelectorHealProposal, d.heal_id)).source_run_id is None
    await _assert_tree_intact(session, scene.sibling)


# --- 4. Case delete (same shape, same fix) ------------------------------------

async def test_case_delete_with_revision_and_requirement_succeeds(session, scene):
    from app.api.endpoints.test_cases import delete_test_case

    d = scene.doomed
    result = await delete_test_case(case_id=d.case_id, session=session,
                                    current_user=scene.admin)
    assert result["status"] == "success"
    await _resync(session)
    assert await session.get(TestCase, d.case_id) is None
    # The single-case run goes with its case; the suite run stays and keeps
    # its result, de-linked from the case (testcaseresult.test_case_id).
    assert await session.get(TestRun, d.case_run_id) is None
    assert await session.get(TestRun, d.run_id) is not None
    linked = (await session.exec(text(
        "SELECT test_case_id FROM testcaseresult WHERE test_run_id = :r"),
        params={"r": d.run_id})).all()
    assert linked == [(None,)]
    await _assert_tree_intact(session, scene.sibling)


# --- project boundary -----------------------------------------------------------
# Access is checked for the deleted entity's project only, so the cascade must
# never reach another project's rows. Cross-project references shouldn't exist,
# but nothing in the schema forbids them; when one does, the delete is refused
# (409 via the app's exception handler) and both sides survive untouched.

async def test_suite_delete_refuses_to_cascade_into_another_project(session, scene):
    from app.api.endpoints.test_suites import delete_test_suite
    from app.services.entity_delete import CrossProjectReference

    d, s = scene.doomed, scene.sibling
    await session.exec(text("UPDATE testcase SET test_suite_id = :suite WHERE id = :case"),
                       params={"suite": d.suite_id, "case": s.case_id})
    await session.commit()

    with pytest.raises(CrossProjectReference) as err:
        await delete_test_suite(suite_id=d.suite_id, session=session,
                                current_user=scene.admin)
    assert err.value.table == "testcase"
    assert err.value.project_ids == [s.project_id]
    await _resync(session)

    assert await session.get(TestSuite, d.suite_id) is not None
    assert await session.get(TestCase, s.case_id) is not None
    assert await session.get(TestCase, d.case_id) is not None


async def test_case_delete_refuses_to_take_another_projects_run(session, scene):
    from app.api.endpoints.test_cases import delete_test_case
    from app.services.entity_delete import CrossProjectReference

    d, s = scene.doomed, scene.sibling
    await session.exec(text("UPDATE testrun SET test_case_id = :case WHERE id = :run"),
                       params={"case": d.case_id, "run": s.case_run_id})
    await session.commit()

    with pytest.raises(CrossProjectReference):
        await delete_test_case(case_id=d.case_id, session=session,
                               current_user=scene.admin)
    await _resync(session)

    assert await session.get(TestCase, d.case_id) is not None
    assert await session.get(TestRun, s.case_run_id) is not None


async def test_run_delete_refuses_to_take_another_projects_finding(session, scene):
    from app.api.endpoints.test_runs import delete_run
    from app.services.entity_delete import CrossProjectReference

    d, s = scene.doomed, scene.sibling
    await session.exec(text("UPDATE securityfinding SET run_id = :run WHERE project_id = :p"),
                       params={"run": d.run_id, "p": s.project_id})
    await session.commit()

    with pytest.raises(CrossProjectReference):
        await delete_run(run_id=d.run_id, session=session, current_user=scene.admin)
    await _resync(session)

    assert await session.get(TestRun, d.run_id) is not None
    assert await _scalar(session, "SELECT count(*) FROM securityfinding WHERE project_id = :p",
                         p=s.project_id) == 1


async def test_a_projectless_root_cannot_cascade_into_a_project(session, scene):
    # A legacy suite with no project must not be a way into any project's data.
    from app.services.entity_delete import CrossProjectReference, cascade_delete

    d = scene.doomed
    await session.exec(text("UPDATE testsuite SET project_id = NULL WHERE id = :s"),
                       params={"s": d.child_suite_id})
    await session.commit()

    with pytest.raises(CrossProjectReference):
        await cascade_delete(session, "testsuite", [d.child_suite_id])
    await _resync(session)
    assert await session.get(TestCase, d.case_id) is not None
