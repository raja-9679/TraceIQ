"""Project notification settings: validation, secret handling, the effective
settings every sender uses, and the guarded webhook POST."""
from types import SimpleNamespace as NS

import pytest

from app.services import project_notifications as pn

SLACK = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXsecretpart"


def project(**settings):
    return NS(id=1, notification_settings=settings or None)


# --- validation ------------------------------------------------------------------

def test_validate_accepts_a_full_patch():
    patch = pn.validate_patch({
        "email_enabled": False, "failures_only": True, "slack_enabled": True,
        "recipient_user_ids": [3, 3, 7], "extra_emails": [" QA@corp.example.com ", "qa@corp.example.com"],
        "slack_webhook": SLACK,
    })
    assert patch["recipient_user_ids"] == [3, 7]                 # de-duplicated
    assert patch["extra_emails"] == ["QA@corp.example.com"]      # trimmed, case-insensitive dedupe
    assert patch["slack_webhook"] == SLACK


@pytest.mark.parametrize("payload", [
    {"nope": 1},                                   # unknown keys are refused
    {"email_enabled": "yes"},
    {"recipient_user_ids": [0]}, {"recipient_user_ids": ["3"]}, {"recipient_user_ids": [True]},
    {"recipient_user_ids": list(range(1, 60))},
    {"extra_emails": ["a@b.com\nBcc: x@y.com"]},   # header injection
    {"extra_emails": [f"u{i}@corp.example.com" for i in range(25)]},
    {"slack_webhook": "http://hooks.slack.com/x"},  # https only
    {"teams_webhook": 42},
])
def test_validate_rejects(payload):
    with pytest.raises(ValueError):
        pn.validate_patch(payload)


def test_empty_webhook_means_clear():
    assert pn.validate_patch({"slack_webhook": ""}) == {"slack_webhook": ""}
    assert pn.validate_patch({"slack_webhook": None}) == {"slack_webhook": ""}


# --- secrets ------------------------------------------------------------------------

def test_webhooks_are_stored_encrypted_and_never_described():
    stored = pn.apply_patch({}, {"slack_webhook": SLACK, "email_enabled": False})
    assert "slack_webhook" not in stored and SLACK not in str(stored)
    p = NS(id=1, notification_settings=stored)
    view = pn.describe(p)
    assert view["slack_webhook_set"] is True and view["teams_webhook_set"] is False
    assert SLACK not in str(view)
    assert view["slack_webhook_hint"].startswith("https://hooks.slack.com/") and "secretpart" not in view["slack_webhook_hint"]
    assert pn.webhook_url(p, "slack") == SLACK
    cleared = pn.apply_patch(stored, {"slack_webhook": ""})
    assert "slack_webhook_enc" not in cleared


def test_defaults_when_nothing_is_stored():
    view = pn.describe(project())
    assert view["email_enabled"] is True and view["recipient_user_ids"] == []
    assert view["slack_webhook_set"] is False


# --- effective settings (shared by run, monitor and estate notifications) --------

@pytest.fixture
def instance(monkeypatch):
    from app.services import instance_settings
    values = {"NOTIFICATIONS_ENABLED": True, "EMAIL_NOTIFICATIONS_ENABLED": True,
              "SLACK_NOTIFICATIONS_ENABLED": True, "TEAMS_NOTIFICATIONS_ENABLED": True,
              "NOTIFY_ON_FAILURE_ONLY": False,
              "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/INSTANCE/WIDE/CHANNEL",
              "TEAMS_WEBHOOK_URL": "https://example.webhook.office.com/INSTANCE"}
    monkeypatch.setattr(instance_settings, "effective", lambda k: values.get(k))
    return values


def _settings(p):
    from app.tasks.notification_tasks import get_notification_settings
    return get_notification_settings(p, session=None)


def test_instance_webhook_is_never_used_for_a_project(instance):
    cfg = _settings(project(slack_enabled=True))         # enabled but no project webhook
    assert cfg["slack_enabled"] is False and cfg["slack_webhook_url"] is None
    assert "INSTANCE" not in str(cfg)


def test_project_webhook_is_used(instance):
    stored = pn.apply_patch({"slack_enabled": True}, {"slack_webhook": SLACK})
    cfg = _settings(NS(id=1, notification_settings=stored))
    assert cfg["slack_enabled"] is True and cfg["slack_webhook_url"] == SLACK


def test_instance_switches_stay_the_master(instance):
    stored = pn.apply_patch({"slack_enabled": True, "email_enabled": True}, {"slack_webhook": SLACK})
    instance["SLACK_NOTIFICATIONS_ENABLED"] = False
    instance["EMAIL_NOTIFICATIONS_ENABLED"] = False
    cfg = _settings(NS(id=1, notification_settings=stored))
    assert cfg["slack_enabled"] is False and cfg["email_enabled"] is False


def test_failures_only_from_either_level(instance):
    assert _settings(project(failures_only=True))["notify_on_failure_only"] is True
    instance["NOTIFY_ON_FAILURE_ONLY"] = True
    assert _settings(project())["notify_on_failure_only"] is True


def test_project_can_switch_email_off(instance):
    assert _settings(project(email_enabled=False))["email_enabled"] is False


# --- guarded webhook POST ---------------------------------------------------------

def test_post_webhook_refuses_internal_targets(monkeypatch):
    from app.tasks import notification_tasks
    calls = []
    monkeypatch.setattr(notification_tasks.requests, "post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(Exception):
        notification_tasks.post_webhook("https://169.254.169.254/latest/meta-data", {"x": 1})
    assert calls == []


def test_post_webhook_disables_redirects(monkeypatch):
    from app.core import net_guard
    from app.tasks import notification_tasks
    calls = []
    monkeypatch.setattr(net_guard, "validate_outbound_url_sync", lambda url, **k: url)
    monkeypatch.setattr(notification_tasks.requests, "post", lambda url, **k: calls.append(k) or NS(status_code=200))
    notification_tasks.post_webhook(SLACK, {"text": "hi"})
    assert calls[0]["allow_redirects"] is False and calls[0]["json"] == {"text": "hi"}
