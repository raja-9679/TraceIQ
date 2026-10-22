"""Release readiness + the case-level gate checks against a real Postgres.

    ./run-tests-live.sh tests/integration/test_release_readiness_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api.endpoints import quality                          # noqa: E402
from app.core.auth import AuthPrincipal                         # noqa: E402
from app.models import (                                        # noqa: E402
    ApiKey, AuditLog, FlakeRecord, Project, QualityGatePolicy, RequirementLink,
    TestCase, TestCaseResult, TestRun, TestStatus, User,
)

# Reuse the targeted-runs scene: one project, root+child suites, a foreign case.
from tests.integration.test_targeted_runs_db import (           # noqa: E402,F401
    no_queue, scene, session,
)
from tests.integration.denied import DENIED  # noqa: E402

P, F = TestStatus.PASSED, TestStatus.FAILED


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _run(session, scene, commit, results, *, project_id=None, browser="chromium",
               at=None, branch="main"):
    """One finished run on `commit` with {case_id: status} results."""
    status = P if all(s == P for s in results.values()) else F
    run = TestRun(test_suite_id=scene.root_id, project_id=project_id or scene.project_id,
                  git_commit=commit, git_branch=branch, status=status, browser=browser,
                  total_tests=len(results), created_at=at or datetime.utcnow(),
                  finalized_at=at or datetime.utcnow(), selection_mode="full")
    session.add(run)
    await session.flush()
    for case_id, st in results.items():
        case = await session.get(TestCase, case_id)
        session.add(TestCaseResult(test_run_id=run.id, test_name=case.name, status=st,
                                   duration_ms=1.0, test_case_id=case_id))
    await session.commit()
    return run


async def _critical(session, *case_ids):
    for cid in case_ids:
        case = await session.get(TestCase, cid)
        case.priority = "Critical"   # stored free-form; matched case-insensitively
        session.add(case)
    await session.commit()


async def _policy(session, scene, **kw):
    project = await session.get(Project, scene.project_id)
    project.quality_gate_policy = QualityGatePolicy(**kw).model_dump()
    session.add(project)
    await session.commit()


async def _gate(session, scene, commit):
    return await quality.evaluate_quality_gate(
        scene.project_id, git_commit=commit, git_branch=None,
        session=session, principal=_me(scene))


async def _ready(session, scene, commit=None, branch=None, principal=None):
    return await quality.release_readiness_view(
        scene.project_id, git_commit=commit, git_branch=branch,
        session=session, principal=principal or _me(scene))


def _check(result, name):
    return next(c for c in result.checks if c.name == name)


async def test_existing_policy_gets_no_new_checks(session, scene):
    await _run(session, scene, "c1", {scene.root_cases[0]: P})
    gate = await _gate(session, scene, "c1")
    assert {c.name for c in gate.checks} == {"pass_rate", "coverage", "security_high_findings"}
    assert gate.passed


async def test_critical_passed_is_judged_per_commit(session, scene):
    cart, address = scene.root_cases
    await _critical(session, cart, address)
    await _policy(session, scene, require_critical_passed=True)
    # Commit A: both critical cases pass (across two runs on the same commit).
    await _run(session, scene, "aaa", {cart: P})
    await _run(session, scene, "aaa", {address: P})
    # Commit B: cart fails, address never ran. A's passes must not count.
    await _run(session, scene, "bbb", {cart: F, scene.child_cases[0]: P})

    a = _check(await _gate(session, scene, "aaa"), "critical_passed")
    assert a.passed and a.actual == "2 of 2 passed"

    b = _check(await _gate(session, scene, "bbb"), "critical_passed")
    assert not b.passed and not b.missing_data
    assert "failed: cart" in b.detail and "no result for commit bbb: address" in b.detail

    # A green rerun of cart on B leaves only the missing case: insufficient, not failed.
    await _run(session, scene, "bbb", {cart: P})
    b2 = _check(await _gate(session, scene, "bbb"), "critical_passed")
    assert not b2.passed and b2.missing_data and b2.actual == "1 of 2 passed"


async def test_another_projects_results_do_not_count(session, scene):
    cart = scene.root_cases[0]
    await _critical(session, cart)
    await _policy(session, scene, require_critical_passed=True)
    # A result for cart on the same commit but filed under another project.
    await _run(session, scene, "ccc", {cart: P}, project_id=scene.other_project_id)
    await _run(session, scene, "ccc", {scene.sibling_case: P})
    c = _check(await _gate(session, scene, "ccc"), "critical_passed")
    assert not c.passed and "cart" in c.detail


async def test_max_flaky_cases_uses_active_quarantine_or_score(session, scene):
    a, b = scene.root_cases
    c, d = scene.child_cases
    now = datetime.utcnow()
    session.add_all([
        FlakeRecord(test_case_id=a, flake_score=0.1, is_quarantined=True,
                    quarantine_expires_at=now + timedelta(days=3)),
        FlakeRecord(test_case_id=a, step_id="s2", flake_score=0.6),     # same case twice
        FlakeRecord(test_case_id=b, flake_score=0.5),
        FlakeRecord(test_case_id=c, flake_score=0.1, is_quarantined=True,
                    quarantine_expires_at=now - timedelta(days=1)),     # expired, low score
        FlakeRecord(test_case_id=d, flake_score=0.2),
        FlakeRecord(test_case_id=scene.foreign_case, flake_score=0.9),  # other project
    ])
    await session.commit()
    await _policy(session, scene, max_flaky_cases=1)
    await _run(session, scene, "f1", {a: P})
    chk = _check(await _gate(session, scene, "f1"), "flaky_cases")
    assert chk.actual == "2 flaky" and not chk.passed
    assert chk.detail == "address, cart"


async def test_evidence_age_falls_back_to_all_cases_without_critical(session, scene):
    old = datetime.utcnow() - timedelta(days=20)
    await _policy(session, scene, max_evidence_age_days=7)
    every = scene.root_cases + scene.child_cases + [scene.sibling_case]
    await _run(session, scene, "old", {cid: P for cid in every}, at=old)
    await _run(session, scene, "new", {cid: P for cid in every[:-1]})
    chk = _check(await _gate(session, scene, "new"), "evidence_age")
    assert "all cases" in chk.threshold
    assert not chk.passed and chk.detail.startswith("stale: query 20d")

    await _critical(session, scene.root_cases[0])
    chk = _check(await _gate(session, scene, "new"), "evidence_age")
    assert chk.passed and "critical cases" in chk.threshold


async def test_readiness_reports_evidence_and_verdict(session, scene):
    cart, address = scene.root_cases
    await _critical(session, cart, address)
    await _policy(session, scene, require_critical_passed=True, min_pass_rate=0)
    owner = scene.user
    session.add(FlakeRecord(test_case_id=scene.child_cases[0], flake_score=0.5,
                            is_quarantined=True, quarantine_source="manual",
                            quarantine_reason="races the mock", owner_user_id=owner.id,
                            quarantine_expires_at=datetime.utcnow() + timedelta(days=5)))
    session.add_all([
        RequirementLink(test_case_id=cart, project_id=scene.project_id, ref="REQ-1"),
        RequirementLink(test_case_id=address, project_id=scene.project_id, ref="REQ-1"),
        RequirementLink(test_case_id=scene.sibling_case, project_id=scene.project_id, ref="REQ-2"),
    ])
    await session.commit()
    await _run(session, scene, "aaa", {cart: P, address: P}, at=datetime.utcnow() - timedelta(hours=2))
    await _run(session, scene, "bbb", {cart: P})

    # Default: the newest run picks the commit — bbb, where address never ran.
    r = await _ready(session, scene)
    assert r.scope == "latest_run" and r.git_commit == "bbb"
    assert r.verdict == "insufficient_data"
    rows = {c.name: c for c in r.critical_cases}
    assert rows["cart"].status == "passed"
    assert rows["address"].status == "not_run"
    assert rows["address"].detail == "no run for this commit"
    assert rows["address"].last_commit == "aaa" and rows["address"].last_status == "passed"
    reqs = {q.ref: q for q in r.requirements}
    assert reqs["REQ-1"].status == "partial" and reqs["REQ-2"].status == "untested"
    [q] = r.quarantines
    assert q.case_name == "card" and q.owner.email == owner.email and q.source == "manual"

    assert [c.git_commit for c in r.recent_commits] == ["bbb", "aaa"]

    r = await _ready(session, scene, commit="aaa")
    assert r.verdict == "ready" and r.scope == "commit"
    assert {q.ref: q.status for q in r.requirements}["REQ-1"] == "passing"

    await _run(session, scene, "ccc", {cart: F, address: P})
    r = await _ready(session, scene, commit="ccc")
    assert r.verdict == "not_ready"
    assert any(x.startswith("Critical tests passed: ") for x in r.reasons)


async def test_readiness_for_unknown_commit_is_insufficient_not_pass(session, scene):
    await _run(session, scene, "aaa", {scene.root_cases[0]: P})
    r = await _ready(session, scene, commit="nope")
    assert r.verdict == "insufficient_data" and r.runs == []
    assert r.reasons[0] == "no finished run for commit nope"
    assert r.gate.passed is False


async def test_branch_resolves_to_its_newest_commit(session, scene):
    await _run(session, scene, "m1", {scene.root_cases[0]: P}, branch="main")
    await _run(session, scene, "f1", {scene.root_cases[0]: F}, branch="feature")
    r = await _ready(session, scene, branch="main")
    assert r.scope == "branch" and r.git_commit == "m1" and r.verdict == "ready"


async def test_access_is_enforced(session, scene):
    await _run(session, scene, "aaa", {scene.root_cases[0]: P})
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id,
                 prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await _ready(session, scene, principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code in DENIED
    with pytest.raises(HTTPException) as exc:
        await quality.evaluate_quality_gate(
            scene.project_id, git_commit=None, git_branch=None, session=session,
            principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code in DENIED

    # The project's real workspace: with RLS enforcing, a key is held to its
    # workspace, so a hard-coded 1 only worked while the scene happened to be there.
    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    own_key = ApiKey(name="ci", workspace_id=ws_id, project_id=scene.project_id,
                     prefix="tiq_y", hashed_key="h")
    r = await _ready(session, scene, principal=AuthPrincipal(user=scene.user, api_key=own_key))
    assert r.project_id == scene.project_id

    stranger = User(email="x@else.example.com", full_name="X", hashed_password="x")
    session.add(stranger)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _ready(session, scene, principal=AuthPrincipal(user=stranger, api_key=None))
    assert exc.value.status_code in DENIED


async def test_policy_edit_round_trips_and_is_audited(session, scene):
    body = QualityGatePolicy(max_flaky_cases=2, require_critical_passed=True,
                             max_evidence_age_days=14)
    await quality.set_quality_gate_policy(scene.project_id, body, session=session,
                                          current_user=scene.user)
    got = await quality.get_quality_gate_policy(scene.project_id, session=session,
                                                current_user=scene.user)
    assert (got.max_flaky_cases, got.require_critical_passed, got.max_evidence_age_days) == (2, True, 14)
    rows = (await session.exec(select(AuditLog).where(
        AuditLog.action == "quality_gate_policy_update"))).all()
    assert len(rows) == 1 and "max_flaky_cases" in rows[0].changes


async def test_default_gate_judges_the_same_commit_as_readiness(session, scene):
    """No commit/branch: both endpoints let the newest run pick the commit and
    judge every run on it. The gate used to judge the single newest run with
    no commit, so commit-scoped checks disagreed with readiness."""
    cart, address = scene.root_cases
    await _critical(session, cart, address)
    await _policy(session, scene, require_critical_passed=True, min_pass_rate=0)
    t = datetime.utcnow()
    await _run(session, scene, "aaa", {cart: P}, at=t - timedelta(hours=2))
    # Newest run of commit aaa covers only address; cart's pass is in the older run.
    await _run(session, scene, "aaa", {address: P}, at=t - timedelta(hours=1))

    gate = await quality.evaluate_quality_gate(
        scene.project_id, git_commit=None, git_branch=None, session=session, principal=_me(scene))
    ready = await _ready(session, scene)
    assert gate.git_commit == ready.git_commit == "aaa"
    assert sorted(gate.evaluated_run_ids) == sorted(ready.gate.evaluated_run_ids)
    assert _check(gate, "critical_passed").passed
    assert [(c.name, c.passed, c.actual) for c in gate.checks] == \
        [(c.name, c.passed, c.actual) for c in ready.gate.checks]


async def test_last_result_is_the_newest_by_time_not_by_id(session, scene):
    cart = scene.root_cases[0]
    await _critical(session, cart)
    t = datetime.utcnow()
    await _run(session, scene, "new", {cart: P}, at=t - timedelta(hours=1))
    # Written afterwards (higher ids) but two days older.
    await _run(session, scene, None, {cart: F}, at=t - timedelta(days=2))
    r = await _ready(session, scene, commit="new")
    [row] = [c for c in r.critical_cases if c.case_id == cart]
    assert row.last_status == "passed" and row.last_commit == "new"
    assert row.last_at >= row.last_passed_at
