"""User settings that now take effect, against a real Postgres: the run
owner's email preferences in the real recipient lookup, and the default
device at run creation.

    ./run-tests-live.sh tests/integration/test_user_settings_wiring_db.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlmodel import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal  # noqa: E402
from app.models import TestRun, TestStatus  # noqa: E402
from app.settings_models import UserSettings  # noqa: E402

from tests.integration.test_targeted_runs_db import (  # noqa: E402,F401
    no_queue, scene, session,
)


def _sync_session():
    from app.tasks.monitor_tasks import sync_engine
    return Session(sync_engine)


async def _run(session, scene, status):
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id,
                  user_id=scene.user.id, status=status)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _recipients(run_id, extra=None):
    from app.tasks.notification_tasks import get_email_recipients
    with _sync_session() as s:
        return sorted(get_email_recipients(s.get(TestRun, run_id), {"email_recipients": extra or []}, s))


async def test_owner_with_no_saved_settings_is_emailed(session, scene):
    run = await _run(session, scene, TestStatus.PASSED)
    assert _recipients(run.id) == [scene.user.email]


async def test_failures_only_and_custom_address(session, scene):
    session.add(UserSettings(user_id=scene.user.id, email_notifications=True,
                             notify_on_completion=False, notify_on_failure=True,
                             notification_email="alerts@corp.example.com"))
    await session.commit()
    passed = await _run(session, scene, TestStatus.PASSED)
    failed = await _run(session, scene, TestStatus.FAILED)
    assert _recipients(passed.id) == []
    assert _recipients(failed.id) == ["alerts@corp.example.com"]


async def test_opt_out_drops_only_the_owner(session, scene):
    session.add(UserSettings(user_id=scene.user.id, email_notifications=False))
    await session.commit()
    run = await _run(session, scene, TestStatus.FAILED)
    assert _recipients(run.id, extra=["team@corp.example.com"]) == ["team@corp.example.com"]


async def test_new_settings_rows_default_to_email_on(session, scene):
    row = UserSettings(user_id=scene.user.id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    assert row.email_notifications is True


async def _create_run(session, scene):
    from app.api.endpoints import test_runs
    return await test_runs.create_run(
        suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=None, session=session, principal=AuthPrincipal(user=scene.user, api_key=None))


async def test_default_device_is_used_when_nothing_else_picks_one(session, scene):
    session.add(UserSettings(user_id=scene.user.id, default_device="iPhone 13"))
    await session.commit()
    runs = await _create_run(session, scene)
    assert {r.device for r in runs} == {"iPhone 13"}


async def test_desktop_default_stays_desktop(session, scene):
    session.add(UserSettings(user_id=scene.user.id, default_device="Desktop"))
    await session.commit()
    runs = await _create_run(session, scene)
    assert {r.device for r in runs} in ({None}, {"Desktop"})
