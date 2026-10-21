"""Schedule timezones against a real Postgres: create/update validate the zone
and compute next_run_at in it; the beat task advances in it.

    TRACEIQ_SCRATCH_DB=traceiq_sched_tz ./run-tests-live.sh tests/integration/test_schedule_timezone_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api.endpoints import schedules                               # noqa: E402
from app.models import TestSchedule, TestScheduleBase, TestScheduleUpdate  # noqa: E402
from app.services.schedule_time import next_run_utc                   # noqa: E402

from tests.integration.test_targeted_runs_db import (                 # noqa: E402,F401
    no_queue, scene, session,
)


def _base(scene, **kw):
    return TestScheduleBase(name="nightly", project_id=scene.project_id,
                            test_case_id=scene.root_cases[0], **kw)


async def test_create_defaults_to_utc(session, scene):
    sched = await schedules.create_schedule(_base(scene, cron_expression="0 9 * * 1"),
                                            session=session, current_user=scene.user)
    assert sched.timezone == "UTC"
    assert sched.next_run_at.hour == 9 and sched.next_run_at.minute == 0


async def test_create_reads_cron_in_the_given_zone(session, scene):
    sched = await schedules.create_schedule(
        _base(scene, cron_expression="0 9 * * 1", timezone="Asia/Kolkata"),
        session=session, current_user=scene.user)
    assert sched.timezone == "Asia/Kolkata"
    # 09:00 IST = 03:30 UTC, on a Monday.
    assert (sched.next_run_at.hour, sched.next_run_at.minute) == (3, 30)
    assert sched.next_run_at.weekday() == 0


async def test_create_refuses_an_unknown_zone(session, scene):
    with pytest.raises(HTTPException) as exc:
        await schedules.create_schedule(
            _base(scene, cron_expression="0 9 * * 1", timezone="Mars/Olympus"),
            session=session, current_user=scene.user)
    assert exc.value.status_code == 400


async def test_create_ignores_client_set_server_fields(session, scene):
    # TestScheduleBase is the request body, so these arrive as input; a client
    # must not be able to backdate a schedule or claim it already ran.
    sched = await schedules.create_schedule(
        _base(scene, cron_expression="0 9 * * 1",
              created_at=datetime(2001, 1, 1), updated_at=datetime(2001, 1, 1),
              last_run_at=datetime(2001, 1, 2)),
        session=session, current_user=scene.user)
    assert sched.last_run_at is None
    assert sched.created_at.year > 2001 and sched.updated_at.year > 2001
    assert sched.created_by_id == scene.user.id


async def test_changing_only_the_zone_recomputes_next_run(session, scene):
    sched = await schedules.create_schedule(_base(scene, cron_expression="0 9 * * *"),
                                            session=session, current_user=scene.user)
    updated = await schedules.update_schedule(
        sched.id, TestScheduleUpdate(timezone="Asia/Kolkata"),
        session=session, current_user=scene.user)
    assert updated.timezone == "Asia/Kolkata"
    assert (updated.next_run_at.hour, updated.next_run_at.minute) == (3, 30)

    with pytest.raises(HTTPException) as exc:
        await schedules.update_schedule(sched.id, TestScheduleUpdate(timezone="Nowhere/Land"),
                                        session=session, current_user=scene.user)
    assert exc.value.status_code == 400


async def test_beat_task_advances_in_the_schedule_zone(session, scene, monkeypatch):
    from app.tasks import schedule_tasks

    sched = TestSchedule(name="ist", project_id=scene.project_id,
                         test_case_id=scene.root_cases[0], cron_expression="0 9 * * *",
                         timezone="Asia/Kolkata", next_run_at=datetime(2000, 1, 1))
    session.add(sched)
    await session.commit()

    async def _no_dispatch(schedule, s):
        return None
    monkeypatch.setattr(schedule_tasks, "_dispatch_schedule", _no_dispatch)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield session
    monkeypatch.setattr(schedule_tasks, "get_session_context", _ctx)

    await schedule_tasks._process_schedules_async()
    await session.refresh(sched)
    assert (sched.next_run_at.hour, sched.next_run_at.minute) == (3, 30)
    assert sched.next_run_at == next_run_utc("0 9 * * *", "Asia/Kolkata",
                                             after=sched.last_run_at)
