"""Jira / Linear import clients against mocked HTTP (httpx.MockTransport).

These pin the request shapes from the vendors' documentation — Jira Cloud REST
v3 search, Linear GraphQL — not a live tenant.

    ./run-tests.sh tests/test_tracker_import.py -q
"""
import json
from datetime import datetime

import httpx
import pytest

from app.services import tracker_import as ti


@pytest.fixture(autouse=True)
def _allow_urls(monkeypatch):
    seen = []

    async def validate(url, **kw):
        seen.append(url)
        if "169.254" in url:
            from app.core.net_guard import UnsafeUrlError
            raise UnsafeUrlError("metadata address")
        return url
    monkeypatch.setattr(ti, "validate_outbound_url", validate)
    return seen


def _transport(monkeypatch, handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request)
    monkeypatch.setattr(ti, "_TRANSPORT", httpx.MockTransport(wrapped))
    return calls


JIRA_ISSUE = {
    "id": "10001", "key": "SHOP-12",
    "fields": {
        "summary": "Guest checkout",
        "status": {"name": "In Progress"},
        "updated": "2026-10-01T10:00:00.000+0530",
        "labels": ["checkout"],
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "heading", "attrs": {"level": 2},
             "content": [{"type": "text", "text": "Acceptance Criteria"}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": "Guests can pay without an account"}]}]}]},
        ]},
    },
}


async def test_jira_search_uses_v3_search_jql_with_basic_auth(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(200, json={"issues": [JIRA_ISSUE]}))
    issues = await ti.jira_search("https://acme.atlassian.net/", "me@acme.io", "tok",
                                  "project = SHOP", max_results=10)
    req = calls[0]
    assert req.url.path == "/rest/api/3/search/jql"
    assert req.url.params["jql"] == "project = SHOP"
    assert req.url.params["maxResults"] == "10"
    assert "description" in req.url.params["fields"]
    assert req.headers["authorization"].startswith("Basic ")
    [i] = issues
    assert i.key == "SHOP-12" and i.title == "Guest checkout"
    assert i.url == "https://acme.atlassian.net/browse/SHOP-12"
    assert i.status == "In Progress"
    assert i.updated_at == datetime(2026, 10, 1, 4, 30)       # +05:30 → UTC, naive
    assert "Guests can pay without an account" in i.description


async def test_jira_falls_back_to_legacy_search_on_410(monkeypatch):
    def handler(r):
        if r.url.path.endswith("/search/jql"):
            return httpx.Response(404)
        return httpx.Response(200, json={"issues": [JIRA_ISSUE]})
    calls = _transport(monkeypatch, handler)
    issues = await ti.jira_search("https://jira.corp.example", "u", "t", "key = SHOP-12")
    assert [c.url.path for c in calls] == ["/rest/api/3/search/jql", "/rest/api/3/search"]
    assert issues[0].key == "SHOP-12"


async def test_jira_auth_failure_is_a_clean_error_without_the_token(monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(401, text="nope"))
    with pytest.raises(ti.TrackerImportError) as exc:
        await ti.jira_search("https://acme.atlassian.net", "me", "SUPERSECRET", "x")
    assert "credentials" in str(exc.value) and "SUPERSECRET" not in str(exc.value)


async def test_jira_redirect_is_not_followed(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(ti.TrackerImportError, match="redirect"):
        await ti.jira_search("https://acme.atlassian.net", "me", "t", "x")
    assert len(calls) == 1


async def test_jira_get_issues_refuses_jql_injection(monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(200, json={"issues": []}))
    with pytest.raises(ti.TrackerImportError, match="Not Jira issue keys"):
        await ti.jira_get_issues("https://a.atlassian.net", "u", "t", ["SHOP-1) OR project = HR OR key in (X-1"])


async def test_jira_get_issues_builds_key_in_jql(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(200, json={"issues": [JIRA_ISSUE]}))
    await ti.jira_get_issues("https://a.atlassian.net", "u", "t", ["shop-12", " SHOP-13 "])
    assert calls[0].url.params["jql"] == "key in (SHOP-12, SHOP-13)"


async def test_unsafe_base_url_is_refused_before_any_request(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(ti.TrackerImportError, match="refused"):
        await ti.jira_search("http://169.254.169.254", "u", "t", "x")
    assert calls == []


LINEAR_NODE = {
    "id": "uuid-1", "identifier": "ENG-42", "title": "Saved filters",
    "description": "## Acceptance Criteria\n- [ ] Filters persist after reload",
    "url": "https://linear.app/acme/issue/ENG-42", "updatedAt": "2026-10-02T08:00:00.000Z",
    "state": {"name": "Todo"}, "labels": {"nodes": [{"name": "frontend"}]},
}


async def test_linear_search_posts_graphql_with_filter_as_variable(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(
        200, json={"data": {"issues": {"nodes": [LINEAR_NODE]}}}))
    issues = await ti.linear_search(None, "lin_api_key", ti.linear_filter(team_key="eng", query="filter"))
    req = calls[0]
    assert str(req.url) == "https://api.linear.app/graphql"
    assert req.headers["authorization"] == "lin_api_key"         # personal keys: no Bearer
    body = json.loads(req.content)
    assert "issues(filter: $filter" in body["query"]
    assert body["variables"]["filter"] == {"team": {"key": {"eq": "ENG"}},
                                           "title": {"containsIgnoreCase": "filter"}}
    [i] = issues
    assert (i.key, i.status, i.labels) == ("ENG-42", "Todo", ["frontend"])
    assert i.updated_at == datetime(2026, 10, 2, 8, 0)


async def test_linear_graphql_errors_surface(monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(200, json={"errors": [{"message": "Authentication required"}]}))
    with pytest.raises(ti.TrackerImportError, match="Authentication required"):
        await ti.linear_search(None, "bad", None)


async def test_linear_get_issues_by_identifier(monkeypatch):
    calls = _transport(monkeypatch, lambda r: httpx.Response(200, json={"data": {"issue": LINEAR_NODE}}))
    out = await ti.linear_get_issues(None, "k", ["eng-42"])
    assert json.loads(calls[0].content)["variables"] == {"id": "ENG-42"}
    assert out[0].key == "ENG-42"
    with pytest.raises(ti.TrackerImportError):
        await ti.linear_get_issues(None, "k", ["not a key"])


async def test_linear_unsafe_issue_url_is_dropped(monkeypatch):
    node = {**LINEAR_NODE, "url": "javascript:alert(1)"}
    _transport(monkeypatch, lambda r: httpx.Response(200, json={"data": {"issues": {"nodes": [node]}}}))
    [i] = await ti.linear_search(None, "k", None)
    assert i.url is None


async def test_dispatch_rejects_other_providers():
    with pytest.raises(ti.TrackerImportError):
        await ti.search("github", "https://api.github.com", None, "t")
    with pytest.raises(ti.TrackerImportError, match="JQL"):
        await ti.search("jira", "https://a.atlassian.net", "u", "t", jql="  ")


def test_linear_filter_passes_raw_and_merges():
    assert ti.linear_filter() is None
    assert ti.linear_filter(raw={"priority": {"eq": 1}}, label="Bug", state="Todo") == {
        "priority": {"eq": 1}, "labels": {"name": {"eqIgnoreCase": "Bug"}},
        "state": {"name": {"eqIgnoreCase": "Todo"}}}
