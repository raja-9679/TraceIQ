"""The invitation email's wording is fixed; only two names come from callers.

Anyone who may invite can make this server email an address they choose, so
those two names must not be able to carry markup, extra header lines or a
second paragraph of their own.
"""
from datetime import datetime

from app.core.config import settings
from app.services import invite_email


def _build(**over):
    args = dict(workspace_name="Checkout team", inviter_name="Maya Rao", role="member",
                token="tok_en/+ value", expires_at=datetime(2026, 11, 17, 9, 0))
    args.update(over)
    return invite_email.build_invitation(**args)


def test_says_who_what_and_where():
    subject, html, text = _build()
    assert subject == "Maya Rao invited you to Checkout team on TraceIQ"
    link = settings.FRONTEND_BASE_URL.rstrip("/") + "/signup?token=tok_en%2F%2B%20value"
    assert link in text and link in html
    assert "as a member" in text and "17 November 2026" in text
    assert "Sign up with this email address" in text


def test_admin_role_and_unknown_role():
    assert "as an admin" in _build(role="admin")[2]
    assert "as a member" in _build(role="superuser")[2]


def test_names_cannot_add_header_lines_or_markup():
    subject, html, text = _build(
        workspace_name='Team\r\nBcc: victim@example.com\n<script>alert(1)</script>',
        inviter_name='<a href="https://evil.example">Click</a> more')
    assert "\n" not in subject and "\r" not in subject
    assert "<script>" not in html and "<a href=\"https://evil.example\">" not in html
    assert "&lt;script&gt;" in html
    # The only link in the HTML is ours.
    assert html.count('<a href="') == 1 and html.count("<a ") == 1
    assert f'<a href="{settings.FRONTEND_BASE_URL.rstrip("/")}/signup?token=' in html


def test_long_and_empty_names():
    subject, _, text = _build(workspace_name="W" * 500, inviter_name="   ")
    assert "W" * 81 not in subject
    assert subject.startswith("A colleague invited you to ")
    assert "a workspace" in _build(workspace_name=None)[0]


def test_added_message_has_no_token():
    subject, html, text = invite_email.build_added(workspace_name="Checkout team", inviter_name="Maya Rao", role="admin")
    assert subject == "Maya Rao added you to Checkout team on TraceIQ"
    assert "token" not in text and "token" not in html
    assert "as an admin" in text
