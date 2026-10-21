"""WP16d value report against a real Postgres: access (project viewer, scoped
API keys, workspace roll-up over readable projects only), validation-run
exclusion, one vote per user per hypothesis, the triage timestamp.

    TRACEIQ_SCRATCH_DB=traceiq_wp16d ./run-tests-live.sh tests/integration/test_value_report_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import triage as triage_api, value_report as api   # noqa: E402
from app.core.auth import AuthPrincipal                          # noqa: E402
from app.models import (                                         # noqa: E402
    ApiKey, AuditLog, FailureCluster, FailureClusterUpdate, HypothesisFeedback, Project,
    TestCaseResult, TestRun, TestStatus, User, UserProjectAccess, UserWorkspace,
)
from app.services import value_report as vr                      # noqa: E402

from tests.integration.test_targeted_runs_db import (            # noqa: E402,F401
    no_queue, scene, session,
)
from tests.integration.denied import DENIED                      # noqa: E402

HYP = {"category": "app_bug", "summary": "Checkout API returned 500", "source": "heuristic",
       "confidence": 0.7, "evidence_refs": ["s1"]}


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _member(session, scene, email, level, project_id=None) -> AuthPrincipal:
    u = User(email=email, full_name=email, hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    session.add(UserWorkspace(user_id=u.id, workspace_id=ws_id, role="member"))
    session.add(UserProjectAccess(user_id=u.id, project_id=project_id or scene.project_id,
                                  access_level=level))
    await session.commit()
    return AuthPrincipal(user=u, api_key=None)


async def _analysed_run(session, scene, *, project_id=None, validation=False, suite_id=None):
    now = datetime.utcnow()
    run = TestRun(test_suite_id=suite_id or scene.root_id, project_id=project_id or scene.project_id,
                  status=TestStatus.FAILED, created_at=now - timedelta(hours=1),
                  is_validation=validation, git_commit="aaa111")
    session.add(run)
    await session.flush()
    res = TestCaseResult(test_run_id=run.id, test_name="checkout", status=TestStatus.FAILED,
                         duration_ms=10, test_case_id=scene.root_cases[0])
    session.add(res)
    await session.flush()
    run.ai_analysis = {"schema_version": 1, "reports": [
        {"schema_version": 2, "result_id": res.id, "hypotheses": [
            HYP, {"category": "flake", "summary": "timing", "source": "heuristic"}]}]}
    session.add(run)
    await session.commit()
    return run.id, res.id


def _vote(index=0, verdict="correct", key=None):
    return api.HypothesisVoteIn(index=index, hypothesis_key=key or vr.hypothesis_key(HYP), verdict=verdict)


async def _report(session, scene, principal, project_id=None):
    return await api.project_value_report(project_id or scene.project_id, start=None, end=None,
                                          session=session, principal=principal)


def _metric(report, key):
    return next(m for m in report["metrics"] if m["key"] == key)


# --- votes -----------------------------------------------------------------------

async def test_one_vote_per_user_per_hypothesis_changeable_and_audited(session, scene):
    run_id, res_id = await _analysed_run(session, scene)
    me = _me(scene)
    user_id = scene.user.id
    first = await api.rate_hypothesis(run_id, res_id, _vote(), session=session, principal=me)
    again = await api.rate_hypothesis(run_id, res_id, _vote(), session=session, principal=me)
    changed = await api.rate_hypothesis(run_id, res_id, _vote(verdict="incorrect"),
                                        session=session, principal=me)
    assert first["id"] == again["id"] == changed["id"] and changed["previous"] == "correct"
    n = (await session.exec(select(func.count()).select_from(HypothesisFeedback))).one()
    assert n == 1
    audits = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "hypothesis_feedback"))).all()
    assert audits == ["rate", "rate"]          # the no-op repeat is not audited

    listed = await api.run_hypothesis_feedback(run_id, session=session, principal=me)
    top = listed["results"][str(res_id)][0]
    assert (top["correct"], top["incorrect"], top["mine"]) == (0, 1, "incorrect")
    assert listed["can_rate"] is True

    # The database itself refuses a second row for the same person (last:
    # the rollback expires every loaded object).
    row = (await session.exec(select(HypothesisFeedback))).one()
    session.add(HypothesisFeedback(**{**row.model_dump(exclude={"id"}), "verdict": "correct"}))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    await api.clear_hypothesis_rating(run_id, res_id, index=0, hypothesis_key=vr.hypothesis_key(HYP),
                                      session=session, principal=AuthPrincipal(
                                          user=await session.get(User, user_id), api_key=None))
    assert (await session.exec(select(func.count()).select_from(HypothesisFeedback))).one() == 0


async def test_who_may_rate(session, scene):
    run_id, res_id = await _analysed_run(session, scene)
    viewer = await _member(session, scene, "vw@corp.example.com", "viewer")
    with pytest.raises(HTTPException) as exc:
        await api.rate_hypothesis(run_id, res_id, _vote(), session=session, principal=viewer)
    assert exc.value.status_code == 403
    listed = await api.run_hypothesis_feedback(run_id, session=session, principal=viewer)
    assert listed["can_rate"] is False

    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    key = ApiKey(name="agent", workspace_id=ws_id, prefix="tiq_a", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await api.rate_hypothesis(run_id, res_id, _vote(),
                                  session=session, principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code == 403

    # The analysis changed since it was shown: refused, not misattributed.
    with pytest.raises(HTTPException) as exc:
        await api.rate_hypothesis(run_id, res_id, _vote(key="0" * 32), session=session, principal=_me(scene))
    assert exc.value.status_code == 409
    # A result from another run.
    other_run, other_res = await _analysed_run(session, scene)
    with pytest.raises(HTTPException) as exc:
        await api.rate_hypothesis(run_id, other_res, _vote(), session=session, principal=_me(scene))
    assert exc.value.status_code == 404

    val_run, val_res = await _analysed_run(session, scene, validation=True)
    with pytest.raises(HTTPException) as exc:
        await api.rate_hypothesis(val_run, val_res, _vote(), session=session, principal=_me(scene))
    assert exc.value.status_code == 400

    outsider = AuthPrincipal(user=User(id=999999, email="x@y", full_name="x", hashed_password="x"))
    with pytest.raises(HTTPException) as exc:
        await api.run_hypothesis_feedback(run_id, session=session, principal=outsider)
    assert exc.value.status_code in DENIED


# --- report: access + roll-up ------------------------------------------------------

async def test_project_report_access(session, scene):
    viewer = await _member(session, scene, "vw@corp.example.com", "viewer")
    report = await _report(session, scene, viewer)
    assert [m["key"] for m in report["metrics"]][0] == "time_to_diagnosis"
    assert all(m["value"] is None and m["missing"] for m in report["metrics"])

    with pytest.raises(HTTPException) as exc:
        await _report(session, scene, viewer, project_id=scene.other_project_id)
    assert exc.value.status_code == 403
    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    key = ApiKey(name="ci", workspace_id=ws_id, project_id=scene.other_project_id,
                 prefix="tiq_s", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await _report(session, scene, AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code in DENIED


async def test_workspace_rollup_covers_only_readable_projects(session, scene):
    now = datetime.utcnow()
    for pid in (scene.project_id, scene.other_project_id):
        session.add(FailureCluster(project_id=pid, signature=f"sig{pid}", title="t", category="other",
                                   first_seen_at=now - timedelta(hours=3),
                                   first_triaged_at=now - timedelta(hours=1)))
    await session.commit()
    viewer = await _member(session, scene, "vw@corp.example.com", "viewer")
    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    report = await api.workspace_value_report(ws_id, start=None, end=None, project_ids=None,
                                              session=session, principal=viewer)
    assert [p["id"] for p in report["scope"]["projects"]] == [scene.project_id]
    assert _metric(report, "time_to_diagnosis")["sample_size"] == 1
    # Asking for a project the caller cannot read silently narrows to nothing.
    narrowed = await api.workspace_value_report(ws_id, start=None, end=None,
                                                project_ids=str(scene.other_project_id),
                                                session=session, principal=viewer)
    assert narrowed["scope"]["projects"] == []
    admin = await api.workspace_value_report(ws_id, start=None, end=None, project_ids=None,
                                             session=session, principal=_me(scene))
    assert _metric(admin, "time_to_diagnosis")["sample_size"] == 2


# --- validation runs never count --------------------------------------------------

async def test_validation_runs_are_excluded(session, scene):
    now = datetime.utcnow()
    # A cluster seen only in a validation run is not a failure of the product.
    val_run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.FAILED,
                      is_validation=True, created_at=now - timedelta(hours=2))
    session.add(val_run)
    await session.flush()
    c = FailureCluster(project_id=scene.project_id, signature="val", title="t", category="other",
                       first_seen_at=now - timedelta(hours=2), first_triaged_at=now)
    session.add(c)
    await session.flush()
    session.add(TestCaseResult(test_run_id=val_run.id, test_name="x", status=TestStatus.FAILED,
                               duration_ms=1, cluster_id=c.id, test_case_id=scene.root_cases[0]))
    # A targeted validation run and its "baseline" never make a saving.
    for i, mode in enumerate(("full", "targeted")):
        for case in scene.root_cases + scene.child_cases if mode == "full" else scene.root_cases[:1]:
            session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, test_case_id=case,
                                status=TestStatus.PASSED, is_validation=True, selection_mode=mode,
                                selection_total=4, duration_ms=60000, user_id=scene.user.id,
                                created_at=now - timedelta(hours=5 - i)))
    # A vote left on a validation run (rows from before the refusal) is ignored.
    session.add(HypothesisFeedback(project_id=scene.project_id, run_id=val_run.id, result_id=1,
                                   run_created_at=val_run.created_at, hypothesis_index=0,
                                   hypothesis_key="k" * 32, category="app_bug", verdict="correct",
                                   user_id=scene.user.id))
    await session.commit()
    report = await _report(session, scene, _me(scene))
    assert _metric(report, "time_to_diagnosis")["clusters_considered"] == 0
    assert _metric(report, "targeted_time_saved")["measured_inputs"]["targeted_runs"] == 0
    assert _metric(report, "analysis_accuracy")["sample_size"] == 0


async def test_counted_targeted_run_against_a_full_run(session, scene):
    now = datetime.utcnow()
    every = scene.root_cases + scene.child_cases
    for case in every:
        session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, test_case_id=case,
                            status=TestStatus.PASSED, selection_mode="full", selection_total=4,
                            duration_ms=60000, user_id=scene.user.id, git_commit="c1",
                            created_at=now - timedelta(hours=5)))
    session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.project_id,
                        test_case_id=every[0], status=TestStatus.FAILED, selection_mode="targeted",
                        selection_total=4, duration_ms=50000, user_id=scene.user.id, git_commit="c2",
                        created_at=now - timedelta(hours=1)))
    await session.commit()
    m = _metric(await _report(session, scene, _me(scene)), "targeted_time_saved")
    assert m["kind"] == "estimated"
    assert m["value"]["saved_browser_minutes"] == 3.0       # 4 min × (1 − 1/4)


# --- time to diagnosis end to end ------------------------------------------------

async def test_triage_stamps_first_triaged_at_once(session, scene):
    c = FailureCluster(project_id=scene.project_id, signature="s", title="t", category="other")
    session.add(c)
    await session.commit()
    await triage_api.update_cluster(c.id, FailureClusterUpdate(status="investigating"),
                                    session=session, current_user=scene.user)
    stamped = (await session.get(FailureCluster, c.id)).first_triaged_at
    assert stamped is not None
    await triage_api.update_cluster(c.id, FailureClusterUpdate(status="open"),
                                    session=session, current_user=scene.user)
    await triage_api.update_cluster(c.id, FailureClusterUpdate(status="resolved"),
                                    session=session, current_user=scene.user)
    assert (await session.get(FailureCluster, c.id)).first_triaged_at == stamped


async def test_fix_on_a_new_commit_diagnoses_a_cluster(session, scene):
    now = datetime.utcnow()
    run_id, res_id = await _analysed_run(session, scene)          # fails on aaa111
    c = FailureCluster(project_id=scene.project_id, signature="fx", title="t", category="other",
                       first_seen_at=now - timedelta(hours=1))
    session.add(c)
    await session.flush()
    res = await session.get(TestCaseResult, res_id)
    res.cluster_id = c.id
    session.add(res)
    for commit, hours in (("aaa111", 0.5), ("bbb222", 0.25)):
        r = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.PASSED,
                    git_commit=commit, created_at=now - timedelta(hours=hours))
        session.add(r)
        await session.flush()
        session.add(TestCaseResult(test_run_id=r.id, test_name="checkout", status=TestStatus.PASSED,
                                   duration_ms=1, test_case_id=scene.root_cases[0]))
    await session.commit()
    m = _metric(await _report(session, scene, _me(scene)), "time_to_diagnosis")
    assert m["value"]["by_signal"]["fixed"] == 1
    assert m["value"]["median_hours"] == pytest.approx(0.75, abs=0.01)
