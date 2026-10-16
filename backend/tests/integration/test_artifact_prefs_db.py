"""A user's saved Settings → video/trace/screenshot choices are what the
dispatcher attaches to their runs' jobs (real Postgres, sync dispatch session).

    ./run-tests-live.sh tests/integration/test_artifact_prefs_db.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlmodel import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.services.artifact_prefs import DEFAULT_PREFS, load_for_user  # noqa: E402
from app.settings_models import UserSettings  # noqa: E402

from tests.integration.test_targeted_runs_db import (  # noqa: E402,F401
    no_queue, scene, session,
)


def _sync_session():
    from app.tasks.monitor_tasks import sync_engine
    return Session(sync_engine)


async def test_saved_settings_reach_the_dispatcher(session, scene):
    session.add(UserSettings(user_id=scene.user.id, video_recording="always",
                             trace_files=False, screenshot_on_error=False))
    await session.commit()
    with _sync_session() as s:
        assert load_for_user(scene.user.id, s) == {
            "video": "always", "trace": False, "failure_screenshot": False}


async def test_user_who_never_saved_settings_gets_the_page_defaults(session, scene):
    with _sync_session() as s:
        assert load_for_user(scene.user.id, s) == DEFAULT_PREFS
