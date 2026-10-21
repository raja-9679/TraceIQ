"""Accessibility register + HAR uploads (WP15d) against a real Postgres.

    TRACEIQ_SCRATCH_DB=traceiq_wp15d ./run-tests-live.sh tests/integration/test_accessibility_db.py -q
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from starlette.datastructures import UploadFile                 # noqa: E402

from app.api import accessibility as a11y_api, har_files as har_api  # noqa: E402
from app.api.endpoints.quality import evaluate_gate_for_runs     # noqa: E402
from app.core.auth import AuthPrincipal                          # noqa: E402
from app.core.config import settings                             # noqa: E402
from app.core.database import db_url_for                         # noqa: E402
from app.models import (                                         # noqa: E402
    AccessibilityCheck, AccessibilityViolation, AccessibilityWaiver, AuditLog, HarArchive,
    Project, TestRun, TestStatus, User,
)
from app.services import accessibility as a11y                   # noqa: E402
from app.services.entity_delete import cascade_delete            # noqa: E402

from tests.integration.test_targeted_runs_db import (            # noqa: E402,F401
    scene, session,
)
from tests.integration.denied import DENIED  # noqa: E402


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


def _raw(*violations, url="https://shop.example/cart?sid=1"):
    return [{"step_id": "s1", "url": url, "violations": [
        {"id": rule, "impact": impact, "node_count": 1,
         "help_url": f"https://dequeuniversity.com/rules/axe/{rule}",
         "targets": [{"selector": target, "nodes": 1}]}
        for rule, impact, target in violations]}]


async def _suite_of(session, case_id: int) -> int:
    from app.models import TestCase
    return (await session.get(TestCase, case_id)).test_suite_id


async def _run(session, scene, minutes_ago: int, case_id=None, project_id=None) -> TestRun:
    case_id = case_id or scene.root_cases[0]
    run = TestRun(project_id=project_id or scene.project_id,
                  test_suite_id=await _suite_of(session, case_id),
                  test_case_id=case_id or scene.root_cases[0], status=TestStatus.PASSED,
                  browser="chromium", created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _persist(run: TestRun, case_id: int, raw, name="cart"):
    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(engine) as s:
            fresh = s.get(TestRun, run.id)
            n = a11y.persist_results(s, fresh, case_id, name, raw)
            s.commit()
            return n
    finally:
        engine.dispose()


async def _gate(session, scene, runs):
    project = await session.get(Project, scene.project_id)
    project.quality_gate_policy = {"min_pass_rate": 0, "block_new_accessibility_violations": True}
    session.add(project)
    await session.commit()
    result = await evaluate_gate_for_runs(session, project, runs)
    return next(c for c in result.checks if c.name == "accessibility_new_violations")


async def test_ingestion_is_replace_per_run_and_case_and_never_page_text(session, scene):
    case = scene.root_cases[0]
    run = await _run(session, scene, 10)
    raw = _raw(("color-contrast", "serious", 'a[title="Jane Doe"]'), ("image-alt", "minor", "img.logo"))
    assert _persist(run, case, raw) == 2
    assert _persist(run, case, raw) == 2      # redelivery does not double count
    rows = (await session.exec(select(AccessibilityViolation).where(
        AccessibilityViolation.test_run_id == run.id))).all()
    assert len(rows) == 2
    assert "Jane" not in json.dumps([r.target for r in rows])
    assert {r.page_key for r in rows} == {"https://shop.example/cart"}
    checks = (await session.exec(select(AccessibilityCheck).where(
        AccessibilityCheck.test_run_id == run.id))).all()
    assert len(checks) == 1 and checks[0].violation_count == 2


async def test_gate_new_serious_waived_and_expired(session, scene):
    case = scene.root_cases[0]
    base = await _run(session, scene, 30)
    _persist(base, case, _raw(("color-contrast", "serious", "#a"), ("image-alt", "minor", "#i")))
    head = await _run(session, scene, 5)
    _persist(head, case, _raw(("color-contrast", "serious", "#a"), ("button-name", "critical", "#b"),
                              ("region", "moderate", "#r")))

    check = await _gate(session, scene, [head])
    assert not check.passed and check.actual == "1 new"   # only button-name: serious+, new
    assert "button-name" in check.detail

    out = await a11y_api.create_waiver(
        scene.project_id, a11y_api.WaiverCreate(rule_id="button-name", selector_pattern="#*",
                                                reason="vendor widget, fix in Q1", days=14),
        None, principal=_me(scene), session=session)
    assert out["state"] == "active" and out["owner"]["id"] == scene.user.id
    check = await _gate(session, scene, [head])
    assert check.passed and "1 waived" in check.detail

    # Expired waivers count again; nothing renews them.
    waiver = await session.get(AccessibilityWaiver, out["id"])
    waiver.expires_at = datetime.utcnow() - timedelta(minutes=1)
    session.add(waiver)
    await session.commit()
    assert not (await _gate(session, scene, [head])).passed

    renewed = await a11y_api.update_waiver(out["id"], a11y_api.WaiverUpdate(days=30), None,
                                           principal=_me(scene), session=session)
    assert renewed["state"] == "active"
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "accessibility_waiver", AuditLog.entity_id == out["id"]))).all()
    assert {"create", "renew"} <= set(actions)

    detail = await a11y_api.accessibility_run(scene.project_id, head.id,
                                              principal=_me(scene), session=session)
    flags = {v["rule_id"]: (v["new"], v["waived"]) for v in detail["violations"]}
    assert flags == {"color-contrast": (False, False), "button-name": (True, True),
                     "region": (True, False)}

    trend = await a11y_api.accessibility_trend(scene.project_id, days=30,
                                               principal=_me(scene), session=session)
    assert sum(d["new"] for d in trend["series"]) == 2      # button-name, region
    assert sum(d["fixed"] for d in trend["series"]) == 1    # image-alt
    assert trend["totals"]["waived"] == 1
    assert trend["totals"]["serious"] == 1                  # color-contrast still open


async def test_gate_missing_data_and_no_baseline(session, scene):
    bare = await _run(session, scene, 3)
    check = await _gate(session, scene, [bare])
    assert not check.passed and check.missing_data

    # A case's first scan has no baseline: serious violations count as new.
    first = await _run(session, scene, 2, case_id=scene.root_cases[1])
    _persist(first, scene.root_cases[1], _raw(("label", "serious", "#q")), name="address")
    assert (await _gate(session, scene, [first])).actual == "1 new"

    # A clean scan is evidence, not missing data.
    clean = await _run(session, scene, 1, case_id=scene.root_cases[1])
    _persist(clean, scene.root_cases[1], [{"step_id": "s1", "url": "https://shop.example/", "violations": []}],
             name="address")
    check = await _gate(session, scene, [clean])
    assert check.passed and not check.missing_data


async def test_access_and_validation(session, scene):
    outsider = User(email="out@else.example.com", full_name="Out", hashed_password="x")
    session.add(outsider)
    await session.commit()
    nobody = AuthPrincipal(user=outsider, api_key=None)
    with pytest.raises(HTTPException) as exc:
        await a11y_api.accessibility_trend(scene.project_id, days=30, principal=nobody, session=session)
    assert exc.value.status_code in DENIED
    with pytest.raises(HTTPException) as exc:
        await har_api.list_hars(scene.project_id, principal=nobody, session=session)
    assert exc.value.status_code in DENIED

    # Another project's run looks like a missing one.
    foreign = await _run(session, scene, 1, case_id=scene.foreign_case, project_id=scene.other_project_id)
    with pytest.raises(HTTPException) as exc:
        await a11y_api.accessibility_run(scene.project_id, foreign.id, principal=_me(scene), session=session)
    assert exc.value.status_code == 404

    with pytest.raises(ValidationError):
        a11y_api.WaiverCreate(rule_id="label", reason="x", days=91)
    with pytest.raises(HTTPException) as exc:
        await a11y_api.create_waiver(scene.project_id, a11y_api.WaiverCreate(rule_id="not a rule", reason="x"),
                                     None, principal=_me(scene), session=session)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await a11y_api.create_waiver(scene.project_id,
                                     a11y_api.WaiverCreate(rule_id="label", reason="x", owner_user_id=outsider.id),
                                     None, principal=_me(scene), session=session)
    assert exc.value.status_code == 422


async def test_run_delete_takes_the_register_rows(session, scene):
    run = await _run(session, scene, 1)
    _persist(run, scene.root_cases[0], _raw(("label", "serious", "#q")))
    report = await cascade_delete(session, "testrun", [run.id])
    await session.commit()
    assert report.rows.get("accessibilityviolation") == 1
    assert report.rows.get("accessibilitycheck") == 1


def _har_bytes():
    return json.dumps({"log": {"version": "1.2", "entries": [{
        "request": {"method": "GET", "url": "https://api.shop.example/me",
                    "headers": [{"name": "Authorization", "value": "Bearer live-token"}], "cookies": []},
        "response": {"status": 200, "headers": [], "cookies": [{"name": "sid", "value": "sess-9"}],
                     "content": {"mimeType": "application/json", "text": "{\"ok\":true}"}},
    }]}}).encode()


async def test_har_upload_redacts_and_worker_fetch_is_same_project(session, scene, monkeypatch):
    stored = {}
    monkeypatch.setattr(har_api.minio_client, "upload_fileobj",
                        lambda f, key, content_type=None: stored.__setitem__(key, f.read()))
    monkeypatch.setattr(har_api.minio_client, "get_object_bytes",
                        lambda key, max_bytes=None: stored[key])
    monkeypatch.setattr(har_api.minio_client, "delete_object", lambda key: stored.pop(key, None))

    upload = UploadFile(file=io.BytesIO(_har_bytes()), filename="login.har")
    out = await har_api.upload_har(scene.project_id, None, file=upload, name=None,
                                   principal=_me(scene), session=session)
    key = (await session.get(HarArchive, out["id"])).object_key
    assert key.startswith(f"har/{scene.project_id}/")
    assert b"live-token" not in stored[key] and b"sess-9" not in stored[key]
    assert out["hosts"] == ["api.shop.example"] and out["entry_count"] == 1

    bad = UploadFile(file=io.BytesIO(b"<html>not a har</html>"), filename="x.har")
    with pytest.raises(HTTPException) as exc:
        await har_api.upload_har(scene.project_id, None, file=bad, name=None,
                                 principal=_me(scene), session=session)
    assert exc.value.status_code == 400

    mine, theirs = scene.root_id, await _suite_of(session, scene.foreign_case)
    live = TestRun(project_id=scene.project_id, test_suite_id=mine, status=TestStatus.RUNNING, browser="chromium")
    other = TestRun(project_id=scene.other_project_id, test_suite_id=theirs, status=TestStatus.RUNNING,
                    browser="chromium")
    done = TestRun(project_id=scene.project_id, test_suite_id=mine, status=TestStatus.PASSED, browser="chromium")
    session.add_all([live, other, done])
    await session.commit()
    resp = await har_api.worker_fetch_har(out["id"], run_id=live.id, test_case_id=scene.root_cases[0],
                                          session=session)
    assert resp.body == stored[key]
    for run_id, case_id in ((other.id, scene.foreign_case),        # other project's run
                            (live.id, scene.foreign_case),         # case not in the run's project
                            (done.id, scene.root_cases[0])):       # run no longer live
        with pytest.raises(HTTPException) as exc:
            await har_api.worker_fetch_har(out["id"], run_id=run_id, test_case_id=case_id, session=session)
        assert exc.value.status_code == 404

    # Dispatch-time resolution only accepts this project's HARs.
    from app.services.har_files import resolve_steps, lookup_sync
    steps = [{"type": "har-replay", "params": {"har_id": out["id"]}}]
    assert resolve_steps(steps, scene.project_id, lookup_sync)[0]["params"]["har"]["id"] == out["id"]
    assert "har_error" in resolve_steps(steps, scene.other_project_id, lookup_sync)[0]["params"]

    # Project delete names the HAR prefix for object cleanup.
    report = await cascade_delete(session, "project", [scene.project_id])
    await session.rollback()
    assert f"har/{scene.project_id}/" in report.object_keys
    await session.refresh(scene.user)   # the rollback expired it

    await har_api.delete_har(out["id"], None, principal=_me(scene), session=session)
    assert key not in stored
    actions = (await session.exec(select(AuditLog.action).where(AuditLog.entity_type == "har_file"))).all()
    assert {"upload", "delete"} <= set(actions)
