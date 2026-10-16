"""Settings → video recording / trace files / screenshot on error reach the
job as `artifact_prefs` (they used to be saved and never read)."""
from types import SimpleNamespace

import pytest

from app.services.artifact_prefs import DEFAULT_PREFS, load_for_user, prefs_from_user_settings
import app.models  # noqa: F401 - registers User for the UserSettings relationship
from app.settings_models import UserSettings


def test_defaults_match_the_settings_page_defaults():
    # The worker gets exactly what the page shows a user who changed nothing.
    us = UserSettings(user_id=1)
    assert prefs_from_user_settings(us) == DEFAULT_PREFS == {
        "video": us.video_recording, "trace": us.trace_files,
        "failure_screenshot": us.screenshot_on_error}


def test_no_settings_row_gets_the_defaults():
    assert prefs_from_user_settings(None) == DEFAULT_PREFS


@pytest.mark.parametrize("video,expected", [
    ("always", "always"), ("off", "off"), ("on-failure", "on-failure"),
    ("never", "off"),   # what the Settings page actually stores for "Never record"
    ("ALWAYS", "always"), ("sometimes", "on-failure"), (None, "on-failure"),
])
def test_video_choice_is_validated(video, expected):
    us = SimpleNamespace(video_recording=video, trace_files=True, screenshot_on_error=True)
    assert prefs_from_user_settings(us)["video"] == expected


def test_switches_are_carried():
    us = SimpleNamespace(video_recording="always", trace_files=False, screenshot_on_error=False)
    assert prefs_from_user_settings(us) == {"video": "always", "trace": False, "failure_screenshot": False}


def test_runs_without_a_user_get_defaults_without_touching_the_db():
    class Boom:
        def exec(self, *a, **k):
            raise AssertionError("must not query")
    assert load_for_user(None, Boom()) == DEFAULT_PREFS


def test_a_failing_lookup_never_blocks_the_run():
    class Broken:
        def exec(self, *a, **k):
            raise RuntimeError("db down")
    assert load_for_user(7, Broken()) == DEFAULT_PREFS


def test_prefs_ride_on_every_job_payload():
    from app.worker import _settings_payload
    prefs = {"video": "always", "trace": True, "failure_screenshot": False}
    payload = _settings_payload({"artifact_prefs": prefs, "data_policy": {"capture_level": "full"}})
    assert payload["artifact_prefs"] == prefs
    assert payload["data_policy"]["capture_level"] == "full"   # policy still travels separately
