"""The run owner's own email preferences (Settings → Notifications).

Until 2026-10 these were saved and never read: the run owner was emailed for
every run whenever the instance had email on. They now decide whether, and
where, the OWNER is emailed. The instance switches stay the master control
(no email channel, or NOTIFY_ON_FAILURE_ONLY, still win) and extra project
recipients are unaffected.

Whose preferences: the user the run is attributed to (whoever clicked Run,
or a schedule's creator). An owner with no saved settings keeps the old
behaviour: emailed for every run at their account address.
"""
from typing import Any, Optional


def owner_email(user: Any, user_settings: Optional[Any], run_failed: bool) -> Optional[str]:
    """Address to email the run owner at, or None to leave them out."""
    account = getattr(user, "email", None) if user is not None else None
    if not account:
        return None
    if user_settings is None:
        return account
    if not getattr(user_settings, "email_notifications", True):
        return None
    wants = getattr(user_settings, "notify_on_completion", True) or (
        run_failed and getattr(user_settings, "notify_on_failure", True))
    if not wants:
        return None
    return (getattr(user_settings, "notification_email", None) or "").strip() or account
