"""Thin async HTTP client wrapping the TraceIQ REST API."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx


class TraceIQClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TRACEIQ_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("TRACEIQ_API_KEY", "")
        self.agent_id = agent_id or os.environ.get("TRACEIQ_AGENT_ID")
        if not self.base_url or not self.api_key:
            raise RuntimeError(
                "TraceIQClient: TRACEIQ_BASE_URL and TRACEIQ_API_KEY must be set"
            )
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "TraceIQ-MCP/0.1.0",
        }
        if self.agent_id:
            h["X-Agent-Id"] = self.agent_id
        return h

    async def _request(self, method: str, path: str, **kw) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                **kw,
            )
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"TraceIQ {method} {path} failed: {resp.status_code} {resp.text}",
                    request=resp.request,
                    response=resp,
                )
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return resp.text

    # ------------------------------------------------------------------
    # API surface used by the MCP tools
    # ------------------------------------------------------------------

    async def list_projects(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/projects")

    async def list_suites(self, project_id: int) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/suites", params={"project_id": project_id})

    async def run_suite(
        self,
        suite_id: int,
        case_id: Optional[int] = None,
        browser: Optional[List[str]] = None,
        device: Optional[List[str]] = None,
        git_commit: Optional[str] = None,
        git_branch: Optional[str] = None,
        git_pr_url: Optional[str] = None,
        git_repo: Optional[str] = None,
        triggered_by: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {"suite_id": suite_id}
        if case_id is not None:
            params["case_id"] = case_id
        if browser:
            params["browser"] = browser
        if device:
            params["device"] = device
        body = {
            "git_commit": git_commit,
            "git_branch": git_branch,
            "git_pr_url": git_pr_url,
            "git_repo": git_repo,
            "triggered_by": triggered_by,
            "agent_id": self.agent_id,
        }
        # Strip None so the server applies defaults.
        body = {k: v for k, v in body.items() if v is not None}
        return await self._request("POST", "/api/runs", params=params, json=body)

    async def get_run(self, run_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/runs/{run_id}")

    async def get_artifact_url(self, object_path: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/artifacts/{object_path}")
