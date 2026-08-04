"""Runs, per-case results, failure analysis."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class RunRef(TQModel):
    """A run as returned by run-creating endpoints."""
    id: int
    status: str
    suite_name: Optional[str] = None
    test_case_name: Optional[str] = None
    browser: Optional[str] = None
    device: Optional[str] = None
    environment_id: Optional[int] = None
    local_worker_id: Optional[str] = None
    app_build_id: Optional[int] = None


class RunRefList(TQModel):
    runs: List[RunRef] = []


class CaseResult(TQModel):
    id: Optional[int] = None
    test_name: str
    test_case_id: Optional[int] = None
    status: str
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    har_url: Optional[str] = None
    screenshots: List[str] = []
    result_kind: Optional[str] = None
    result_payload: Optional[Dict[str, Any]] = None


class RunDetail(TQModel):
    id: int
    status: str
    suite_name: Optional[str] = None
    test_case_name: Optional[str] = None
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    browser: Optional[str] = None
    device: Optional[str] = None
    created_at: Optional[datetime] = None
    finalized_at: Optional[datetime] = None
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    git_pr_url: Optional[str] = None
    git_repo: Optional[str] = None
    triggered_by: Optional[str] = None
    agent_id: Optional[str] = None
    local_worker_id: Optional[str] = None
    environment_id: Optional[int] = None
    app_build_id: Optional[int] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    har_url: Optional[str] = None
    results: List[CaseResult] = []


class RunResults(TQModel):
    run_id: int
    status: str
    results: List[CaseResult] = []


class FailureAnalysis(TQModel):
    """The run-level AI failure analysis plus the failed results it covers.

    `analysis` is the persisted LLM output — its exact keys vary by provider
    and analysis version, so it stays a free dict."""
    run_id: int
    status: str
    analysis: Dict[str, Any] = {}
    failed_results: List[CaseResult] = []
