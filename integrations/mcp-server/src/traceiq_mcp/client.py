"""Thin async HTTP client wrapping the TraceIQ REST API.

Deliberately dumb: one method per backend route, dict/list in, dict/list out.
Typing and validation live in the tool layer (schemas/*), so a backend field
addition never breaks this file.

v2 fixes over the original client:
  - list_cases now calls the real GET /api/cases (the route was added for v2;
    the old client called a nonexistent endpoint).
  - set_code_paths routes through POST /api/cases/bulk-set-code-paths — the
    single auditable write path for code_paths (the old client PATCHed a
    route that never existed).
  - run_suite forwards tags / local_worker_id / app_build_id.
  - dead update_case/update_suite/move_suite methods removed.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

# Keys whose values are credentials and must never appear in an error message.
# FastAPI 422 validation errors echo the submitted body back, so a bad request
# to propose_update_suite_settings (headers: {Authorization: Bearer ...}) or
# start_project_security_scan (auth_header_value) would otherwise round-trip a
# live token into the tool result.
_CREDENTIAL_KEYS = frozenset({
    "authorization", "auth_header_value", "api_key", "secret", "secrets",
    "password", "token", "headers",
})
_MAX_ERROR_BODY = 600


def _redact(obj: Any) -> Any:
    """Recursively replace credential-keyed values with '***'."""
    if isinstance(obj, dict):
        return {
            k: ("***" if isinstance(k, str) and k.lower() in _CREDENTIAL_KEYS
                else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _safe_error_body(text: str, limit: int = _MAX_ERROR_BODY) -> str:
    """Sanitize a backend response body for inclusion in an exception message:
    redact credential-keyed values if it's JSON, then truncate. Falls back to a
    plain truncation when the body isn't JSON."""
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return text[:limit]
    redacted = json.dumps(_redact(parsed))
    return redacted[:limit]


class TraceIQClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TRACEIQ_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("TRACEIQ_API_KEY", "")
        self.agent_id = agent_id or os.environ.get("TRACEIQ_AGENT_ID")
        # Per-session identifier: lets the server tell creates from this agent
        # session apart from older ones. One per Claude Code conversation is
        # the typical lifespan.
        import uuid as _uuid
        self.agent_session_id = (
            agent_session_id
            or os.environ.get("TRACEIQ_AGENT_SESSION_ID")
            or str(_uuid.uuid4())
        )
        if not self.base_url or not self.api_key:
            raise RuntimeError(
                "TraceIQClient: TRACEIQ_BASE_URL and TRACEIQ_API_KEY must be set"
            )
        if urlparse(self.base_url).scheme == "http":
            logger.warning(
                "TRACEIQ_BASE_URL uses http:// — the API key is sent in "
                "cleartext on every request; use https:// in production.")
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "User-Agent": "TraceIQ-MCP/0.2.0",
            "X-Agent-Session-Id": self.agent_session_id,
        }
        # Workspace API keys are `tiq_...` and authenticate via X-API-Key; the
        # backend rejects non-`tiq_` values there. Anything else is treated as
        # a JWT and forwarded as a Bearer token so HTTP callers can pass one.
        if self.api_key.startswith("tiq_"):
            h["X-API-Key"] = self.api_key
        else:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.agent_id:
            h["X-Agent-Id"] = self.agent_id
        return h

    async def _request(self, method: str, path: str, **kw) -> Any:
        req_timeout = kw.pop("timeout", self._timeout)
        headers = {**self._headers(), **kw.pop("headers", {})}
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            resp = await client.request(
                method, f"{self.base_url}{path}", headers=headers, **kw)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"TraceIQ {method} {path} failed: {resp.status_code} "
                    f"{_safe_error_body(resp.text)}",
                    request=resp.request,
                    response=resp,
                )
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return resp.text

    @staticmethod
    def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in d.items() if v is not None}

    # ------------------------------------------------------------------
    # Core: workspaces / projects / suites / cases
    # ------------------------------------------------------------------

    async def list_workspaces(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/workspaces")

    async def create_project(self, workspace_id: int, name: str,
                             description: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/projects",
            json={"workspace_id": workspace_id, "name": name, "description": description})

    async def list_projects(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/projects")

    async def list_suites(self, project_id: int) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/suites", params={"project_id": project_id})

    async def get_suite(self, suite_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/suites/{suite_id}")

    async def create_suite(self, project_id: int, name: str,
                           parent_id: Optional[int] = None,
                           execution_mode: Optional[str] = None,
                           description: Optional[str] = None,
                           settings: Optional[Dict[str, Any]] = None,
                           inherit_settings: Optional[bool] = None) -> Dict[str, Any]:
        return await self._request("POST", "/api/suites", json=self._clean({
            "name": name, "project_id": project_id, "parent_id": parent_id,
            "execution_mode": execution_mode, "description": description,
            "settings": settings, "inherit_settings": inherit_settings}))

    async def delete_suite(self, suite_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/api/suites/{suite_id}")

    async def list_cases(self, project_id: int,
                         test_suite_id: Optional[int] = None,
                         tag: Optional[str] = None,
                         limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        return await self._request("GET", "/api/cases", params=self._clean({
            "project_id": project_id, "test_suite_id": test_suite_id,
            "tag": tag, "limit": limit, "offset": offset}))

    async def get_case(self, case_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/cases/{case_id}")

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(
        self,
        suite_id: int,
        case_id: Optional[int] = None,
        browser: Optional[List[str]] = None,
        device: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        git_commit: Optional[str] = None,
        git_branch: Optional[str] = None,
        git_pr_url: Optional[str] = None,
        git_repo: Optional[str] = None,
        triggered_by: Optional[str] = None,
        environment_id: Optional[int] = None,
        local_worker_id: Optional[str] = None,
        app_build_id: Optional[int] = None,
    ) -> Any:
        params: Dict[str, Any] = {"suite_id": suite_id}
        if case_id is not None:
            params["case_id"] = case_id
        if browser:
            params["browser"] = browser
        if device:
            params["device"] = device
        if tags:
            params["tags"] = tags
        body = self._clean({
            "git_commit": git_commit, "git_branch": git_branch,
            "git_pr_url": git_pr_url, "git_repo": git_repo,
            "triggered_by": triggered_by, "agent_id": self.agent_id,
            "environment_id": environment_id,
            "local_worker_id": local_worker_id,
            "app_build_id": app_build_id,
        })
        return await self._request("POST", "/api/runs", params=params, json=body)

    async def get_run(self, run_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/runs/{run_id}")

    async def get_artifact_url(self, object_path: str) -> Dict[str, Any]:
        # Artifact object keys look like `runs/{run_id}/screenshots/…` — slashes
        # are legitimate, but a `..` segment or a leading `/` would (after httpx
        # normalizes the URL) escape /api/artifacts/ and reach other backend
        # routes. Reject those, then percent-encode everything else so a key
        # can't smuggle in query/fragment or additional path segments.
        path = object_path or ""
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(
                f"Invalid artifact object_path {object_path!r}: must be a "
                "relative key with no '..' segments (e.g. "
                "'runs/123/screenshots/foo.png').")
        return await self._request(
            "GET", f"/api/artifacts/{quote(path, safe='/')}")

    async def analyze_run(self, run_id: int,
                          provider_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/api/runs/{run_id}/analyze",
            json=self._clean({"provider_id": provider_id}))

    async def get_run_report(self, run_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/runs/{run_id}/report")

    # ------------------------------------------------------------------
    # Impact analysis / discovery / code paths
    # ------------------------------------------------------------------

    async def impact_analysis(self, project_id: int, changed_files: List[str],
                              include_no_code_paths: bool = False) -> Dict[str, Any]:
        return await self._request("POST", "/api/runs/impact-analysis", json={
            "project_id": project_id, "changed_files": changed_files,
            "include_no_code_paths": include_no_code_paths})

    async def app_surface(self, project_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/apps/{project_id}/surface")

    async def crawl_app_surface(self, project_id: int, base_url: str,
                                max_pages: int = 10) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/api/apps/{project_id}/discover",
            json={"base_url": base_url, "max_pages": max_pages},
            timeout=120.0)

    async def run_history(self, case_id: int, limit: int = 30) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/api/cases/{case_id}/run-history", params={"limit": limit})

    async def bulk_set_code_paths(self, project_id: int,
                                  mapping: Dict[int, List[str]]) -> Dict[str, Any]:
        return await self._request("POST", "/api/cases/bulk-set-code-paths", json={
            "project_id": project_id,
            "mapping": {str(k): v for k, v in mapping.items()}})

    # ------------------------------------------------------------------
    # Authoring: proposals / generation / reference
    # ------------------------------------------------------------------

    async def propose_case(self, project_id: int, action: str,
                           test_suite_id: Optional[int] = None,
                           target_case_id: Optional[int] = None,
                           payload: Optional[Dict[str, Any]] = None,
                           rationale: Optional[str] = None,
                           ai_confidence: float = 0.0) -> Dict[str, Any]:
        return await self._request("POST", "/api/case-proposals", json={
            "project_id": project_id, "action": action,
            "test_suite_id": test_suite_id, "target_case_id": target_case_id,
            "payload": payload or {}, "rationale": rationale,
            "ai_confidence": ai_confidence})

    async def list_case_proposals(self, project_id: Optional[int] = None,
                                  status: Optional[str] = "pending",
                                  limit: int = 100) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/case-proposals", params=self._clean({
            "project_id": project_id, "status": status, "limit": limit}))

    async def generate_case_proposal(self, description: str, test_suite_id: int,
                                     target_url: Optional[str] = None,
                                     case_name: Optional[str] = None,
                                     code_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        return await self._request("POST", "/api/cases/generate", json={
            "description": description, "test_suite_id": test_suite_id,
            "target_url": target_url, "case_name": case_name,
            "code_paths": code_paths, "mode": "propose"})

    async def bulk_propose_cases(self, project_id: int,
                                 proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await self._request("POST", "/api/cases/bulk-propose", json={
            "project_id": project_id, "proposals": proposals})

    async def describe_step_types(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/step-types")

    async def get_authoring_guide(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/agent-guide")

    # ------------------------------------------------------------------
    # Quality & results
    # ------------------------------------------------------------------

    async def quality_snapshot(self, project_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/projects/{project_id}/quality")

    async def quality_gate(self, project_id: int,
                           git_commit: Optional[str] = None,
                           git_branch: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/api/projects/{project_id}/quality-gate",
            params=self._clean({"git_commit": git_commit, "git_branch": git_branch}))

    async def test_effectiveness(self, project_id: int, days: int = 30,
                                 limit: int = 100) -> List[Dict[str, Any]]:
        return await self._request(
            "GET", f"/api/analytics/projects/{project_id}/test-effectiveness",
            params={"days": days, "limit": limit})

    async def list_failure_clusters(self, project_id: int,
                                    status: Optional[str] = None,
                                    category: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._request(
            "GET", f"/api/projects/{project_id}/failure-clusters",
            params=self._clean({"status": status, "category": category}))

    async def get_failure_cluster(self, cluster_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/failure-clusters/{cluster_id}")

    async def list_flakes(self, project_id: Optional[int] = None,
                          test_case_id: Optional[int] = None,
                          quarantined_only: bool = False) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/flakes", params=self._clean({
            "project_id": project_id, "test_case_id": test_case_id,
            "quarantined_only": quarantined_only or None}))

    async def list_heal_proposals(self, status: Optional[str] = "pending",
                                  test_case_id: Optional[int] = None,
                                  project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/heal-proposals", params=self._clean({
            "status": status, "test_case_id": test_case_id,
            "project_id": project_id}))

    async def create_comparison_run(self, baseline_run_id: int, target_url: str,
                                    browser: Optional[str] = None,
                                    device: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", "/api/runs/comparison", json=self._clean({
            "baseline_run_id": baseline_run_id, "target_url": target_url,
            "browser": browser, "device": device}))

    async def get_comparison(self, run_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/runs/{run_id}/comparison")

    async def ingest_junit(self, project_id: int, junit_xml: str,
                           git_commit: Optional[str] = None,
                           git_branch: Optional[str] = None,
                           suite: Optional[str] = None) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/api/projects/{project_id}/external-results",
            params=self._clean({"git_commit": git_commit,
                                "git_branch": git_branch, "suite": suite}),
            content=junit_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"})

    async def list_external_results(self, project_id: int,
                                    git_commit: Optional[str] = None,
                                    limit: int = 20) -> List[Dict[str, Any]]:
        return await self._request(
            "GET", f"/api/projects/{project_id}/external-results",
            params=self._clean({"git_commit": git_commit, "limit": limit}))

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    async def run_security_scan(self, run_id: int) -> Dict[str, Any]:
        return await self._request("POST", f"/api/runs/{run_id}/security-scan")

    async def get_run_security_findings(self, run_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/runs/{run_id}/security-findings")

    async def start_project_security_scan(
        self, project_id: int, target_url: str, authorized: bool,
        scan_type: str = "baseline", authenticated: bool = False,
        openapi_url: Optional[str] = None,
        auth_header_name: Optional[str] = None,
        auth_header_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/api/projects/{project_id}/security-scan",
            json=self._clean({
                "target_url": target_url, "authorized": authorized,
                "scan_type": scan_type, "authenticated": authenticated,
                "openapi_url": openapi_url,
                "auth_header_name": auth_header_name,
                "auth_header_value": auth_header_value}))

    async def list_security_scans(self, project_id: int) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/api/projects/{project_id}/security-scans")

    async def get_security_scan(self, scan_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/security-scans/{scan_id}")

    async def get_security_scan_diff(self, scan_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/security-scans/{scan_id}/diff")

    # ------------------------------------------------------------------
    # Mobile app builds
    # ------------------------------------------------------------------

    async def list_app_builds(self, project_id: int) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/api/projects/{project_id}/app-builds")

    async def get_app_build(self, build_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/app-builds/{build_id}")


def new_client() -> TraceIQClient:
    """Client for the current call: per-request API key when serving HTTP,
    env-var credentials on stdio."""
    from traceiq_mcp._context import get_request_api_key
    req_key = get_request_api_key()
    if req_key:
        return TraceIQClient(api_key=req_key)
    return TraceIQClient()
