"""Environment locks (V3) + validated repairs (V4) against a real Postgres (WP16c).

    ./run-tests-live.sh tests/integration/test_env_lock_validation_db.py -q

Redis is an in-memory fake (tests/fake_lease_redis.py); the dispatcher, the
aggregator's finalize/timeout paths, cancel, the endpoints and every
aggregate query run for real.
"""
from __future__ import annotations

import json
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
    AuditLog, CaseProposal, CaseProposalAction, Project, ProjectEnvironment, RepairOutcome,
    SelectorHealProposal, Tenant, TestCase, TestCaseResult, TestCaseRevision, TestRun,
    TestStatus, TestSuite, User, UserProjectAccess, UserSystemRole, UserWorkspace, Workspace,
)
from app.services import environment_lock, run_completion    # noqa: E402
from app.services.rbac_service import rbac_service           # noqa: E402
from tests.fake_lease_redis import FakeLeaseRedis            # noqa: E402

STEPS = [{"id": "s1", "type": "goto", "value": "/cart"},
         {"id": "s2", "type": "click", "selector": "#buy-old"}]
SYNC_URL = db_url_for(settings.DATABASE_URL, sync=True)


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


@pytest.fixture
def sync_session():
    engine = create_engine(SYNC_URL)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def plumbing(monkeypatch):
    """Fake lease store, no Celery, no job streams, no completion tasks."""
    from app import worker
    from app.core import redis as core_redis
    fake = FakeLeaseRedis()
    monkeypatch.setattr(environment_lock, "_client", fake)
    deferred, dispatched = [], []
    monkeypatch.setattr(worker.run_test_suite, "delay", lambda *a, **k: None)
    monkeypatch.setattr(worker.run_test_suite, "apply_async",
                        lambda args, kwargs=None, countdown=None: deferred.append((args[0], countdown)))
    monkeypatch.setattr(worker, "dispatch_cases_to_queue",
                        lambda run, cases, eff, s: dispatched.append(
                            (run.id, [json.loads(json.dumps(worker._case_payload(c), default=str))
                                      for c in cases], dict(eff))))
    monkeypatch.setattr(run_completion, "SIDE_EFFECT_TASKS", ())

    class AsyncFake:
        async def set(self, *a, **k):
            return True
    monkeypatch.setattr(core_redis.RedisClient, "get_instance", classmethod(lambda cls: AsyncFake()))
    return SimpleNamespace(redis=fake, deferred=deferred, dispatched=dispatched)


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session):
    editor = User(email="ed@corp.example.com", full_name="Ed", hashed_password="x", is_verified=True)
    viewer = User(email="vi@corp.example.com", full_name="Vi", hashed_password="x", is_verified=True)
    session.add_all([editor, viewer])
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=editor.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    member = await rbac_service.get_role_by_name(session, "Workspace Member")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=editor.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=editor.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=viewer.id, workspace_id=ws.id, role="member", role_id=member.id),
    ])
    project = Project(name="Shop", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    session.add(UserProjectAccess(user_id=viewer.id, project_id=project.id, access_level="viewer"))
    env = ProjectEnvironment(project_id=project.id, name="staging", base_url="https://staging.test",
                             exclusive=True, is_default=True)
    suite = TestSuite(name="Checkout", project_id=project.id)
    empty = TestSuite(name="Empty", project_id=project.id)
    session.add_all([env, suite, empty])
    await session.flush()
    case = TestCase(name="buy", test_suite_id=suite.id, project_id=project.id, steps=STEPS)
    session.add(case)
    await session.commit()
    return SimpleNamespace(editor=editor, viewer=viewer, ws_id=ws.id, project_id=project.id,
                           env_id=env.id, suite_id=suite.id, empty_suite_id=empty.id, case_id=case.id)


async def _run(session, scene, *, status=TestStatus.PENDING, suite=None, case=True, **kw):
    r = TestRun(test_suite_id=suite or scene.suite_id, test_case_id=scene.case_id if case else None,
                project_id=scene.project_id, status=status, **kw)
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return r


def _p(user, key=None):
    return AuthPrincipal(user=user, api_key=key)


async def _status(session, run_id):
    run = await session.get(TestRun, run_id)
    await session.refresh(run)
    return run.status


# ---------------------------------------------------------------------------
# V3 — environment locks
# ---------------------------------------------------------------------------

async def test_second_run_on_an_exclusive_environment_waits_pending(session, scene, plumbing):
    from app import worker
    a, b = await _run(session, scene), await _run(session, scene)
    worker.run_test_suite(a.id)
    worker.run_test_suite(b.id)
    assert await _status(session, a.id) == TestStatus.RUNNING
    assert await _status(session, b.id) == TestStatus.PENDING        # deferred, never failed
    assert plumbing.deferred == [(b.id, environment_lock.RETRY_SECONDS)]
    assert [d[0] for d in plumbing.dispatched] == [a.id]
    assert environment_lock.holder(scene.env_id)["run_id"] == a.id

    # Run page + environment list show who holds it.
    from app.api.endpoints import test_runs
    from app.api import environments
    wait = await test_runs.run_environment_lock(b.id, session=session, principal=_p(scene.viewer))
    assert wait["state"] == "waiting" and wait["holder_run_id"] == a.id
    assert wait["environment"] == "staging"
    hold = await test_runs.run_environment_lock(a.id, session=session, principal=_p(scene.viewer))
    assert hold["state"] == "holding"
    listed = await environments.list_environments(scene.project_id, session=session,
                                                  current_user=scene.viewer)
    assert listed[0]["lock"]["run_id"] == a.id and listed[0]["exclusive"] is True


@pytest.mark.parametrize("path", ["completion", "timeout", "cancel", "force_complete"])
async def test_every_terminal_path_releases_the_lease(session, sync_session, scene, plumbing, path):
    from app import worker
    a, b = await _run(session, scene), await _run(session, scene)
    worker.run_test_suite(a.id)
    worker.run_test_suite(b.id)
    assert environment_lock.holder(scene.env_id)["run_id"] == a.id

    if path == "completion":
        run = sync_session.get(TestRun, a.id)
        run.status = TestStatus.PASSED
        sync_session.add(run)
        run_completion.finalize_on_commit(sync_session, a.id)
        sync_session.commit()
    elif path == "timeout":
        from app.tasks.result_aggregator import _mark_run_timeout
        run = sync_session.get(TestRun, a.id)
        _mark_run_timeout(run, None, sync_session, reason="test")
        sync_session.commit()
    elif path == "cancel":
        from app.api.endpoints import test_runs
        req = Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                       "client": ("203.0.113.9", 4000)})
        await test_runs.cancel_test_run(run_id=a.id, request=req, session=session,
                                        principal=_p(scene.editor))
    else:
        from app.api.endpoints import test_runs
        await test_runs.force_complete_test_run(a.id, status=TestStatus.ERROR, error_message="x",
                                                session=session, current_user=scene.editor)

    assert environment_lock.holder(scene.env_id) is None
    worker.run_test_suite(b.id)          # the waiter's next retry gets in
    assert await _status(session, b.id) == TestStatus.RUNNING
    assert environment_lock.holder(scene.env_id)["run_id"] == b.id


async def test_dispatch_error_releases_the_lease(session, scene, plumbing):
    from app import worker
    broken = await _run(session, scene, suite=scene.empty_suite_id, case=False)   # no cases
    worker.run_test_suite(broken.id)
    run = await session.get(TestRun, broken.id)
    await session.refresh(run)
    assert run.status == TestStatus.ERROR and "No test cases" in run.error_message
    assert environment_lock.holder(scene.env_id) is None


async def test_heartbeat_frees_a_lease_whose_release_was_lost(session, sync_session, scene, plumbing):
    from app import worker
    a = await _run(session, scene)
    worker.run_test_suite(a.id)
    # Finished without going through completion (legacy webhook path).
    await session.exec(text(f"UPDATE testrun SET status='FAILED' WHERE id={a.id}"))
    await session.commit()
    assert environment_lock.refresh(sync_session) == {"renewed": 0, "released": 1}
    assert environment_lock.holder(scene.env_id) is None


async def test_force_release_is_editor_only_and_audited(session, scene, plumbing):
    from app import worker
    from app.api import environments
    a = await _run(session, scene)
    worker.run_test_suite(a.id)
    with pytest.raises(HTTPException) as exc:
        await environments.force_release_lock(scene.env_id, principal=_p(scene.viewer), session=session)
    assert exc.value.status_code == 403
    out = await environments.force_release_lock(scene.env_id, principal=_p(scene.editor), session=session)
    assert out["released"] and out["previous_holder"]["run_id"] == a.id
    audit = (await session.exec(select(AuditLog).where(AuditLog.action == "lock_force_released"))).all()
    assert len(audit) == 1 and audit[0].changes["holder_run_id"] == a.id
    assert audit[0].workspace_id == scene.ws_id


async def test_exclusive_toggle_is_audited(session, scene, plumbing):
    from app.api import environments
    await environments.update_environment(scene.env_id, environments.EnvironmentUpdate(exclusive=False),
                                          session=session, current_user=scene.editor)
    assert (await session.exec(select(AuditLog).where(AuditLog.action == "exclusive_off"))).first()


async def test_non_exclusive_environment_takes_no_lease(session, scene, plumbing):
    from app import worker
    env = await session.get(ProjectEnvironment, scene.env_id)
    env.exclusive = False
    session.add(env)
    await session.commit()
    a, b = await _run(session, scene), await _run(session, scene)
    worker.run_test_suite(a.id)
    worker.run_test_suite(b.id)
    assert plumbing.deferred == [] and plumbing.redis.data == {}


# ---------------------------------------------------------------------------
# V4 — validated repairs
# ---------------------------------------------------------------------------

async def _heal(session, scene, source_run_id=None):
    p = SelectorHealProposal(test_case_id=scene.case_id, step_id="s2", old_selector="#buy-old",
                             new_selector="#buy", chosen_selector="#buy",
                             candidates=[{"selector": "#buy"}, {"selector": "[data-testid=buy]"}],
                             source_run_id=source_run_id)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


async def _finish(session, run_id, status, result_status=None):
    run = await session.get(TestRun, run_id)
    await session.refresh(run)
    run.status = status
    session.add(run)
    session.add(TestCaseResult(test_run_id=run_id, test_name="buy", status=result_status or status,
                               duration_ms=10, test_case_id=run.test_case_id))
    await session.commit()
    run_completion.queue_side_effects(run_id, is_validation=True)   # drops the env lease
    from app.tasks.repair_validation_tasks import record_validation_outcome
    return record_validation_outcome(run_id)


async def test_heal_validation_overlays_one_run_and_never_touches_the_case(session, scene, plumbing):
    from app import worker
    from app.api import heal_proposals
    env = await session.get(ProjectEnvironment, scene.env_id)
    env.exclusive = False
    session.add(env)
    await session.commit()
    failing = await _run(session, scene, status=TestStatus.FAILED, browser="firefox",
                         device="Pixel 7", environment_id=scene.env_id)
    p = await _heal(session, scene, source_run_id=failing.id)

    out = await heal_proposals.validate_proposal(
        p.id, choice=heal_proposals.HealSelectorChoice(selector="[data-testid=buy]"),
        principal=_p(scene.editor), session=session)
    run = await session.get(TestRun, out["run_id"])
    assert run.is_validation and run.validation_proposal_kind == "heal"
    assert (run.browser, run.device, run.environment_id) == ("firefox", "Pixel 7", scene.env_id)
    await session.refresh(p)
    assert p.validation_status == "validating" and p.validation_run_id == run.id

    worker.run_test_suite(run.id)
    (_rid, payloads, eff), = plumbing.dispatched
    assert payloads[0]["steps"][1]["selector"] == "[data-testid=buy]"
    assert eff["validation_run"] is True and eff["auto_retry"] is False

    # The stored case, its revisions and its sync document are untouched.
    case = await session.get(TestCase, scene.case_id)
    await session.refresh(case)
    assert case.steps[1]["selector"] == "#buy-old"
    assert (await session.exec(select(TestCaseRevision).where(
        TestCaseRevision.test_case_id == scene.case_id))).all() == []
    from app.services.case_sync import case_doc
    assert "#buy-old" in json.dumps(case_doc(case)) and "data-testid" not in json.dumps(case_doc(case))

    assert await _finish(session, run.id, TestStatus.PASSED) == "validated"
    await session.refresh(p)
    assert p.validation_status == "validated" and "failing run" in p.validation_detail
    outcome = (await session.exec(select(RepairOutcome).where(RepairOutcome.proposal_id == p.id))).one()
    assert outcome.event == "validation_passed" and outcome.conditions_matched is True


async def test_required_validation_gates_heal_accept(session, scene, plumbing):
    from app import worker
    from app.api import heal_proposals
    ws = await session.get(Workspace, scene.ws_id)
    ws.require_validation_before_accept = True
    session.add(ws)
    await session.commit()
    p = await _heal(session, scene)

    with pytest.raises(HTTPException) as exc:
        await heal_proposals.accept_proposal(p.id, choice=None, principal=_p(scene.editor), session=session)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "validation_required"

    out = await heal_proposals.validate_proposal(p.id, choice=None, principal=_p(scene.editor),
                                                 session=session)
    worker.run_test_suite(out["run_id"])
    assert await _finish(session, out["run_id"], TestStatus.FAILED) == "validation_failed"
    with pytest.raises(HTTPException):
        await heal_proposals.accept_proposal(p.id, choice=None, principal=_p(scene.editor), session=session)

    out = await heal_proposals.validate_proposal(p.id, choice=None, principal=_p(scene.editor),
                                                 session=session)
    worker.run_test_suite(out["run_id"])
    assert await _finish(session, out["run_id"], TestStatus.PASSED) == "validated"
    await session.refresh(p)     # settled by the Celery task in its own session
    # A different candidate needs its own validation run.
    with pytest.raises(HTTPException) as exc:
        await heal_proposals.accept_proposal(
            p.id, choice=heal_proposals.HealSelectorChoice(selector="[data-testid=buy]"),
            principal=_p(scene.editor), session=session)
    assert exc.value.status_code == 409
    accepted = await heal_proposals.accept_proposal(p.id, choice=None, principal=_p(scene.editor),
                                                    session=session)
    assert accepted["status"] == "accepted"
    events = [o.event for o in (await session.exec(select(RepairOutcome).where(
        RepairOutcome.proposal_id == p.id).order_by(RepairOutcome.id))).all()]
    assert events == ["validation_failed", "validation_passed", "accepted"]


async def test_a_runtime_heal_fails_the_validation(session, scene, plumbing):
    from app import worker
    from app.api import heal_proposals
    from app.tasks.result_aggregator import _persist_heal_suggestions
    p = await _heal(session, scene)
    out = await heal_proposals.validate_proposal(p.id, choice=None, principal=_p(scene.editor),
                                                 session=session)
    worker.run_test_suite(out["run_id"])
    _persist_heal_suggestions({"run_id": out["run_id"], "heal_suggestions": [
        {"test_case_id": scene.case_id, "step_id": "s2", "new_selector": "#other", "applied": True}]})
    # No new proposal from a validation run…
    assert len((await session.exec(select(SelectorHealProposal))).all()) == 1
    # …and the pass does not count.
    assert await _finish(session, out["run_id"], TestStatus.PASSED) == "validation_failed"


async def test_protected_suite_forces_validation_and_blocks_auto_apply(session, scene, plumbing):
    from app.api import agent_ownership
    from app.api.endpoints import test_suites
    with pytest.raises(HTTPException) as exc:
        await test_suites.set_suite_protected(scene.suite_id, test_suites.SuiteProtection(protected=True),
                                              principal=_p(scene.viewer), session=session)
    assert exc.value.status_code == 403
    await test_suites.set_suite_protected(scene.suite_id, test_suites.SuiteProtection(protected=True),
                                          principal=_p(scene.editor), session=session)
    assert (await session.exec(select(AuditLog).where(AuditLog.action == "protected"))).first()

    ws = await session.get(Workspace, scene.ws_id)
    ws.auto_apply_threshold = 0.5
    session.add(ws)
    await session.commit()
    prop = CaseProposal(project_id=scene.project_id, target_case_id=scene.case_id,
                        action=CaseProposalAction.UPDATE, ai_confidence=0.99,
                        payload={"steps": [{"id": "s1", "type": "goto", "value": "/cart2"}]})
    session.add(prop)
    await session.flush()
    assert await agent_ownership.maybe_auto_apply(prop, scene.editor.id, session) is False
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await agent_ownership.accept_proposal(prop.id, note=None, principal=_p(scene.editor),
                                              session=session)
    assert exc.value.status_code == 409

    from app import worker
    out = await agent_ownership.validate_proposal(prop.id, principal=_p(scene.editor), session=session)
    worker.run_test_suite(out["run_id"])
    (_rid, payloads, _eff), = plumbing.dispatched[-1:]
    assert payloads[0]["steps"][0]["value"] == "/cart2"
    case = await session.get(TestCase, scene.case_id)
    await session.refresh(case)
    assert case.steps[0]["value"] == "/cart"                     # stored case untouched
    assert await _finish(session, out["run_id"], TestStatus.PASSED) == "validated"
    await session.refresh(prop)
    read = await agent_ownership.accept_proposal(prop.id, note=None, principal=_p(scene.editor),
                                                 session=session)
    assert read.status == "accepted" and read.validation_status == "validated"
    await session.refresh(case)
    assert case.steps[0]["value"] == "/cart2"


async def test_proposal_policy_round_trip(session, scene, plumbing):
    from app.api import agent_ownership
    out = await agent_ownership.set_proposal_policy(
        scene.ws_id, agent_ownership.ProposalPolicyBody(require_validation_before_accept=True),
        principal=_p(scene.editor), session=session)
    assert out["require_validation_before_accept"] and out["validation_enforced"]
    assert (await session.exec(select(AuditLog).where(
        AuditLog.action == "require_validation_before_accept"))).first()


async def test_validation_runs_are_excluded_from_every_aggregate(session, sync_session, scene, plumbing):
    """One real passing run and one validation run that failed (newest).
    Each aggregate must look exactly as if the validation run did not exist."""
    from datetime import datetime, timedelta
    from app.api import analytics, dora
    from app.api.endpoints import quality
    from app.services import release_readiness, reporting
    from app.tasks.result_aggregator import _update_flake_scores

    real = await _run(session, scene, status=TestStatus.PASSED, git_commit="abc")
    session.add(TestCaseResult(test_run_id=real.id, test_name="buy", status=TestStatus.PASSED,
                               duration_ms=5, test_case_id=scene.case_id))
    val = await _run(session, scene, status=TestStatus.FAILED, git_commit="abc", is_validation=True,
                     validation_proposal_kind="heal", validation_proposal_id=1)
    session.add(TestCaseResult(test_run_id=val.id, test_name="buy", status=TestStatus.FAILED,
                               duration_ms=5, test_case_id=scene.case_id))
    await session.commit()

    stats = await reporting.stats_by_project(session, [scene.project_id],
                                             datetime.utcnow() - timedelta(days=1), datetime.utcnow())
    assert stats[scene.project_id]["runs"] == 1 and stats[scene.project_id]["failures"] == 0
    top = await reporting.top_failing(session, [scene.project_id], datetime.utcnow() - timedelta(days=1))
    assert top == []
    assert (await reporting.last_runs(session, [scene.project_id]))[scene.project_id]["id"] == real.id

    scope, commit, runs = await release_readiness.resolve_runs(session, scene.project_id, None, None)
    assert [r.id for r in runs] == [real.id]
    in_scope = await release_readiness.results_in_scope(session, scene.project_id, "abc", [])
    assert all(row.run_id == real.id for rows in in_scope.values() for row in rows)

    gate = await quality.evaluate_quality_gate(scene.project_id, git_commit=None, git_branch=None,
                                               run_ids=[real.id, val.id],
                                               principal=_p(scene.editor), session=session)
    assert gate.passed is True                       # the failed validation run is not evidence
    trends = await analytics._daily_trends(session, [TestRun.project_id == scene.project_id], 2)
    assert sum(t["total_runs"] for t in trends) == 1

    dora_out = await dora.dora_metrics(scene.project_id, days=30, principal=_p(scene.editor),
                                       session=session)
    assert dora_out is not None

    # Aggregator: a validation run neither stamps last_validated_commit nor scores flakes.
    v = sync_session.get(TestRun, val.id)
    v.status = TestStatus.PASSED
    sync_session.add(v)
    sync_session.commit()
    _update_flake_scores(v, v.id, sync_session)
    sync_session.commit()
    case = await session.get(TestCase, scene.case_id)
    await session.refresh(case)
    assert case.last_validated_commit is None
    from app.models import FlakeRecord
    assert (await session.exec(select(FlakeRecord))).all() == []
