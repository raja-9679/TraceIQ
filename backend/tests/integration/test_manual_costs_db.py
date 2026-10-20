"""Manual runs, release sign-off and cost reports against a real Postgres (WP12d).

    ./run-tests-live.sh tests/integration/test_manual_costs_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import costs, manual_testing as mtapi                # noqa: E402
from app.api.endpoints import test_runs                           # noqa: E402
from app.core.auth import AuthPrincipal                            # noqa: E402
from app.core.config import db_url_for, settings                   # noqa: E402
from app.models import (                                           # noqa: E402
    AuditLog, CostBudgetAlert, LLMUsageEvent, ProjectCostBudget, TestCase, TestCaseResult,
    TestRun, TestStatus, User, UserProjectAccess, Workspace,
)
from app.services import cost as cost_svc                          # noqa: E402

from tests.integration.test_targeted_runs_db import (              # noqa: E402,F401
    no_queue, scene, session,
)
from tests.integration.denied import DENIED  # noqa: E402

MANUAL_STEPS = [
    {"id": "s1", "type": "manual-step", "value": "Open the invoice", "params": {"expected": "VAT shown"}},
    {"id": "s2", "type": "manual-step", "value": "Download it", "params": {"expected": "A PDF"}},
]


def _p(user):
    return AuthPrincipal(user=user, api_key=None)


async def _user(session, email, project_id=None, level=None):
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if project_id:
        session.add(UserProjectAccess(user_id=u.id, project_id=project_id, access_level=level))
    await session.commit()
    return u


async def _manual_cases(session, scene):
    cases = [TestCase(name=n, test_suite_id=scene.sibling_id, project_id=scene.project_id,
                      executor="manual", steps=MANUAL_STEPS) for n in ("invoice", "refund")]
    session.add_all(cases)
    await session.commit()
    return [c.id for c in cases]


async def _finished_manual_run(session, scene, tester):
    await _manual_cases(session, scene)
    run = await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.sibling_id,
                                                             release_label="v2.4"),
                                       None, session=session, principal=_p(scene.user))
    for res, outcome in zip(run["results"], ("passed", "blocked")):
        await mtapi.record_manual_result(
            run["id"], res["id"], mtapi.ManualResultIn(outcome=outcome, notes="n"), None,
            session=session, principal=_p(tester))
    await mtapi.complete_manual_run(run["id"], None, mtapi.CompleteIn(), session=session,
                                    principal=_p(tester))
    return run["id"]


# --- manual runs ----------------------------------------------------------------


async def test_manual_run_records_results_and_shows_in_history(session, scene):
    editor = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    case_ids = await _manual_cases(session, scene)
    run = await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.sibling_id),
                                       None, session=session, principal=_p(editor))
    assert run["status"] == "pending" and run["executor"] == "manual"
    assert sorted(r["test_case_id"] for r in run["results"]) == sorted(case_ids)
    assert run["results"][0]["steps"][0] == {"instruction": "Open the invoice", "expected": "VAT shown"}

    first, second = run["results"]
    out = await mtapi.record_manual_result(
        run["id"], first["id"],
        mtapi.ManualResultIn(outcome="failed", notes="VAT missing", duration_ms=90_000,
                             step_results=[mtapi.StepResultIn(outcome="passed"),
                                           mtapi.StepResultIn(outcome="failed", note="no VAT")]),
        None, session=session, principal=_p(editor))
    assert out["outcome"] == "failed" and out["tester_id"] == editor.id
    assert out["tester_name"] == "tess"

    with pytest.raises(HTTPException) as exc:     # one case still open
        await mtapi.complete_manual_run(run["id"], None, mtapi.CompleteIn(),
                                        session=session, principal=_p(editor))
    assert exc.value.status_code == 409
    done = await mtapi.complete_manual_run(run["id"], None, mtapi.CompleteIn(skip_unrecorded=True),
                                           session=session, principal=_p(editor))
    assert done["status"] == "failed"
    assert done["summary"]["counts"]["skipped"] == 1

    row = await session.get(TestRun, run["id"])
    assert (row.passed_tests, row.failed_tests, row.total_tests) == (0, 1, 2)
    assert row.finalized_at is not None
    res = await session.get(TestCaseResult, first["id"])
    assert res.status == TestStatus.FAILED and res.error_message == "VAT missing"
    assert res.result_kind == "manual"

    # In the ordinary run history, marked manual.
    listed = await test_runs.get_runs(project_id=scene.project_id, session=session,
                                      current_user=scene.user)
    mine = [r for r in listed["runs"] if r.id == run["id"]]
    assert mine and mine[0].executor == "manual"

    # Completed runs are read-only.
    with pytest.raises(HTTPException) as exc:
        await mtapi.record_manual_result(run["id"], second["id"], mtapi.ManualResultIn(outcome="passed"),
                                         None, session=session, principal=_p(editor))
    assert exc.value.status_code == 409
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "manual_run", AuditLog.entity_id == run["id"]))).all()
    assert {"start", "record", "complete"} <= set(actions)


async def test_manual_run_access(session, scene):
    await _manual_cases(session, scene)
    viewer = await _user(session, "view@corp.example.com", scene.project_id, "viewer")
    outsider = await _user(session, "out@corp.example.com", scene.other_project_id, "editor")
    for who in (viewer, outsider):
        with pytest.raises(HTTPException) as exc:
            await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.sibling_id),
                                         None, session=session, principal=_p(who))
        assert exc.value.status_code == 403
    run = await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.sibling_id),
                                       None, session=session, principal=_p(scene.user))
    assert (await mtapi.get_manual_run(run["id"], session=session, principal=_p(viewer)))["id"] == run["id"]
    with pytest.raises(HTTPException) as exc:
        await mtapi.get_manual_run(run["id"], session=session, principal=_p(outsider))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await mtapi.record_manual_result(run["id"], run["results"][0]["id"],
                                         mtapi.ManualResultIn(outcome="passed"), None,
                                         session=session, principal=_p(viewer))
    assert exc.value.status_code == 403


async def test_automated_cases_cannot_be_put_in_a_manual_run(session, scene):
    await _manual_cases(session, scene)
    with pytest.raises(HTTPException) as exc:
        await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.root_id),
                                     None, session=session, principal=_p(scene.user))
    assert exc.value.status_code == 400          # root suite has only automated cases
    with pytest.raises(HTTPException) as exc:
        await mtapi.start_manual_run(mtapi.ManualRunCreate(suite_id=scene.sibling_id,
                                                           case_ids=[scene.sibling_case]),
                                     None, session=session, principal=_p(scene.user))
    assert exc.value.status_code == 400


# --- sign-off -------------------------------------------------------------------


def _signoff(run_ids, verdict="approve"):
    return mtapi.SignoffIn(release_label="v2.4", run_ids=run_ids, verdict=verdict, note="ok")


async def test_signoff_needs_editor_on_this_project(session, scene):
    tester = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    run_id = await _finished_manual_run(session, scene, tester)
    viewer = await _user(session, "view@corp.example.com", scene.project_id, "viewer")
    outsider = await _user(session, "out@corp.example.com", scene.other_project_id, "admin")
    for who in (viewer, outsider):
        with pytest.raises(HTTPException) as exc:
            await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None,
                                       session=session, principal=_p(who))
        assert exc.value.status_code == 403
    # An API key never signs, whatever its scope.
    with pytest.raises(HTTPException) as exc:
        await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None, session=session,
                                   principal=AuthPrincipal(user=tester, api_key=NS(id=1, project_id=None)))
    assert exc.value.status_code in DENIED

    out = await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None,
                                     session=session, principal=_p(tester))
    assert out["verdict"] == "approve" and out["signed_by_id"] == tester.id
    listed = await mtapi.list_signoffs(scene.project_id, session=session, principal=_p(viewer))
    assert [s["id"] for s in listed] == [out["id"]]
    with pytest.raises(HTTPException) as exc:
        await mtapi.list_signoffs(scene.project_id, session=session, principal=_p(outsider))
    assert exc.value.status_code == 403
    audit = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "release_signoff", AuditLog.entity_id == out["id"]))).first()
    assert audit is not None and audit.action == "approve"


async def test_signoff_refuses_runs_of_another_project(session, scene):
    tester = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    run_id = await _finished_manual_run(session, scene, tester)
    foreign = TestRun(test_suite_id=scene.root_id, project_id=scene.other_project_id,
                      status=TestStatus.PASSED)
    session.add(foreign)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await mtapi.create_signoff(scene.project_id, _signoff([run_id, foreign.id]), None,
                                   session=session, principal=_p(scene.user))
    assert exc.value.status_code == 400


async def test_four_eyes_when_the_workspace_requires_a_separate_approver(session, scene):
    tester = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    other = await _user(session, "olga@corp.example.com", scene.project_id, "editor")
    run_id = await _finished_manual_run(session, scene, tester)
    ws = (await session.exec(select(Workspace))).first()
    ws.require_separate_approver = True
    session.add(ws)
    await session.commit()
    for executor in (tester, scene.user):          # recorded results / started the run
        with pytest.raises(HTTPException) as exc:
            await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None,
                                       session=session, principal=_p(executor))
        assert exc.value.status_code == 403
        assert "Separation of duties" in exc.value.detail
    out = await mtapi.create_signoff(scene.project_id, _signoff([run_id], "reject"), None,
                                     session=session, principal=_p(other))
    assert out["verdict"] == "reject"


async def test_without_the_policy_the_tester_may_sign(session, scene):
    tester = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    run_id = await _finished_manual_run(session, scene, tester)
    out = await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None,
                                     session=session, principal=_p(tester))
    assert out["id"]


async def test_gate_check_reads_the_newest_signoff(session, scene):
    from app.models import QualityGatePolicy
    from app.services.release_readiness import readiness_gate_checks
    tester = await _user(session, "tess@corp.example.com", scene.project_id, "editor")
    run_id = await _finished_manual_run(session, scene, tester)
    policy = QualityGatePolicy(require_manual_signoff=True)
    checks = await readiness_gate_checks(session, scene.project_id, policy, [run_id], None)
    assert [c.name for c in checks] == ["manual_signoff"] and checks[0].missing_data
    await mtapi.create_signoff(scene.project_id, _signoff([run_id]), None, session=session,
                               principal=_p(tester))
    checks = await readiness_gate_checks(session, scene.project_id, policy, [run_id], None)
    assert checks[0].passed


# --- costs ----------------------------------------------------------------------


async def _usage(session, scene):
    ws = (await session.exec(select(Workspace))).first()
    session.add_all([
        LLMUsageEvent(workspace_id=ws.id, project_id=scene.project_id, provider="anthropic",
                      model="m", feature="failure_analysis", input_tokens=1_000_000,
                      output_tokens=0, total_tokens=1_000_000),
        LLMUsageEvent(workspace_id=ws.id, project_id=scene.other_project_id, provider="anthropic",
                      model="m", feature="failure_analysis", input_tokens=5_000_000,
                      output_tokens=0, total_tokens=5_000_000),
        LLMUsageEvent(workspace_id=ws.id, project_id=None, provider="anthropic", model="m",
                      feature="case_generation", input_tokens=7, output_tokens=0, total_tokens=7),
    ])
    auto = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.PASSED)
    manual = TestRun(test_suite_id=scene.sibling_id, project_id=scene.project_id,
                     status=TestStatus.PASSED, executor="manual")
    session.add_all([auto, manual])
    await session.flush()
    session.add_all([
        TestCaseResult(test_run_id=auto.id, test_name="a", status=TestStatus.PASSED, duration_ms=120_000),
        # A person's 2 hours are not worker minutes.
        TestCaseResult(test_run_id=manual.id, test_name="m", status=TestStatus.PASSED, duration_ms=7_200_000),
    ])
    await session.commit()
    return ws


async def test_cost_report_covers_only_accessible_projects(session, scene):
    ws = await _usage(session, scene)
    member = await _user(session, "pm@corp.example.com", scene.project_id, "viewer")
    rep = await costs.cost_report(ws.id, days=30, project_id=None, session=session, principal=_p(member))
    assert [p["project_id"] for p in rep["projects"]] == [scene.project_id]
    assert rep["llm"]["tokens"] == 1_000_000            # not the other project's, not unassigned
    assert rep["includes_unassigned"] is False
    assert rep["browser"]["minutes"] == 2.0             # manual run excluded
    with pytest.raises(HTTPException) as exc:
        await costs.cost_report(ws.id, days=30, project_id=scene.other_project_id,
                                session=session, principal=_p(member))
    assert exc.value.status_code == 403
    stranger = await _user(session, "x@else.example.com")
    with pytest.raises(HTTPException) as exc:
        await costs.cost_report(ws.id, days=30, project_id=None, session=session, principal=_p(stranger))
    assert exc.value.status_code == 403

    admin = await costs.cost_report(ws.id, days=30, project_id=None, session=session,
                                    principal=_p(scene.user))
    assert admin["llm"]["tokens"] == 6_000_007 and admin["includes_unassigned"]


async def test_budget_needs_project_admin(session, scene):
    editor = await _user(session, "ed@corp.example.com", scene.project_id, "editor")
    with pytest.raises(HTTPException) as exc:
        await costs.set_budget(scene.project_id, costs.BudgetIn(monthly_amount=10), None,
                               session=session, principal=_p(editor))
    assert exc.value.status_code == 403
    out = await costs.set_budget(scene.project_id, costs.BudgetIn(monthly_amount=10), None,
                                 session=session, principal=_p(scene.user))
    assert out["monthly_amount"] == 10


async def test_budget_alerts_once_per_threshold_per_month(session, scene, monkeypatch):
    ws = await _usage(session, scene)                   # 1M tokens x 3/1M = 3.00 EUR
    session.add(ProjectCostBudget(project_id=scene.project_id, monthly_amount=3.5))
    await session.commit()
    from app.tasks import cost_tasks
    prices = cost_svc.Prices(currency="EUR", models={"m": (Decimal(3), Decimal(0))})
    monkeypatch.setattr(cost_svc, "load_prices", lambda get=None: prices)
    sent = []

    def deliver(s, project, threshold, spent, budget, currency, period):
        sent.append((project.id, threshold, spent, currency))

    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(engine) as s:
            assert cost_tasks.check_budgets(s, deliver=deliver) == 1     # 86% -> 80
            assert cost_tasks.check_budgets(s, deliver=deliver) == 0     # same month: quiet
        session.add(LLMUsageEvent(workspace_id=ws.id, project_id=scene.project_id,
                                  provider="anthropic", model="m", feature="x",
                                  input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000))
        await session.commit()
        with Session(engine) as s:
            assert cost_tasks.check_budgets(s, deliver=deliver) == 1     # 171% -> 100
            assert cost_tasks.check_budgets(s, deliver=deliver) == 0
            # A new month starts over.
            nxt = datetime(2099, 1, 15)
            assert {a.period for a in s.exec(select(CostBudgetAlert)).all()} == {
                datetime.utcnow().strftime("%Y-%m")}
            assert cost_tasks.check_budgets(s, now=nxt, deliver=deliver) == 0   # no usage in 2099
    finally:
        engine.dispose()
    assert [(t, c) for _, t, _, c in sent] == [(80, "EUR"), (100, "EUR")]
    assert sent[0][2] == 3.0 and sent[1][2] == 6.0


async def test_budget_dedupe_and_highest_threshold_only(session, scene, monkeypatch):
    await _usage(session, scene)
    session.add(ProjectCostBudget(project_id=scene.project_id, monthly_amount=2.0))
    await session.commit()
    from app.tasks import cost_tasks
    prices = cost_svc.Prices(currency="EUR", models={"m": (Decimal(3), Decimal(0))})
    monkeypatch.setattr(cost_svc, "load_prices", lambda get=None: prices)
    sent = []
    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(engine) as s:
            # 3.00 of 2.00: both thresholds recorded, one message (100%).
            assert cost_tasks.check_budgets(s, deliver=lambda *a: sent.append(a[2])) == 2
            assert cost_tasks.check_budgets(s, deliver=lambda *a: sent.append(a[2])) == 0
            rows = s.exec(select(CostBudgetAlert.threshold).where(
                CostBudgetAlert.project_id == scene.project_id)).all()
    finally:
        engine.dispose()
    assert sent == [100]
    assert sorted(rows) == [80, 100]
