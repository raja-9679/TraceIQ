"""WP16b against a real Postgres: OpenAPI spec history + diff + gate check,
and test ownership (validation, review list, owner notification dedupe).

    TRACEIQ_SCRATCH_DB=traceiq_wp16b ./run-tests-live.sh tests/integration/test_spec_history_ownership_db.py -q
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import developer, case_ownership as own_api         # noqa: E402
from app.api.endpoints import quality                            # noqa: E402
from app.core.auth import AuthPrincipal                          # noqa: E402
from app.core.config import db_url_for, settings                 # noqa: E402
from app.models import (                                         # noqa: E402
    ApiKey, AuditLog, CaseOwnerNotice, Project, ProjectApiSpecVersion, ProjectNotificationPref,
    TestCase, TestCaseResult, TestRun, TestStatus, TestSuite, User, UserProjectAccess, UserWorkspace,
)
from app.services import spec_versions                           # noqa: E402

from tests.integration.test_targeted_runs_db import (            # noqa: E402,F401
    no_queue, scene, session,
)


def _me(scene, api_key=None):
    return AuthPrincipal(user=scene.user, api_key=api_key)


PET = {"type": "object", "required": ["id", "name"],
       "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}


def _spec(pet=PET, title="Shop"):
    return {"openapi": "3.0.3", "info": {"title": title, "version": "1"},
            "paths": {"/pets/{id}": {"get": {"operationId": "getPet", "responses": {
                "200": {"description": "ok", "content": {"application/json": {"schema": pet}}}}}}}}


async def _put(session, scene, spec):
    return await developer.put_openapi_spec(scene.project_id, developer.ApiSpecUpdate(spec=spec),
                                            principal=_me(scene), session=session)


async def _person(session, email, project_id=None, workspace_id=None, level="viewer"):
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if workspace_id:
        session.add(UserWorkspace(user_id=u.id, workspace_id=workspace_id, role="member"))
    if project_id:
        session.add(UserProjectAccess(user_id=u.id, project_id=project_id, access_level=level))
    await session.commit()
    return u


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------

async def test_each_store_is_a_numbered_version_and_identical_uploads_are_not(session, scene):
    a = await _put(session, scene, _spec())
    b = await _put(session, scene, copy.deepcopy(_spec()))
    assert a["history_version"] == b["history_version"] == 1
    pet2 = copy.deepcopy(PET)
    del pet2["properties"]["name"]
    c = await _put(session, scene, _spec(pet2))
    assert c["history_version"] == 2
    listed = await developer.list_openapi_spec_versions(scene.project_id, principal=_me(scene), session=session)
    assert [v["version"] for v in listed["versions"]] == [2, 1]
    assert [v["is_current"] for v in listed["versions"]] == [True, False]
    assert listed["versions"][0]["sha256"] == spec_versions.sha256_of(_spec(pet2))
    audit = (await session.exec(select(AuditLog.changes).where(AuditLog.action == "openapi_spec_set")
                                .order_by(AuditLog.id.desc()))).first()
    assert audit["history_version"] == 2


async def test_diff_classifies_and_names_affected_tests(session, scene):
    await _put(session, scene, _spec())
    pet2 = copy.deepcopy(PET)
    del pet2["properties"]["name"]
    pet2["properties"]["age"] = {"type": "integer"}
    await _put(session, scene, _spec(pet2))
    case = await session.get(TestCase, scene.root_cases[0])
    case.steps = [{"type": "http-request", "params": {"expect_schema": {"openapi": {"operationId": "getPet"}}}}]
    foreign = await session.get(TestCase, scene.foreign_case)   # another project, same operationId
    foreign.steps = copy.deepcopy(case.steps)
    session.add_all([case, foreign])
    await session.commit()

    out = await developer.diff_openapi_spec_versions(scene.project_id, principal=_me(scene), session=session)
    assert (out["from_version"], out["to_version"]) == (1, 2)
    assert out["breaking_count"] == 1 and out["non_breaking_count"] == 1
    assert out["changes"][0]["rule"] == "response_property_removed"
    assert [t["case_id"] for t in out["affected_tests"]] == [case.id]   # never the other project's case

    back = await developer.diff_openapi_spec_versions(scene.project_id, from_=2, to=1,
                                                      principal=_me(scene), session=session)
    assert back["breaking_count"] == 1   # backwards: "age" removed

    sarif = await developer.diff_openapi_spec_versions_sarif(scene.project_id, principal=_me(scene),
                                                             session=session)
    assert sarif.media_type == "application/sarif+json"


async def test_diff_needs_two_versions_and_versions_are_project_scoped(session, scene):
    await _put(session, scene, _spec())
    with pytest.raises(HTTPException) as exc:
        await developer.diff_openapi_spec_versions(scene.project_id, principal=_me(scene), session=session)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await developer.get_openapi_spec_version(scene.other_project_id, 1, principal=_me(scene), session=session)
    assert exc.value.status_code == 404


async def test_outsider_and_wrongly_scoped_key_cannot_read_history(session, scene):
    await _put(session, scene, _spec())
    project = await session.get(Project, scene.project_id)
    outsider = await _person(session, "out@else.example.com")
    with pytest.raises(HTTPException) as exc:
        await developer.list_openapi_spec_versions(scene.project_id, principal=AuthPrincipal(user=outsider),
                                                   session=session)
    assert exc.value.status_code == 403
    key = ApiKey(workspace_id=project.workspace_id, project_id=scene.other_project_id, name="k",
                 hashed_key="h" * 64, prefix="tiq_x", created_by_id=scene.user.id)
    session.add(key)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await developer.diff_openapi_spec_versions(scene.project_id, principal=_me(scene, key), session=session)
    assert exc.value.status_code == 403


async def test_history_is_capped_but_keeps_the_pinned_baseline(session, scene, monkeypatch):
    monkeypatch.setattr(spec_versions, "MAX_SPEC_VERSIONS", 3)
    project = await session.get(Project, scene.project_id)
    project.quality_gate_policy = {"api_baseline": "pinned", "api_baseline_version": 1}
    session.add(project)
    await session.commit()
    for i in range(6):
        await _put(session, scene, _spec(title=f"v{i}"))
    kept = (await session.exec(select(ProjectApiSpecVersion.version)
                               .where(ProjectApiSpecVersion.project_id == scene.project_id))).all()
    assert sorted(kept) == [1, 4, 5, 6]


async def _gate(session, scene, policy):
    project = await session.get(Project, scene.project_id)
    project.quality_gate_policy = policy
    session.add(project)
    run = TestRun(project_id=scene.project_id, test_suite_id=scene.root_id, status=TestStatus.PASSED)
    session.add(run)
    await session.commit()
    res = await quality.evaluate_gate_for_runs(session, project, [run])
    return next(c for c in res.checks if c.name == "api_breaking_changes")


async def test_gate_check_previous_and_pinned_baselines(session, scene):
    check = await _gate(session, scene, {"require_no_breaking_api_changes": True})
    assert check.missing_data and not check.passed
    await _put(session, scene, _spec())
    pet2 = copy.deepcopy(PET)
    pet2["properties"]["age"] = {"type": "integer"}
    await _put(session, scene, _spec(pet2))
    check = await _gate(session, scene, {"require_no_breaking_api_changes": True})
    assert check.passed and not check.missing_data
    pet3 = copy.deepcopy(pet2)
    del pet3["properties"]["id"]
    await _put(session, scene, _spec(pet3))
    check = await _gate(session, scene, {"require_no_breaking_api_changes": True})
    assert not check.passed and "v2 → v3" in check.actual
    pinned = await _gate(session, scene, {"require_no_breaking_api_changes": True,
                                          "api_baseline": "pinned", "api_baseline_version": 1})
    assert not pinned.passed and "v1 → v3" in pinned.actual
    missing = await _gate(session, scene, {"require_no_breaking_api_changes": True,
                                           "api_baseline": "pinned", "api_baseline_version": 99})
    assert missing.missing_data


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------

async def test_owner_must_have_project_access_and_differ_from_backup(session, scene):
    project = await session.get(Project, scene.project_id)
    viewer = await _person(session, "viewer@corp.example.com", scene.project_id, project.workspace_id)
    member = await _person(session, "member@corp.example.com", None, project.workspace_id)
    case_id = scene.root_cases[0]
    with pytest.raises(HTTPException) as exc:
        await own_api.set_ownership(case_id, own_api.OwnershipUpdate(owner_user_id=member.id),
                                    principal=_me(scene), session=session)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        await own_api.set_ownership(case_id, own_api.OwnershipUpdate(owner_user_id=viewer.id,
                                                                     backup_owner_user_id=viewer.id),
                                    principal=_me(scene), session=session)
    out = await own_api.set_ownership(case_id, own_api.OwnershipUpdate(owner_user_id=viewer.id,
                                                                       backup_owner_user_id=scene.user.id),
                                      principal=_me(scene), session=session)
    assert out["owner"]["id"] == viewer.id and out["owner"]["state"] == "ok"
    assert (await session.exec(select(AuditLog).where(AuditLog.action == "ownership_set",
                                                      AuditLog.entity_id == case_id))).first()
    # A viewer can't assign owners.
    with pytest.raises(HTTPException) as exc:
        await own_api.set_ownership(case_id, own_api.OwnershipUpdate(owner_user_id=None),
                                    principal=AuthPrincipal(user=viewer), session=session)
    assert exc.value.status_code == 403

    # Access lost: the owner stays, flagged.
    for row in (await session.exec(select(UserProjectAccess).where(UserProjectAccess.user_id == viewer.id))).all():
        await session.delete(row)
    await session.commit()
    got = await own_api.get_ownership(case_id, principal=_me(scene), session=session)
    assert got["owner"]["id"] == viewer.id and got["owner"]["state"] == "no_access"
    lst = await own_api.test_review_list(scene.project_id, status="all", owner="no_access",
                                         principal=_me(scene), session=session)
    assert [r["case_id"] for r in lst["items"]] == [case_id]


async def test_review_list_interval_inheritance_and_mark_reviewed(session, scene):
    root = await session.get(TestSuite, scene.root_id)
    root.settings = {**(root.settings or {}), "review_interval_days": 30}
    sibling = await session.get(TestSuite, scene.sibling_id)
    sibling.settings = {"review_interval_days": 0}
    old = await session.get(TestCase, scene.child_cases[0])          # child inherits 30
    old.last_reviewed_at = datetime.utcnow() - timedelta(days=31)
    fresh = await session.get(TestCase, scene.child_cases[1])
    fresh.last_reviewed_at = datetime.utcnow() - timedelta(days=2)
    session.add_all([root, sibling, old, fresh])
    await session.commit()

    out = await own_api.test_review_list(scene.project_id, status="needs_review", owner=None,
                                         principal=_me(scene), session=session)
    ids = [r["case_id"] for r in out["items"]]
    assert ids[0] == old.id                                   # overdue before never-reviewed
    assert set(ids) == {old.id, *scene.root_cases}            # sibling: not required; fresh: ok
    assert out["counts"]["not_required"] == 1 and out["counts"]["ok"] == 1
    assert all(r["review_interval_days"] == 30 for r in out["items"])
    assert scene.foreign_case not in ids

    with pytest.raises(HTTPException) as exc:
        await own_api.mark_reviewed(old.id, principal=_me(scene, ApiKey(id=1, workspace_id=1, name="k",
                                                                        hashed_key="x", prefix="p",
                                                                        created_by_id=scene.user.id)),
                                    session=session)
    assert exc.value.status_code == 403
    done = await own_api.mark_reviewed(old.id, principal=_me(scene), session=session)
    assert done["review_status"] == "ok" and done["last_reviewed_by"]["id"] == scene.user.id
    assert (await session.exec(select(AuditLog).where(AuditLog.action == "reviewed",
                                                      AuditLog.entity_id == old.id))).first()


def _sync_session():
    return Session(create_engine(db_url_for(settings.DATABASE_URL, sync=True)))


async def _run(session, scene, status, case_id, at, error_kind=None):
    run = TestRun(project_id=scene.project_id, test_suite_id=scene.root_id, status=status, created_at=at)
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name="cart", status=status, duration_ms=1,
                               test_case_id=case_id, error_kind=error_kind))
    await session.commit()
    return run


async def test_owner_is_notified_once_per_pass_to_fail_transition(session, scene, monkeypatch):
    from app.tasks import notification_tasks as nt
    monkeypatch.setattr(nt, "get_notification_settings",
                        lambda project, s: {"enabled": True, "email_enabled": True})
    project = await session.get(Project, scene.project_id)
    project.notification_settings = {"notify_case_owners": True}
    case = await session.get(TestCase, scene.root_cases[0])
    case.owner_user_id = scene.user.id
    session.add_all([project, case])
    await session.commit()
    t0 = datetime.utcnow() - timedelta(hours=5)
    await _run(session, scene, TestStatus.PASSED, case.id, t0)
    fail1 = await _run(session, scene, TestStatus.FAILED, case.id, t0 + timedelta(hours=1))
    fail2 = await _run(session, scene, TestStatus.FAILED, case.id, t0 + timedelta(hours=2))

    sent = []
    send = lambda to, subject, html, text: sent.append((to, subject))  # noqa: E731
    with _sync_session() as s:
        assert nt.owner_notices_for_run(s, fail1.id, send=send) == 1
        assert nt.owner_notices_for_run(s, fail1.id, send=send) == 0       # retried task
        assert nt.owner_notices_for_run(s, fail2.id, send=send) == 0       # still failing
    assert sent == [(scene.user.email, "[TraceIQ] Your test started failing: cart")]

    # Concurrent failing run that also saw the same pass: deduped by key.
    with _sync_session() as s:
        fail_b = TestRun(project_id=scene.project_id, test_suite_id=scene.root_id, status=TestStatus.FAILED,
                         created_at=t0 + timedelta(minutes=30))
        s.add(fail_b)
        s.flush()
        s.add(TestCaseResult(test_run_id=fail_b.id, test_name="cart", status=TestStatus.FAILED,
                             duration_ms=1, test_case_id=case.id))
        s.commit()
        assert nt.owner_notices_for_run(s, fail_b.id, send=send) == 0
    assert len(sent) == 1

    # Pass again, then an infrastructure error: not a test failure.
    await _run(session, scene, TestStatus.PASSED, case.id, t0 + timedelta(hours=3))
    infra = await _run(session, scene, TestStatus.ERROR, case.id, t0 + timedelta(hours=4),
                       error_kind="infrastructure")
    with _sync_session() as s:
        assert nt.owner_notices_for_run(s, infra.id, send=send) == 0
    assert (await session.exec(select(CaseOwnerNotice))).all().__len__() == 1


async def test_owner_notice_respects_opt_in_access_and_mute(session, scene, monkeypatch):
    from app.tasks import notification_tasks as nt
    monkeypatch.setattr(nt, "get_notification_settings",
                        lambda project, s: {"enabled": True, "email_enabled": True})
    project = await session.get(Project, scene.project_id)
    gone = await _person(session, "gone@corp.example.com")             # no access to the project
    case = await session.get(TestCase, scene.root_cases[0])
    case.owner_user_id, case.backup_owner_user_id = gone.id, scene.user.id
    session.add(case)
    await session.commit()
    t0 = datetime.utcnow() - timedelta(hours=3)
    await _run(session, scene, TestStatus.PASSED, case.id, t0)
    fail = await _run(session, scene, TestStatus.FAILED, case.id, t0 + timedelta(hours=1))
    sent = []
    send = lambda to, *a: sent.append(to)  # noqa: E731
    with _sync_session() as s:
        assert nt.owner_notices_for_run(s, fail.id, send=send) == 0       # project not opted in
    project.notification_settings = {"notify_case_owners": True}
    session.add(ProjectNotificationPref(user_id=scene.user.id, project_id=project.id, mode="off"))
    session.add(project)
    await session.commit()
    with _sync_session() as s:
        assert nt.owner_notices_for_run(s, fail.id, send=send) == 0       # owner no access, backup muted
    pref = (await session.exec(select(ProjectNotificationPref))).first()
    await session.delete(pref)
    await session.commit()
    with _sync_session() as s:
        assert nt.owner_notices_for_run(s, fail.id, send=send) == 1       # falls to the backup
    assert sent == [scene.user.email]
