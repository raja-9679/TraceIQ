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
    data_seed: Optional[str] = None


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
    # Effective {{fake.*}} seed; pass to run_suite(data_seed=…) to replay.
    data_seed: Optional[str] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    har_url: Optional[str] = None
    results: List[CaseResult] = []


class RunResults(TQModel):
    run_id: int
    status: str
    results: List[CaseResult] = []


class AnalysisEvidenceItem(TQModel):
    """A citable evidence item: `s3` = step 4, `s3.c0` its first console
    line, `s3.r1` its second failed request, `s3.dom`, `s3.shot`, `error`,
    `run:<id>` a prior run; `console.<n>`/`net.<n>` on older results."""
    id: str
    kind: str
    label: str = ""
    step_index: Optional[int] = None


class AnalysisObservation(TQModel):
    """A fact (not an interpretation) with the evidence it came from."""
    text: str
    evidence_refs: List[str] = []


class AnalysisHypothesis(TQModel):
    category: str = "unknown"
    fix_target: str = "none"
    summary: str = ""
    confidence: float = 0.0
    evidence_refs: List[str] = []
    source: str = "heuristic"


class AnalysisHistory(TQModel):
    runs_considered: int = 0
    prior_failures: int = 0
    failing_streak: int = 1
    first_failing_run_id: Optional[int] = None
    first_failing_commit: Optional[str] = None
    last_pass_run_id: Optional[int] = None
    last_pass_commit: Optional[str] = None
    flip_count: int = 0
    cluster: Optional[Dict[str, Any]] = None
    entries: List[Dict[str, Any]] = []


class AnalysisChangeRange(TQModel):
    from_commit: str
    to_commit: str
    last_pass_run_id: int
    repo: Optional[str] = None
    compare_url: Optional[str] = None


class CaseFailureReport(TQModel):
    """One failed case's report. v1 fields are always present; the v2
    investigation fields (observed, ranked hypotheses with evidence_refs,
    history, changed_since_last_pass) are empty on reports written before
    schema_version 2."""
    schema_version: int = 1
    result_id: Optional[int] = None
    test_name: Optional[str] = None
    root_cause_category: str = "unknown"
    summary: str = ""
    details: str = ""
    suggested_fix: Optional[str] = None
    fix_target: str = "none"
    confidence: float = 0.0
    analyzed_by: Optional[str] = None
    observed: List[AnalysisObservation] = []
    hypotheses: List[AnalysisHypothesis] = []
    evidence_items: List[AnalysisEvidenceItem] = []
    history: Optional[AnalysisHistory] = None
    changed_since_last_pass: Optional[AnalysisChangeRange] = None
    dropped_citations: int = 0


class FailureAnalysis(TQModel):
    """The run-level AI failure analysis plus the failed results it covers.

    `analysis` is the persisted output as stored (kept for compatibility);
    `reports` is the same per-case content typed. Branch on
    `reports[i].hypotheses[0]` (ranked, best first) and resolve each
    `evidence_refs` id via `reports[i].evidence_items`."""
    run_id: int
    status: str
    analysis: Dict[str, Any] = {}
    reports: List[CaseFailureReport] = []
    failed_results: List[CaseResult] = []
