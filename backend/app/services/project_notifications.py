"""Per-project notification settings: who hears about a project's runs, where.

Before 2026-10 there were none. Email went to the run starter only, and Slack
and Teams used ONE instance-wide webhook, so every project's runs (every
tenant's, on a shared instance) were posted to the same channel.

Now, per project (`Project.notification_settings`):

    email_enabled        bool   default true
    recipient_user_ids   [int]  people the project admin picks; each must have
                                access to the project (re-checked at send time)
    extra_emails         [str]  plain addresses (distribution lists etc.)
    failures_only        bool   skip runs where every test passed
    notify_case_owners   bool   email a case's owner on its first failure
                                after a pass (WP16b; default false)
    slack_enabled / teams_enabled             bool
    slack_webhook_enc / teams_webhook_enc     encrypted URL (never returned)

and per person and project (`ProjectNotificationPref.mode`): all / failures /
off, falling back to the person's own Settings → Notifications.

The instance switches (NOTIFICATIONS_ENABLED, *_NOTIFICATIONS_ENABLED,
NOTIFY_ON_FAILURE_ONLY) stay the master control. The instance
SLACK/TEAMS_WEBHOOK_URL is not used for project runs any more.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

PREF_MODES = ("all", "failures", "off")
MAX_RECIPIENTS = 50
MAX_EXTRA_EMAILS = 20

DEFAULTS: Dict[str, Any] = {
    "email_enabled": True,
    "recipient_user_ids": [],
    "extra_emails": [],
    "failures_only": False,
    "slack_enabled": False,
    "teams_enabled": False,
    # WP16b: email a case's owner (backup if the owner can't be reached) on
    # the case's first failure after a pass. Opt-in.
    "notify_case_owners": False,
}

_BOOL_KEYS = ("email_enabled", "failures_only", "slack_enabled", "teams_enabled", "notify_case_owners")
_WEBHOOKS = {"slack_webhook": "slack_webhook_enc", "teams_webhook": "teams_webhook_enc"}


def stored(project: Any) -> Dict[str, Any]:
    raw = getattr(project, "notification_settings", None)
    return dict(raw) if isinstance(raw, dict) else {}


def merged(project: Any) -> Dict[str, Any]:
    """Stored settings over the defaults (webhooks still encrypted)."""
    return {**DEFAULTS, **stored(project)}


def _hint(url: str) -> str:
    """Enough of a webhook URL to recognise it, not enough to use it."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    tail = url.rstrip("/")[-4:]
    return f"{parts.scheme}://{parts.hostname or ''}/…{tail}"


def describe(project: Any) -> Dict[str, Any]:
    """API view: never contains a webhook URL, only whether one is set."""
    from app.core.secrets import decrypt_secret
    m = merged(project)
    view = {k: m[k] for k in DEFAULTS}
    for public, enc in _WEBHOOKS.items():
        ct = m.get(enc)
        view[f"{public}_set"] = bool(ct)
        try:
            view[f"{public}_hint"] = _hint(decrypt_secret(ct)) if ct else None
        except Exception:  # noqa: BLE001 - an undecryptable value shows as set, no hint
            view[f"{public}_hint"] = None
    return view


def _int_list(value: Any, limit: int, name: str) -> List[int]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    out: List[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"{name} must contain user ids")
        if v not in out:
            out.append(v)
    if len(out) > limit:
        raise ValueError(f"{name}: at most {limit}")
    return out


def validate_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a partial update. Webhook URLs come back as PLAINTEXT under
    `slack_webhook` / `teams_webhook` ('' = clear); the caller vets them with
    net_guard and encrypts them. Unknown keys are refused."""
    from app.settings_models import normalize_notification_email
    if not isinstance(payload, dict):
        raise ValueError("body must be an object")
    allowed = set(DEFAULTS) | set(_WEBHOOKS)
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    patch: Dict[str, Any] = {}
    for k in _BOOL_KEYS:
        if k in payload:
            if not isinstance(payload[k], bool):
                raise ValueError(f"{k} must be true or false")
            patch[k] = payload[k]
    if "recipient_user_ids" in payload:
        patch["recipient_user_ids"] = _int_list(payload["recipient_user_ids"], MAX_RECIPIENTS, "recipient_user_ids")
    if "extra_emails" in payload:
        if not isinstance(payload["extra_emails"], list):
            raise ValueError("extra_emails must be a list")
        emails: List[str] = []
        for e in payload["extra_emails"]:
            if not isinstance(e, str):
                raise ValueError("extra_emails must contain addresses")
            v = normalize_notification_email(e)
            if v and v.lower() not in {x.lower() for x in emails}:
                emails.append(v)
        if len(emails) > MAX_EXTRA_EMAILS:
            raise ValueError(f"extra_emails: at most {MAX_EXTRA_EMAILS}")
        patch["extra_emails"] = emails
    for public in _WEBHOOKS:
        if public in payload:
            v = payload[public]
            if v is None:
                v = ""
            if not isinstance(v, str):
                raise ValueError(f"{public} must be a URL or empty")
            v = v.strip()
            if v and not v.lower().startswith("https://"):
                raise ValueError(f"{public} must be an https:// URL")
            if len(v) > 2048:
                raise ValueError(f"{public} is too long")
            patch[public] = v
    return patch


def apply_patch(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Stored dict after a validated patch; webhooks are encrypted here."""
    from app.core.secrets import encrypt_secret
    out = dict(current)
    for k, v in patch.items():
        if k in _WEBHOOKS:
            enc = _WEBHOOKS[k]
            if v:
                out[enc] = encrypt_secret(v)
            else:
                out.pop(enc, None)
        else:
            out[k] = v
    return out


def webhook_url(project: Any, which: str) -> Optional[str]:
    """Decrypted project webhook ('slack' / 'teams'), or None."""
    from app.core.secrets import decrypt_secret
    ct = stored(project).get(f"{which}_webhook_enc")
    if not ct:
        return None
    try:
        return decrypt_secret(ct)
    except Exception:  # noqa: BLE001 - undecryptable (key rotated away) = not configured
        return None


# --- send-time recipient rules (sync: they run in Celery tasks) ---------------

def has_project_access_sync(session, user_id: int, project_id: int) -> bool:
    """Viewer-level access, the same rule as AccessService.has_project_access:
    workspace admin, a direct project grant, or a team granted the project."""
    from sqlmodel import select
    from app.models import Project, Role, TeamProjectAccess, UserProjectAccess, UserTeam, UserWorkspace
    project = session.get(Project, project_id)
    if not project:
        return False
    for uw in session.exec(select(UserWorkspace).where(
            UserWorkspace.user_id == user_id, UserWorkspace.workspace_id == project.workspace_id)).all():
        if uw.role_id is not None:
            role = session.get(Role, uw.role_id)
            if role and role.name == "Workspace Admin":
                return True
        elif uw.role == "admin":
            return True
    if session.exec(select(UserProjectAccess).where(
            UserProjectAccess.user_id == user_id, UserProjectAccess.project_id == project_id)).first():
        return True
    return session.exec(
        select(TeamProjectAccess).join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
        .where(UserTeam.user_id == user_id, TeamProjectAccess.project_id == project_id)
        .limit(1)).first() is not None


def person_wants(mode: Optional[str], user_settings: Any, run_failed: bool) -> bool:
    """A recipient's own choice: the project-specific mode if they set one,
    else their Settings → Notifications default."""
    if mode == "off":
        return False
    if mode == "failures":
        return run_failed
    if mode == "all":
        return True
    if user_settings is None:
        return True
    if not getattr(user_settings, "email_notifications", True):
        return False
    return bool(getattr(user_settings, "notify_on_completion", True)
                or (run_failed and getattr(user_settings, "notify_on_failure", True)))


def email_recipients(session, project: Any, starter_id: Optional[int], run_failed: bool) -> List[str]:
    """Addresses for a project run's email, deduplicated.

    The run starter and every picked person must still have access to the
    project; each is asked about their own preference. Extra addresses are
    the project admin's call and are always included.
    """
    from sqlmodel import select
    from app.models import ProjectNotificationPref, User
    from app.settings_models import UserSettings
    cfg = merged(project)
    people: List[int] = []
    for uid in ([starter_id] if starter_id else []) + list(cfg.get("recipient_user_ids") or []):
        if uid and uid not in people:
            people.append(uid)
    out: List[str] = []
    seen: Set[str] = set()

    def add(addr: Optional[str]):
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)

    for uid in people:
        user = session.get(User, uid)
        if not user or not user.email or not getattr(user, "is_active", True):
            continue
        if not has_project_access_sync(session, uid, project.id):
            continue
        us = session.exec(select(UserSettings).where(UserSettings.user_id == uid)).first()
        pref = session.exec(select(ProjectNotificationPref).where(
            ProjectNotificationPref.user_id == uid,
            ProjectNotificationPref.project_id == project.id)).first()
        if not person_wants(pref.mode if pref else None, us, run_failed):
            continue
        add((getattr(us, "notification_email", None) or "").strip() or user.email)
    for addr in cfg.get("extra_emails") or []:
        add(addr)
    return out
