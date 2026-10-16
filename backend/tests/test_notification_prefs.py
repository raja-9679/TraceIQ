"""Settings → Notifications decide whether and where the run OWNER is
emailed (they used to be saved and never read)."""
from types import SimpleNamespace as NS

import pytest

from app.services.project_notifications import person_wants
from app.settings_models import normalize_notification_email

def prefs(**kw):
    base = dict(email_notifications=True, notify_on_completion=True,
                notify_on_failure=True, notification_email=None)
    base.update(kw)
    return NS(**base)


def test_no_saved_settings_and_no_project_choice_means_yes():
    assert person_wants(None, None, run_failed=False) is True


def test_personal_opt_out():
    assert person_wants(None, prefs(email_notifications=False), run_failed=True) is False


@pytest.mark.parametrize("completion,failure,failed,expected", [
    (True, True, False, True),     # all runs
    (True, False, False, True),    # completion covers failures too
    (False, True, False, False),   # failures only: a pass is skipped
    (False, True, True, True),     # ... a failure is sent
    (False, False, True, False),   # neither
])
def test_personal_default_choices(completion, failure, failed, expected):
    us = prefs(notify_on_completion=completion, notify_on_failure=failure)
    assert person_wants(None, us, run_failed=failed) is expected


@pytest.mark.parametrize("mode,failed,expected", [
    ("off", True, False), ("failures", False, False), ("failures", True, True), ("all", False, True),
])
def test_project_choice_overrides_the_personal_default(mode, failed, expected):
    # Even someone who turned email off globally can opt in for one project.
    assert person_wants(mode, prefs(email_notifications=False), run_failed=failed) is expected


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
