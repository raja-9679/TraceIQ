"""Flake owners + quarantine expiry against a real Postgres.

    ./run-tests-live.sh tests/integration/test_flake_owners_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import analytics, flake_records                  # noqa: E402
from app.core.auth import AuthPrincipal                        # noqa: E402
from app.models import ApiKey, AuditLog, FlakeRecord, User      # noqa: E402
from app.services.flake_policy import active_quarantine_clause  # noqa: E402

# Reuse the targeted-runs scene: one project, root+child suites, a foreign case.
from tests.integration.test_targeted_runs_db import (           # noqa: E402,F401
    _create, no_queue, scene, session,
)


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _flake(session, case_id, **kw) -> FlakeRecord:
    rec = FlakeRecord(test_case_id=case_id, flake_score=0.5, sample_count=10, **kw)
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


async def test_expired_quarantine_runs_again(session, scene):
    active, expired = scene.root_cases
    past = datetime.utcnow() - timedelta(days=1)
    await _flake(session, active, is_quarantined=True,
                 quarantine_expires_at=datetime.utcnow() + timedelta(days=3))
    await _flake(session, expired, is_quarantined=True, quarantine_expires_at=past)

    runs = await _create(session, scene, case_ids=[active, expired], browser=["chromium"])
    assert [r.test_case_id for r in runs] == [expired]

    hidden = (await session.exec(
        select(FlakeRecord.test_case_id).where(active_quarantine_clause()))).all()
    assert set(hidden) == {active}


async def test_manual_quarantine_records_owner_reason_expiry_and_audit(session, scene):
    rec = await _flake(session, scene.root_cases[0])
    body = flake_records.QuarantineRequest(reason="races the payment mock", days=10)
    out = await flake_records.quarantine(rec.id, body, None, principal=_me(scene), session=session)

    assert out["quarantine_state"] == "active"
    assert out["quarantine_source"] == "manual"
    assert out["owner"]["id"] == scene.user.id          # defaults to the caller
    assert out["quarantined_by"]["id"] == scene.user.id
    days = (out["quarantine_expires_at"] - out["quarantined_at"]).days
    assert days == 10

    actions = (await session.exec(
        select(AuditLog.action).where(AuditLog.entity_type == "flake_record",
                                      AuditLog.entity_id == rec.id))).all()
    assert "quarantine" in actions


async def test_quarantine_requires_a_reason_and_a_bounded_length():
    with pytest.raises(ValidationError):
        flake_records.QuarantineRequest(reason="")
    with pytest.raises(ValidationError):
        flake_records.QuarantineRequest(reason="x", days=91)


async def test_release_clears_quarantine_but_keeps_owner(session, scene):
    rec = await _flake(session, scene.root_cases[0])
    await flake_records.quarantine(
        rec.id, flake_records.QuarantineRequest(reason="r"), None, principal=_me(scene), session=session)
    out = await flake_records.release(rec.id, None, principal=_me(scene), session=session)
    assert out["quarantine_state"] == "none"
    assert out["quarantine_expires_at"] is None
    assert out["owner"]["id"] == scene.user.id


async def test_owner_must_be_an_editor_on_the_project(session, scene):
    rec = await _flake(session, scene.root_cases[0])
    outsider = User(email="out@else.example.com", full_name="Out", hashed_password="x")
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await flake_records.set_owner(
            rec.id, flake_records.OwnerRequest(owner_user_id=outsider.id), None,
            principal=_me(scene), session=session)
    assert exc.value.status_code == 422

    out = await flake_records.set_owner(
        rec.id, flake_records.OwnerRequest(owner_user_id=scene.user.id), None,
        principal=_me(scene), session=session)
    assert out["owner"]["email"] == scene.user.email
    cleared = await flake_records.set_owner(
        rec.id, flake_records.OwnerRequest(owner_user_id=None), None,
        principal=_me(scene), session=session)
    assert cleared["owner"] is None


async def test_owner_candidates_are_project_editors(session, scene):
    people = await flake_records.owner_candidates(
        project_id=scene.project_id, principal=_me(scene), session=session)
    assert [p["id"] for p in people] == [scene.user.id]


async def test_flakiness_summary_carries_state_owner_and_expiry(session, scene):
    rec = await _flake(session, scene.root_cases[0], owner_user_id=scene.user.id,
                       is_quarantined=True, quarantine_source="auto",
                       quarantine_expires_at=datetime.utcnow() - timedelta(hours=1))
    rows = await analytics.project_flakiness(
        scene.project_id, days=30, session=session, current_user=scene.user)
    row = next(r for r in rows if r["test_case_id"] == scene.root_cases[0])
    assert row["flake_id"] == rec.id
    assert row["quarantine_state"] == "expired"
    assert row["owner"]["id"] == scene.user.id
    assert row["quarantine_source"] == "auto"


async def test_release_clears_step_level_rows_too(session, scene):
    case = scene.root_cases[0]
    rec = await _flake(session, case, is_quarantined=True)
    await _flake(session, case, step_id="s1", is_quarantined=True)
    await flake_records.release(rec.id, None, principal=_me(scene), session=session)
    left = (await session.exec(select(FlakeRecord).where(
        FlakeRecord.test_case_id == case, active_quarantine_clause()))).all()
    assert left == []


async def test_project_scoped_key_cannot_act_on_another_project(session, scene):
    rec = await _flake(session, scene.root_cases[0])
    # Never persisted: only its project scope matters here.
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id,
                 prefix="tiq_x", hashed_key="h")
    scoped = AuthPrincipal(user=scene.user, api_key=key)
    with pytest.raises(HTTPException) as exc:
        await flake_records.release(rec.id, None, principal=scoped, session=session)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        await flake_records.owner_candidates(
            project_id=scene.project_id, principal=scoped, session=session)


async def test_requarantine_replaces_an_owner_who_lost_access(session, scene):
    gone = User(email="gone@corp.example.com", full_name="Gone", hashed_password="x", is_active=False)
    session.add(gone)
    await session.commit()
    rec = await _flake(session, scene.root_cases[0], owner_user_id=gone.id)
    out = await flake_records.quarantine(
        rec.id, flake_records.QuarantineRequest(reason="r"), None, principal=_me(scene), session=session)
    assert out["owner"]["id"] == scene.user.id
