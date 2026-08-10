"""Right-to-erasure against a real Postgres — workstream G3.

`DELETE /api/auth/me` scrubbed the `users` row and revoked refresh tokens.
Everything else the person left behind survived: pending account tokens, MFA
recovery codes and secret, notification settings, and any API key they had
minted — a live credential belonging to someone who no longer has an account.

What this pins is the *boundary*, in both directions. Personal data must go, and
the workspace's own test history must NOT go: deleting a customer's suites
because an employee exercised erasure would be a far worse bug than the one
being fixed.

    ./run-tests-live.sh tests/integration/test_erasure_db.py -q
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

from app.core.config import db_url_for, settings          # noqa: E402
from app.models import (                                   # noqa: E402
    AccountToken, ApiKey, AuditLog, MfaRecoveryCode, Project, RefreshToken,
    Tenant, TestCase, TestSuite, User, UserSettings, Workspace,
)
from app.services.erasure import erase_user               # noqa: E402

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


class Scene(NamedTuple):
    user_id: int
    case_id: int
    suite_id: int
    api_key_id: int


async def _populate(session) -> Scene:
    user = User(email="dana@corp.example.com", full_name="Dana Real",
                hashed_password="x", is_verified=True,
                mfa_enabled=True, mfa_secret="encrypted-secret",
                scim_external_id="okta-1")
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    project = Project(name="Web", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    suite = TestSuite(name="Checkout", project_id=project.id,
                      created_by_id=user.id, updated_by_id=user.id)
    session.add(suite)
    await session.flush()
    case = TestCase(name="pay", test_suite_id=suite.id, project_id=project.id,
                    steps=[], created_by_id=user.id)
    session.add(case)
    await session.flush()
    key = ApiKey(workspace_id=ws.id, name="dana-ci", prefix="tiq_dn",
                 hashed_key="h", created_by_id=user.id)
    session.add(key)
    await session.flush()

    session.add_all([
        AccountToken(user_id=user.id, purpose="password_reset", hashed_token="t",
                     expires_at=datetime.utcnow() + timedelta(hours=1)),
        MfaRecoveryCode(user_id=user.id, code_hash="c"),
        UserSettings(user_id=user.id),
        RefreshToken(user_id=user.id, hashed_token="r", family_id="f",
                     expires_at=datetime.utcnow() + timedelta(days=30)),
    ])
    await session.flush()
    await session.commit()
    return Scene(user_id=user.id, case_id=case.id, suite_id=suite.id,
                 api_key_id=key.id)


async def _resync(session):
    await session.rollback()
    session.expire_all()


async def _count(session, table, column, value) -> int:
    result = await session.exec(
        text(f"SELECT count(*) FROM {table} WHERE {column} = :v"),
        params={"v": value})
    return int(result.one()[0])


# --- The account row ----------------------------------------------------------

async def test_the_row_no_longer_identifies_anyone(session):
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    erased = await session.get(User, scene.user_id)
    assert "dana" not in erased.email
    assert erased.full_name == "Deleted User"
    assert erased.is_active is False


async def test_mfa_material_is_destroyed(session):
    # A retained TOTP secret is a live second factor for an account that is
    # supposed to be gone.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    erased = await session.get(User, scene.user_id)
    assert erased.mfa_secret is None
    assert erased.mfa_enabled is False


async def test_the_scim_link_is_cleared(session):
    # Otherwise the next directory sync recognises the externalId and resurrects
    # the account.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)
    assert (await session.get(User, scene.user_id)).scim_external_id is None


# --- Personal rows ------------------------------------------------------------

@pytest.mark.parametrize("table", [
    "accounttoken", "mfarecoverycode", "user_settings", "refreshtoken",
])
async def test_personal_rows_are_deleted(session, table):
    scene = await _populate(session)
    assert await _count(session, table, "user_id", scene.user_id) > 0

    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    assert await _count(session, table, "user_id", scene.user_id) == 0


async def test_the_report_names_what_it_erased(session):
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    report = await erase_user(session, user)
    await session.commit()

    assert report.erased.get("accounttoken") == 1
    assert report.erased.get("mfarecoverycode") == 1
    assert report.erased.get("refreshtoken") == 1


# --- API keys -----------------------------------------------------------------

async def test_api_keys_the_person_minted_are_revoked(session):
    # A live credential belonging to a deleted human is a standing liability:
    # nobody is left to rotate it.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    report = await erase_user(session, user)
    await session.commit()
    await _resync(session)

    assert report.api_keys_revoked == 1
    key = await session.get(ApiKey, scene.api_key_id)
    assert key is not None and key.revoked_at is not None


async def test_api_keys_are_revoked_not_deleted(session):
    # Audit rows reference the key prefix; deleting the row would make that
    # history unreadable.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)
    assert await session.get(ApiKey, scene.api_key_id) is not None


# --- What must NOT be erased --------------------------------------------------

async def test_the_workspaces_test_records_survive(session):
    # The bug that would be worse than the one being fixed: erasing an
    # employee's account must not delete their employer's test suite.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    assert await session.get(TestSuite, scene.suite_id) is not None
    assert await session.get(TestCase, scene.case_id) is not None


async def test_authorship_still_points_at_the_scrubbed_row(session):
    # Retained de-identified: the id survives so "who last edited this" still
    # works, and the row it points at no longer names anybody.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    case = await session.get(TestCase, scene.case_id)
    assert case.created_by_id == scene.user_id


async def test_the_audit_trail_survives(session):
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    rows = (await session.exec(
        select(AuditLog).where(AuditLog.entity_type == "user",
                               AuditLog.action == "erase"))).all()
    assert len(rows) == 1


async def test_the_erasure_record_does_not_contain_the_email(session):
    # The audit row is permanent and cannot be rewritten. Writing the address
    # into the record of its own erasure would defeat the exercise.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    row = (await session.exec(
        select(AuditLog).where(AuditLog.action == "erase"))).first()
    assert "dana@corp.example.com" not in str(row.changes)


async def test_the_report_states_what_is_retained_and_why(session):
    # What a data-protection officer needs is a defensible statement of scope.
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    report = await erase_user(session, user)

    assert "auditlog" in report.retained_with_reason
    assert "legal-obligation" in report.retained_with_reason["auditlog"]
    assert any(col.startswith("testcase.") for col in report.retained_authorship)


# --- Idempotency --------------------------------------------------------------

async def test_erasing_twice_is_harmless(session):
    scene = await _populate(session)
    user = await session.get(User, scene.user_id)
    await erase_user(session, user)
    await session.commit()
    await _resync(session)

    user = await session.get(User, scene.user_id)
    second = await erase_user(session, user)
    await session.commit()
    assert second.erased == {}
