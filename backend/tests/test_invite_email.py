"""The invitation email must not be usable to send someone else's wording.

Anyone who may invite can make this server email an address they choose. So
the subject carries no caller text, the inviter is shown by account email (not
a free-text display name), and the workspace name is reduced to inert words.
"""
from datetime import datetime

from app.core.config import settings
from app.services import invite_email


def _build(**over):
    args = dict(workspace_name="Checkout team", inviter_email="maya@acme.test", role="member",
                token="tok_en/+ value", expires_at=datetime(2026, 11, 17, 9, 0))
    args.update(over)
    return invite_email.build_invitation(**args)


def test_says_who_what_and_where():
    subject, html, text = _build()
    assert subject == "You are invited to a TraceIQ workspace"
    link = settings.FRONTEND_BASE_URL.rstrip("/") + "/signup?token=tok_en%2F%2B%20value"
    assert link in text and link in html
    assert 'maya@acme.test invited you to join the workspace "Checkout team"' in text
    assert "as a member" in text and "17 November 2026" in text
    assert "Sign up with this email address" in text


def test_admin_role_and_unknown_role():
    assert "as an admin" in _build(role="admin")[2]
    assert "as a member" in _build(role="superuser")[2]


def test_subject_never_carries_caller_text():
    for ws in ("URGENT: your account is locked", "Team\r\nBcc: victim@example.com", "x" * 500):
        assert _build(workspace_name=ws)[0] == "You are invited to a TraceIQ workspace"
        assert invite_email.build_added(workspace_name=ws, inviter_email="a@b.test", role="member")[0] \
            == "You were added to a TraceIQ workspace"


def test_workspace_name_cannot_carry_an_address_or_markup():
    _, html, text = _build(
        workspace_name='Verify at https://evil.example/login or mail help@evil.example <script>alert(1)</script>')
    for part in (html, text):
        assert "evil.example" not in part and "https://" not in part and "@evil" not in part
        assert "<script>" not in part
    # What is left is plain words (and cut to 80 characters).
    assert 'the workspace "Verify at https evil example login or mail help evil example script' in text
    # The only link is ours.
    assert html.count("<a ") == 1
    assert f'<a href="{settings.FRONTEND_BASE_URL.rstrip("/")}/signup?token=' in html
    assert text.count("http") == 1 + settings.FRONTEND_BASE_URL.count("http") - 1


def test_inviter_is_an_account_address_or_nothing():
    assert "maya@acme.test invited you" in _build()[2]
    for bad in ("IT Security: verify at evil.example", "a@b.test\nBcc: x@y.test", "<a@b.test>", "", None):
        text = _build(inviter_email=bad)[2]
        assert text.startswith("A colleague invited you")
        assert "evil.example" not in text and "Bcc" not in text


def test_long_and_empty_workspace_names():
    text = _build(workspace_name="W" * 500)[2]
    assert "W" * 80 in text and "W" * 81 not in text
    assert 'the workspace "a workspace"' in _build(workspace_name=None)[2]
    assert 'the workspace "a workspace"' in _build(workspace_name="...///@@@")[2]


def test_names_from_other_scripts_survive():
    assert '"Équipe paiement 決済"' in _build(workspace_name="Équipe paiement 決済")[2]


def test_added_message_has_no_token():
    subject, html, text = invite_email.build_added(workspace_name="Checkout team", inviter_email="maya@acme.test", role="admin")
    assert subject == "You were added to a TraceIQ workspace"
    assert "token" not in text and "token" not in html
    assert 'maya@acme.test added you to the workspace "Checkout team"' in text and "as an admin" in text
