"""Invitation emails.

Until 2026-11 an invitation produced only a link, shown once to the inviter,
who had to pass it on by hand. Now the invitee is emailed when the instance
has SMTP configured; the link is still returned to the inviter either way, so
an instance without email works exactly as before.

Three things shape this module:

* **The text is fixed.** Anyone who may invite can make this server send mail
  to an address of their choosing, so nothing they can type freely may read as
  a message of its own. The subject carries no caller text at all. The inviter
  is shown by their ACCOUNT EMAIL, never their display name (a name can be set
  to "IT Security: verify at evil.example"). The workspace name is reduced to
  letters, digits and a few marks — no dots, slashes, colons or @, so it cannot
  form a web address — cut short, and HTML-escaped in the HTML part.
* **Demo workspaces never send** (the rule for every outbound path).
* **"sent" means handed to the mail queue**, not delivered: the response says
  `email_sent`, and the inviter keeps the link in case the mail never arrives.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Optional, Tuple

from app.core.config import settings

_ROLE_LABELS = {"admin": "an admin", "member": "a member"}
_MAX_NAME = 80


def _plain(value: Optional[str], fallback: str) -> str:
    """A workspace name as inert words: letters, digits, spaces and - _ & ' ( ).

    Everything else becomes a space — in particular . / : @, without which
    there is no web address, email address or "click here: ..." to act on.
    """
    text = re.sub(r"[^\w\s&'()-]+", " ", value or "", flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_NAME].strip() or fallback


def _inviter(email: Optional[str]) -> str:
    """The inviter's account address, or a neutral phrase if it is not one."""
    from app.settings_models import _EMAIL_RE
    email = (email or "").strip()
    return email if len(email) <= 254 and _EMAIL_RE.match(email) else "A colleague"


def invitation_link(token: str) -> str:
    """Same address the app builds for the inviter (Signup reads ?token=)."""
    from urllib.parse import quote
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}/signup?token={quote(token, safe='')}"


def app_link() -> str:
    return settings.FRONTEND_BASE_URL.rstrip("/") + "/"


def _page(heading: str, paragraphs: list[str], button: Tuple[str, str], foot: str) -> str:
    """Minimal HTML mail. Every argument is already escaped by the caller."""
    label, href = button
    body = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragraphs)
    return (
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1c1b22;max-width:520px">'
        f'<h1 style="font-size:20px;margin:0 0 16px">{heading}</h1>{body}'
        f'<p style="margin:22px 0"><a href="{href}" style="background:#1c1b22;color:#fff;'
        'text-decoration:none;padding:10px 18px;border-radius:999px;font-weight:600">'
        f'{label}</a></p>'
        f'<p style="margin:0 0 14px;color:#6b6878;font-size:13px">{foot}</p></div>'
    )


def build_invitation(*, workspace_name: Optional[str], inviter_email: Optional[str],
                     role: Optional[str], token: str,
                     expires_at: Optional[datetime]) -> Tuple[str, str, str]:
    """(subject, html, text) for someone who has no account yet."""
    ws = _plain(workspace_name, "a workspace")
    who = _inviter(inviter_email)
    as_role = _ROLE_LABELS.get((role or "").lower(), "a member")
    link = invitation_link(token)
    until = f" The link works until {expires_at:%d %B %Y}." if expires_at else ""

    subject = "You are invited to a TraceIQ workspace"
    text = (
        f"{who} invited you to join the workspace \"{ws}\" on TraceIQ as {as_role}.\n\n"
        f"Create your account here:\n{link}\n\n"
        f"Sign up with this email address; the invitation is tied to it.{until}\n\n"
        "If you were not expecting this, you can ignore this email."
    )
    e = html.escape
    html_body = _page(
        f"You are invited to {e(ws)}",
        [f"{e(who)} invited you to join the workspace <strong>{e(ws)}</strong> on TraceIQ as {e(as_role)}.",
         f"Sign up with this email address; the invitation is tied to it.{e(until)}"],
        ("Create your account", e(link, quote=True)),
        "If you were not expecting this, you can ignore this email.",
    )
    return subject, html_body, text


def build_added(*, workspace_name: Optional[str], inviter_email: Optional[str],
                role: Optional[str]) -> Tuple[str, str, str]:
    """(subject, html, text) for an existing user who was added directly."""
    ws = _plain(workspace_name, "a workspace")
    who = _inviter(inviter_email)
    as_role = _ROLE_LABELS.get((role or "").lower(), "a member")
    link = app_link()

    subject = "You were added to a TraceIQ workspace"
    text = (
        f"{who} added you to the workspace \"{ws}\" on TraceIQ as {as_role}.\n\n"
        f"It is in your workspace list the next time you sign in:\n{link}\n\n"
        "If this looks wrong, ask the person who added you or a workspace admin."
    )
    e = html.escape
    html_body = _page(
        f"You were added to {e(ws)}",
        [f"{e(who)} added you to the workspace <strong>{e(ws)}</strong> on TraceIQ as {e(as_role)}.",
         "It is in your workspace list the next time you sign in."],
        ("Open TraceIQ", e(link, quote=True)),
        "If this looks wrong, ask the person who added you or a workspace admin.",
    )
    return subject, html_body, text


def email_configured() -> bool:
    from app.services import instance_settings as insvc
    return bool(insvc.effective("SMTP_HOST"))


def deliver(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    """Queue the mail (Celery); send inline if the broker is unreachable.

    Never raises: the invitation exists whether or not the mail goes out, and
    the inviter still has the link.
    """
    from app.tasks.notification_tasks import send_account_email
    try:
        send_account_email.delay(to_email, subject, html_body, text_body)
    except Exception:  # noqa: BLE001 - broker down
        try:
            send_account_email(to_email, subject, html_body, text_body)
        except Exception:  # noqa: BLE001
            pass


async def send(session: Any, *, workspace_id: int, to_email: str,
               message: Tuple[str, str, str]) -> bool:
    """Send `message` unless email is not set up or the workspace is a demo.

    Returns whether a mail was handed to the queue.
    """
    from app.services.demo_workspace import is_demo_workspace
    if not email_configured() or await is_demo_workspace(session, workspace_id):
        return False
    deliver(to_email, *message)
    return True
