"""Issue-tracker HTTP calls are net_guarded at SEND time and never follow a
redirect. The base URL is checked when the config is saved too, but DNS can
change after that, and a redirect would re-send the tracker credentials and
the run's evidence to wherever it pointed."""
from types import SimpleNamespace

import pytest

from app.core.net_guard import UnsafeUrlError
from app.services import issue_trackers as it


@pytest.fixture
def calls(monkeypatch):
    class Sent(list):
        status = 201
    sent = Sent()

    def post(url, **kw):
        sent.append((url, kw))
        return SimpleNamespace(status_code=sent.status, ok=200 <= sent.status < 300,
                               text="", json=lambda: {"key": "QA-1", "number": 1, "html_url": "u"})
    monkeypatch.setattr(it.requests, "post", post)
    monkeypatch.setattr(it, "validate_outbound_url_sync", lambda url, **kw: url)
    return sent


def _providers():
    return [it.get_provider("jira", "https://jira.example.com", "a", "s", {"project_key": "QA"}),
            it.get_provider("itop", "https://itop.example.com", "a", "s", {}),
            it.get_provider("github", None, None, "s", {"repo": "o/r"})]


@pytest.mark.parametrize("provider", _providers())
def test_refused_url_never_sends(calls, monkeypatch, provider):
    def refuse(url, **kw):
        raise UnsafeUrlError("resolves to link-local")
    monkeypatch.setattr(it, "validate_outbound_url_sync", refuse)
    with pytest.raises(it.IssueTrackerError, match="network policy"):
        provider.create_issue("s", "d")
    assert calls == []


@pytest.mark.parametrize("provider", _providers())
def test_redirects_are_not_followed(calls, provider):
    calls.status = 307
    with pytest.raises(it.IssueTrackerError, match="redirect"):
        provider.create_issue("s", "d")
    assert calls and all(kw["allow_redirects"] is False for _, kw in calls)


def test_attachments_are_guarded_too(calls, monkeypatch):
    jira = it.get_provider("jira", "https://jira.example.com", "a", "s", {"project_key": "QA"})
    jira.create_issue("s", "d")
    checked = []
    monkeypatch.setattr(it, "validate_outbound_url_sync", lambda url, **kw: checked.append(url) or url)
    jira.attach_file("t.zip", b"x", "application/zip")
    assert checked == ["https://jira.example.com/rest/api/2/issue/QA-1/attachments"]
    assert calls[-1][1]["allow_redirects"] is False
