"""Settings → Notifications decide whether and where the run OWNER is
emailed (they used to be saved and never read)."""
from types import SimpleNamespace as NS

import pytest

from app.services.notification_prefs import owner_email
from app.settings_models import normalize_notification_email

USER = NS(email="dev@corp.example.com")


def prefs(**kw):
    base = dict(email_notifications=True, notify_on_completion=True,
                notify_on_failure=True, notification_email=None)
    base.update(kw)
    return NS(**base)


def test_owner_without_saved_settings_keeps_the_old_behaviour():
    assert owner_email(USER, None, run_failed=False) == "dev@corp.example.com"


def test_opt_out():
    assert owner_email(USER, prefs(email_notifications=False), run_failed=True) is None


@pytest.mark.parametrize("completion,failure,failed,expected", [
    (True, True, False, True),     # all runs
    (True, False, False, True),    # completion covers failures too
    (False, True, False, False),   # failures only: a pass is skipped
    (False, True, True, True),     # ... a failure is sent
    (False, False, True, False),   # neither
])
def test_completion_and_failure_choices(completion, failure, failed, expected):
    got = owner_email(USER, prefs(notify_on_completion=completion, notify_on_failure=failure),
                      run_failed=failed)
    assert (got is not None) == expected


def test_custom_address_replaces_the_account_address():
    assert owner_email(USER, prefs(notification_email="alerts@corp.example.com"),
                       run_failed=True) == "alerts@corp.example.com"
    assert owner_email(USER, prefs(notification_email="  "), run_failed=True) == USER.email


def test_no_user_or_address_means_nobody():
    assert owner_email(None, None, run_failed=True) is None
    assert owner_email(NS(email=""), prefs(), run_failed=True) is None


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("   ", None),
    ("ops@corp.example.com", "ops@corp.example.com"),
    ("  first.last+qa@sub.corp.example.co.in ", "first.last+qa@sub.corp.example.co.in"),
])
def test_valid_notification_emails(raw, expected):
    assert normalize_notification_email(raw) == expected


@pytest.mark.parametrize("raw", [
    "not-an-email", "a@b", "a@@b.com", "two@a.com, three@b.com",
    "a@b.com\nBcc: everyone@corp.example.com",          # header injection
    "a@b.com\r\nSubject: x", "Name <a@b.com>", "a b@c.com", "x" * 250 + "@b.com",
])
def test_rejected_notification_emails(raw):
    with pytest.raises(ValueError):
        normalize_notification_email(raw)


def test_update_model_validates_on_input():
    from pydantic import ValidationError
    from app.settings_models import UserSettingsUpdate
    assert UserSettingsUpdate(notification_email=" ops@corp.example.com ").notification_email == "ops@corp.example.com"
    assert UserSettingsUpdate(notification_email="").notification_email is None
    with pytest.raises(ValidationError):
        UserSettingsUpdate(notification_email="a@b.com\nBcc: x@y.com")
