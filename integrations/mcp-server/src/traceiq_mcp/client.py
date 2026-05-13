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
        agent_session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TRACEIQ_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("TRACEIQ_API_KEY", "")
        self.agent_id = agent_id or os.environ.get("TRACEIQ_AGENT_ID")
        # Phase E: per-session identifier. Lets the server tell creates from
        # the same agent session apart from older creates. Default: mint a
        # fresh UUID per client instance — one per Claude Code conversation
        # is the typical lifespan.
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
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "TraceIQ-MCP/0.1.0",
            "X-Agent-Session-Id": self.agent_session_id,
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

    # ------------------------------------------------------------------
    # Phase D — agent ownership tools
    # ------------------------------------------------------------------

    async def list_suites(self, project_id: int) -> List[Dict[str, Any]]:  # noqa: F811 (override)
        return await self._request("GET", "/api/suites", params={"project_id": project_id})

    async def create_suite(
        self,
        project_id: int,
        name: str,
        parent_id: Optional[int] = None,
        execution_mode: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "name": name,
            "project_id": project_id,
            "parent_id": parent_id,
            "execution_mode": execution_mode,
            "description": description,
        }
        body = {k: v for k, v in body.items() if v is not None}
        return await self._request("POST", "/api/suites", json=body)

    async def update_suite(self, suite_id: int, **fields: Any) -> Dict[str, Any]:
        return await self._request("PATCH", f"/api/suites/{suite_id}", json=fields)

    async def move_suite(self, suite_id: int, new_parent_id: Optional[int]) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/api/suites/{suite_id}/move", json={"parent_id": new_parent_id}
        )

    async def delete_suite(self, suite_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/api/suites/{suite_id}")

    async def list_cases(self, project_id: int, test_suite_id: Optional[int] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"project_id": project_id}
        if test_suite_id is not None:
            params["test_suite_id"] = test_suite_id
        return await self._request("GET", "/api/cases", params=params)

    async def update_case(self, case_id: int, **fields: Any) -> Dict[str, Any]:
        return await self._request("PATCH", f"/api/cases/{case_id}", json=fields)

    async def delete_case(self, case_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/api/cases/{case_id}")

    async def get_run_history(self, case_id: int, limit: int = 30) -> Dict[str, Any]:
        return await self._request("GET", f"/api/cases/{case_id}/run-history", params={"limit": limit})

    async def discover_app_surface(self, project_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/apps/{project_id}/surface")

    async def select_tests_for_diff(
        self,
        project_id: int,
        changed_files: List[str],
        include_no_code_paths: bool = False,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/runs/impact-analysis",
            json={
                "project_id": project_id,
                "changed_files": changed_files,
                "include_no_code_paths": include_no_code_paths,
            },
        )

    async def propose_case(
        self,
        project_id: int,
        action: str,
        test_suite_id: Optional[int] = None,
        target_case_id: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
        rationale: Optional[str] = None,
        ai_confidence: float = 0.0,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/case-proposals",
            json={
                "project_id": project_id,
                "action": action,
                "test_suite_id": test_suite_id,
                "target_case_id": target_case_id,
                "payload": payload or {},
                "rationale": rationale,
                "ai_confidence": ai_confidence,
            },
        )

    async def generate_case_proposal(
        self,
        description: str,
        test_suite_id: int,
        target_url: Optional[str] = None,
        case_name: Optional[str] = None,
        code_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/cases/generate",
            json={
                "description": description,
                "test_suite_id": test_suite_id,
                "target_url": target_url,
                "case_name": case_name,
                "code_paths": code_paths,
                "mode": "propose",
            },
        )

    # ------------------------------------------------------------------
    # Phase E — read, write, bulk, reference
    # ------------------------------------------------------------------

    async def list_cases(self, project_id: int, test_suite_id: Optional[int] = None) -> List[Dict[str, Any]]:  # noqa: F811
        params: Dict[str, Any] = {"project_id": project_id}
        if test_suite_id is not None:
            params["test_suite_id"] = test_suite_id
        return await self._request("GET", "/api/cases", params=params)

    async def get_case(self, case_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/cases/{case_id}")

    async def get_suite(self, suite_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/api/suites/{suite_id}")

    async def list_case_proposals(
        self,
        project_id: Optional[int] = None,
        status: Optional[str] = "pending",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if project_id is not None:
            params["project_id"] = project_id
        if status is not None:
            params["status"] = status
        return await self._request("GET", "/api/case-proposals", params=params)

    async def describe_step_types(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/step-types")

    async def get_authoring_guide(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/agent-guide")

    async def delete_suite(self, suite_id: int) -> Dict[str, Any]:  # noqa: F811
        return await self._request("DELETE", f"/api/suites/{suite_id}")

    async def bulk_propose_cases(
        self,
        project_id: int,
        proposals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/cases/bulk-propose",
            json={"project_id": project_id, "proposals": proposals},
        )

    async def bulk_set_code_paths(
        self,
        project_id: int,
        mapping: Dict[int, List[str]],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/cases/bulk-set-code-paths",
            json={"project_id": project_id, "mapping": mapping},
        )
