"""Inbound side of the issue-tracker integration: search and fetch tickets.

The outbound side (app/services/issue_trackers.py) files tickets; this module
reads them so they can be imported as requirements. Same connection rows
(`IssueTrackerConfig`, credential Fernet-encrypted), two providers:

* **Jira Cloud** — REST v3 search. Atlassian replaced `GET /rest/api/3/search`
  with `GET /rest/api/3/search/jql` (the old endpoint answers 410 on Cloud), so
  the new one is tried first and the old one is the fallback for older/self-
  managed deployments. Descriptions come back as ADF and are flattened by
  `acceptance_criteria.adf_to_text`. Auth: basic, email + API token.
* **Linear** — GraphQL at `https://api.linear.app/graphql`; personal API keys go
  in `Authorization` *without* a `Bearer` prefix (OAuth tokens need it, and are
  passed through as given).

Every base URL is user-supplied, so each request is validated by net_guard and
redirects are not followed (a 3xx would otherwise skip validation). The HTTP
client is built in one place (`_client`) so tests inject an
`httpx.MockTransport` — these clients are verified against the documented API
shapes, not a live tenant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.core.net_guard import UnsafeUrlError, validate_outbound_url
from app.services.acceptance_criteria import adf_to_text

LINEAR_DEFAULT_URL = "https://api.linear.app/graphql"
IMPORT_PROVIDERS = ("jira", "linear")
MAX_RESULTS = 50

# Tests set this to an httpx.MockTransport.
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


class TrackerImportError(Exception):
    """A tracker call failed. The message is safe to show the user: it never
    carries the credential."""


@dataclass
class ExternalIssue:
    key: str
    title: str
    url: Optional[str] = None
    description: str = ""
    status: Optional[str] = None
    external_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    labels: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {"key": self.key, "title": self.title, "url": self.url,
                "status": self.status, "updated_at": self.updated_at,
                "labels": self.labels}


def safe_link(url: Optional[str]) -> Optional[str]:
    """Only http(s) links are kept — these end up in an <a href> in the UI, and
    the value comes from a third party (a `javascript:` URL would be XSS)."""
    if not url or not isinstance(url, str):
        return None
    return url if re.match(r"^https?://", url.strip(), re.I) else None


def parse_dt(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    v = value.strip().replace("Z", "+00:00")
    # Jira: 2026-10-01T10:00:00.000+0000 (no colon in the offset)
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)   # naive UTC, like every column
    return dt


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=_TRANSPORT)


async def _checked(url: str) -> str:
    try:
        return await validate_outbound_url(url)
    except UnsafeUrlError as exc:
        raise TrackerImportError(f"Tracker URL refused: {exc}") from exc


def _err(provider: str, resp: httpx.Response) -> TrackerImportError:
    if resp.status_code in (401, 403):
        return TrackerImportError(f"{provider} rejected the credentials ({resp.status_code})")
    if 300 <= resp.status_code < 400:
        return TrackerImportError(f"{provider} answered with a redirect ({resp.status_code}); "
                                  "check the base URL")
    return TrackerImportError(f"{provider} returned {resp.status_code}: {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

_JIRA_FIELDS = "summary,description,status,updated,labels"
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def _jira_issue(base: str, raw: dict) -> ExternalIssue:
    f = raw.get("fields") or {}
    key = raw.get("key") or ""
    return ExternalIssue(
        key=key, external_id=str(raw.get("id") or "") or None,
        title=f.get("summary") or key,
        url=f"{base}/browse/{key}" if key else None,
        description=adf_to_text(f.get("description")),
        status=((f.get("status") or {}).get("name")),
        updated_at=parse_dt(f.get("updated")),
        labels=[str(x) for x in (f.get("labels") or [])],
    )


async def jira_search(base_url: str, email: Optional[str], token: str, jql: str,
                      max_results: int = 25) -> List[ExternalIssue]:
    base = (base_url or "").rstrip("/")
    if not base:
        raise TrackerImportError("Jira connection has no base URL")
    max_results = max(1, min(int(max_results or 25), MAX_RESULTS))
    params = {"jql": jql, "maxResults": max_results, "fields": _JIRA_FIELDS}
    auth = (email or "", token)
    async with _client() as client:
        resp = None
        for path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
            url = await _checked(base + path)
            resp = await client.get(url, params=params, auth=auth,
                                    headers={"Accept": "application/json"})
            if resp.status_code not in (404, 410):
                break
        assert resp is not None
        if resp.status_code != 200:
            raise _err("Jira", resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TrackerImportError("Jira returned a non-JSON response") from exc
    return [_jira_issue(base, i) for i in data.get("issues") or [] if isinstance(i, dict)]


async def jira_get_issues(base_url: str, email: Optional[str], token: str,
                          keys: List[str]) -> List[ExternalIssue]:
    keys = [k.strip().upper() for k in keys if k and k.strip()]
    bad = [k for k in keys if not _JIRA_KEY.match(k)]
    if bad:
        # Keys are interpolated into JQL; refuse anything that is not a key.
        raise TrackerImportError(f"Not Jira issue keys: {', '.join(bad[:5])}")
    if not keys:
        return []
    return await jira_search(base_url, email, token,
                             f"key in ({', '.join(keys)})", max_results=len(keys))


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

_LINEAR_FIELDS = """
  id identifier title description url updatedAt
  state { name }
  labels { nodes { name } }
"""
_LINEAR_SEARCH = (
    "query TraceIQIssues($filter: IssueFilter, $first: Int) {"
    "  issues(filter: $filter, first: $first, orderBy: updatedAt) { nodes {"
    + _LINEAR_FIELDS + "} } }")
_LINEAR_ONE = "query TraceIQIssue($id: String!) { issue(id: $id) {" + _LINEAR_FIELDS + "} }"
_LINEAR_KEY = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def linear_filter(*, team_key: Optional[str] = None, query: Optional[str] = None,
                  label: Optional[str] = None, state: Optional[str] = None,
                  raw: Optional[dict] = None) -> Optional[dict]:
    """Build a Linear `IssueFilter` from the simple form fields. A raw filter
    object (the API's own shape) is accepted for anything the form can't say;
    it travels as a GraphQL *variable*, so it cannot alter the query."""
    f: Dict[str, Any] = dict(raw or {})
    if team_key:
        f["team"] = {"key": {"eq": team_key.strip().upper()}}
    if query:
        f["title"] = {"containsIgnoreCase": query.strip()}
    if label:
        f["labels"] = {"name": {"eqIgnoreCase": label.strip()}}
    if state:
        f["state"] = {"name": {"eqIgnoreCase": state.strip()}}
    return f or None


def _linear_issue(raw: dict) -> ExternalIssue:
    return ExternalIssue(
        key=raw.get("identifier") or raw.get("id") or "",
        external_id=raw.get("id"),
        title=raw.get("title") or raw.get("identifier") or "",
        url=safe_link(raw.get("url")),
        description=raw.get("description") or "",
        status=(raw.get("state") or {}).get("name"),
        updated_at=parse_dt(raw.get("updatedAt")),
        labels=[n.get("name") for n in ((raw.get("labels") or {}).get("nodes") or [])
                if isinstance(n, dict) and n.get("name")],
    )


async def _linear(base_url: Optional[str], api_key: str, query: str, variables: dict) -> dict:
    url = await _checked((base_url or "").strip() or LINEAR_DEFAULT_URL)
    if not url.rstrip("/").endswith("/graphql"):
        url = url.rstrip("/") + "/graphql"
        url = await _checked(url)
    async with _client() as client:
        resp = await client.post(url, json={"query": query, "variables": variables},
                                 headers={"Authorization": api_key,
                                          "Content-Type": "application/json"})
    if resp.status_code != 200:
        raise _err("Linear", resp)
    try:
        data = resp.json()
    except ValueError as exc:
        raise TrackerImportError("Linear returned a non-JSON response") from exc
    if data.get("errors"):
        msg = "; ".join(str(e.get("message")) for e in data["errors"] if isinstance(e, dict))
        raise TrackerImportError(f"Linear error: {msg[:300]}")
    return data.get("data") or {}


async def linear_search(base_url: Optional[str], api_key: str, issue_filter: Optional[dict],
                        max_results: int = 25) -> List[ExternalIssue]:
    first = max(1, min(int(max_results or 25), MAX_RESULTS))
    data = await _linear(base_url, api_key, _LINEAR_SEARCH,
                         {"filter": issue_filter, "first": first})
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    return [_linear_issue(n) for n in nodes if isinstance(n, dict)]


async def linear_get_issues(base_url: Optional[str], api_key: str,
                            keys: List[str]) -> List[ExternalIssue]:
    out = []
    for k in [k.strip().upper() for k in keys if k and k.strip()]:
        if not _LINEAR_KEY.match(k):
            raise TrackerImportError(f"Not a Linear issue identifier: {k}")
        data = await _linear(base_url, api_key, _LINEAR_ONE, {"id": k})
        if data.get("issue"):
            out.append(_linear_issue(data["issue"]))
    return out


# ---------------------------------------------------------------------------
# Dispatch on a stored connection
# ---------------------------------------------------------------------------

async def search(provider: str, base_url: str, auth_user: Optional[str], secret: str,
                 *, jql: Optional[str] = None, linear: Optional[dict] = None,
                 max_results: int = 25) -> List[ExternalIssue]:
    if provider == "jira":
        if not (jql or "").strip():
            raise TrackerImportError("A JQL query is required for Jira")
        return await jira_search(base_url, auth_user, secret, jql.strip(), max_results)
    if provider == "linear":
        return await linear_search(base_url, secret, linear_filter(**(linear or {})), max_results)
    raise TrackerImportError(f"Import is supported for Jira and Linear, not {provider!r}")


async def fetch(provider: str, base_url: str, auth_user: Optional[str], secret: str,
                keys: List[str]) -> List[ExternalIssue]:
    if provider == "jira":
        return await jira_get_issues(base_url, auth_user, secret, keys)
    if provider == "linear":
        return await linear_get_issues(base_url, secret, keys)
    raise TrackerImportError(f"Import is supported for Jira and Linear, not {provider!r}")
