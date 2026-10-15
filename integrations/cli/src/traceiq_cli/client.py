"""Minimal TraceIQ REST client over urllib (X-API-Key auth)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from . import __version__

# (method, url, headers, body) -> (status, body bytes). Swappable for tests.
Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
                     timeout: float = 120.0) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL is the configured server
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Client:
    def __init__(self, base_url: str, api_key: str, transport: Optional[Transport] = None,
                 agent_id: str = "traceiq-cli"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport or urllib_transport
        self.agent_id = agent_id

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        url = f"{self.base_url}/api{path}"
        if params:
            pairs = []
            for k, v in params.items():
                if v is None:
                    continue
                for item in (v if isinstance(v, (list, tuple)) else [v]):
                    pairs.append((k, str(item)))
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        return url

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                body: Any = None, raw: bool = False) -> Any:
        headers = {"X-API-Key": self.api_key, "X-Agent-Id": self.agent_id,
                   "User-Agent": f"traceiq-cli/{__version__}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, payload = self.transport(method, self._url(path, params), headers, data)
        if status >= 400:
            detail = payload.decode("utf-8", "replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail", detail) if isinstance(parsed, dict) else detail
            except ValueError:
                pass
            raise ApiError(status, str(detail)[:500])
        if raw:
            return payload
        return json.loads(payload.decode("utf-8")) if payload else None

    # --- endpoints -------------------------------------------------------
    def version(self) -> Dict[str, Any]:
        return self.request("GET", "/version")

    def create_run(self, suite_id: int, browser: str, body: Dict[str, Any]) -> Any:
        return self.request("POST", "/runs", {"suite_id": suite_id, "browser": browser}, body)

    def get_run(self, run_id: int) -> Dict[str, Any]:
        return self.request("GET", f"/runs/{run_id}")

    def quality_gate(self, project_id: int, commit: Optional[str] = None,
                     branch: Optional[str] = None) -> Dict[str, Any]:
        params = {"git_commit": commit} if commit else ({"git_branch": branch} if branch else None)
        return self.request("GET", f"/projects/{project_id}/quality-gate", params)

    def bundle(self, run_id: int, result_id: int) -> bytes:
        return self.request("GET", f"/runs/{run_id}/results/{result_id}/bundle", raw=True)

    def export_suite(self, suite_id: int) -> Dict[str, Any]:
        return self.request("GET", f"/suites/{suite_id}/sync")

    def import_suite(self, suite_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", f"/suites/{suite_id}/sync", body=body)
