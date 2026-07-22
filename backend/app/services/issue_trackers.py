"""Issue-tracker provider abstraction (Jira, iTop, GitHub Issues).

Mirrors the LLM-provider pattern: one interface, pluggable backends, selected by
config. Each provider can `create_issue` and (where supported) `attach_file`, so
TraceIQ can file a ticket from a failing run and upload its trace/video/
screenshots. Payload builders are pure (unit-tested); the HTTP calls need a live
tracker instance.
"""
import base64
import json
from typing import Any, Dict, Optional

import requests


class IssueTrackerError(Exception):
    pass


class BaseProvider:
    supports_attachments: bool = True

    def create_issue(self, summary: str, description: str, priority: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def attach_file(self, filename: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Jira (REST v2). Basic auth = email:api_token.
# ---------------------------------------------------------------------------
class JiraProvider(BaseProvider):
    def __init__(self, base_url: str, email: Optional[str], token: str,
                 project_key: str, issue_type: str = "Bug"):
        self.base = base_url.rstrip("/")
        self.auth = (email or "", token)
        self.project_key = project_key
        self.issue_type = issue_type or "Bug"
        self._issue_key: Optional[str] = None

    def build_issue_payload(self, summary: str, description: str, priority: Optional[str] = None) -> dict:
        fields: Dict[str, Any] = {
            "project": {"key": self.project_key},
            "summary": summary,
            "description": description or "",
            "issuetype": {"name": self.issue_type},
        }
        if priority:
            fields["priority"] = {"name": priority}
        return {"fields": fields}

    def create_issue(self, summary, description, priority=None):
        if not self.project_key:
            raise IssueTrackerError("Jira config missing settings.project_key")
        r = requests.post(f"{self.base}/rest/api/2/issue",
                          json=self.build_issue_payload(summary, description, priority),
                          auth=self.auth, timeout=30)
        if not r.ok:
            raise IssueTrackerError(f"Jira create failed: {r.status_code} {r.text[:500]}")
        key = r.json().get("key")
        self._issue_key = key
        return {"key": key, "url": f"{self.base}/browse/{key}"}

    def attach_file(self, filename, content, content_type):
        r = requests.post(f"{self.base}/rest/api/2/issue/{self._issue_key}/attachments",
                          headers={"X-Atlassian-Token": "no-check"}, auth=self.auth,
                          files={"file": (filename, content, content_type)}, timeout=120)
        if not r.ok:
            raise IssueTrackerError(f"Jira attach failed: {r.status_code} {r.text[:300]}")


# ---------------------------------------------------------------------------
# iTop (REST/JSON API). Attachments are a linked Attachment object (base64).
# ---------------------------------------------------------------------------
class ItopProvider(BaseProvider):
    def __init__(self, base_url: str, user: Optional[str], password: str,
                 itop_class: str = "UserRequest", org_id: Optional[Any] = None):
        self.base = base_url.rstrip("/")
        self.user = user or ""
        self.password = password
        self.cls = itop_class or "UserRequest"
        self.org_id = org_id
        self._item_id: Optional[str] = None

    def _post(self, json_data: dict) -> dict:
        data = {"version": "1.3", "auth_user": self.user, "auth_pwd": self.password,
                "json_data": json.dumps(json_data)}
        r = requests.post(f"{self.base}/webservices/rest.php", data=data, timeout=30)
        if not r.ok:
            raise IssueTrackerError(f"iTop HTTP {r.status_code}: {r.text[:300]}")
        res = r.json()
        if res.get("code", 0) != 0:
            raise IssueTrackerError(f"iTop error: {res.get('message')}")
        return res

    def build_create_payload(self, summary: str, description: str) -> dict:
        fields: Dict[str, Any] = {"title": summary, "description": description or ""}
        if self.org_id is not None:
            fields["org_id"] = self.org_id
        return {"operation": "core/create", "class": self.cls, "fields": fields,
                "comment": "Created by TraceIQ", "output_fields": "id,ref,friendlyname"}

    def create_issue(self, summary, description, priority=None):
        res = self._post(self.build_create_payload(summary, description))
        objs = res.get("objects") or {}
        key = None
        item_id = None
        for k, v in objs.items():
            f = (v or {}).get("fields", {})
            key = f.get("ref") or f.get("friendlyname") or v.get("key")
            item_id = v.get("key") or k.split("::")[-1]
        self._item_id = item_id
        url = f"{self.base}/pages/UI.php?operation=details&class={self.cls}&id={item_id}"
        return {"key": str(key or item_id), "url": url}

    def attach_file(self, filename, content, content_type):
        fields = {"item_class": self.cls, "item_id": self._item_id,
                  "contents": {"data": base64.b64encode(content).decode(),
                               "filename": filename, "mimetype": content_type}}
        self._post({"operation": "core/create", "class": "Attachment", "fields": fields,
                    "comment": "TraceIQ artifact", "output_fields": "id"})


# ---------------------------------------------------------------------------
# GitHub Issues. The REST API has no attachment endpoint, so artifacts are
# linked (signed URLs) in the body instead of uploaded.
# ---------------------------------------------------------------------------
class GithubProvider(BaseProvider):
    supports_attachments = False

    def __init__(self, base_url: Optional[str], token: str, repo: str):
        self.base = (base_url or "https://api.github.com").rstrip("/")
        self.token = token
        self.repo = repo

    def build_issue_payload(self, summary: str, description: str) -> dict:
        return {"title": summary, "body": description or ""}

    def create_issue(self, summary, description, priority=None):
        if not self.repo:
            raise IssueTrackerError("GitHub config missing settings.repo (owner/name)")
        r = requests.post(f"{self.base}/repos/{self.repo}/issues",
                          json=self.build_issue_payload(summary, description),
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
        if not r.ok:
            raise IssueTrackerError(f"GitHub create failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        return {"key": f"#{data.get('number')}", "url": data.get("html_url")}


def get_provider(provider: str, base_url: str, auth_user: Optional[str],
                 secret: str, settings: Optional[dict]) -> BaseProvider:
    settings = settings or {}
    provider = (provider or "").lower()
    if provider == "jira":
        return JiraProvider(base_url, auth_user, secret,
                            settings.get("project_key", ""), settings.get("issue_type", "Bug"))
    if provider == "itop":
        return ItopProvider(base_url, auth_user, secret,
                            settings.get("class", "UserRequest"), settings.get("org_id"))
    if provider == "github":
        return GithubProvider(base_url, secret, settings.get("repo", ""))
    raise IssueTrackerError(f"Unknown issue-tracker provider: {provider!r}")
