"""The run starter's artifact preferences, attached to every job.

Settings → "Video recording", "Trace files" and "Screenshot on error" used to
be saved and never read. They now travel with each job as `artifact_prefs`
and the worker applies them — but only INSIDE what the project's capture
level allows (app/services/data_policy.py): a preference can turn video off
or limit it to failed tests, it can never record video on a `standard`
project.

Whose preferences: the user the run is attributed to (`TestRun.user_id` —
the person who clicked Run, or a schedule's creator). Runs with no user, and
users who never saved Settings, get the page's own defaults.
"""
from typing import Any, Dict, Optional

VIDEO_CHOICES = ("off", "on-failure", "always")
#: The Settings page stores "never" for "Never record"; the worker's word is "off".
_VIDEO_ALIASES = {"never": "off"}

#: Mirrors the UserSettings column defaults, i.e. what the Settings page shows
#: a user who never changed anything.
DEFAULT_PREFS: Dict[str, Any] = {"video": "on-failure", "trace": True, "failure_screenshot": True}


def prefs_from_user_settings(user_settings: Optional[Any]) -> Dict[str, Any]:
    if user_settings is None:
        return dict(DEFAULT_PREFS)
    video = str(getattr(user_settings, "video_recording", "") or "").strip().lower()
    video = _VIDEO_ALIASES.get(video, video)
    return {
        "video": video if video in VIDEO_CHOICES else DEFAULT_PREFS["video"],
        "trace": bool(getattr(user_settings, "trace_files", True)),
        "failure_screenshot": bool(getattr(user_settings, "screenshot_on_error", True)),
    }


def load_for_user(user_id: Optional[int], session) -> Dict[str, Any]:
    """Preferences for a (sync) dispatch session; never raises."""
    if not user_id:
        return dict(DEFAULT_PREFS)
    try:
        from sqlmodel import select
        from app.settings_models import UserSettings
        row = session.exec(select(UserSettings).where(UserSettings.user_id == user_id)).first()
    except Exception:  # noqa: BLE001 - preferences must never block a run
        return dict(DEFAULT_PREFS)
    return prefs_from_user_settings(row)
