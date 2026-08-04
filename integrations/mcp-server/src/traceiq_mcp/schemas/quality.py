"""Quality gate, run report, analytics, triage, flakes, heal, comparison,
external (JUnit) results."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class QualityTrendPoint(TQModel):
    date: str
    runs: int = 0
    passed_runs: int = 0
    pass_rate: float = 0.0


class QualitySnapshot(TQModel):
    project_id: int
    window_days: int = 0
    total_runs: int = 0
    finished_runs: int = 0
    passed_runs: int = 0
    failed_runs: int = 0
    pass_rate: float = 0.0
    trend: List[QualityTrendPoint] = []
    flaky_tests: int = 0
    quarantined_tests: int = 0
    monitors_total: int = 0
    monitors_up: int = 0
    monitors_down: int = 0
    down_monitor_names: List[str] = []
    security_findings: Dict[str, int] = {}


class QualityGateCheck(TQModel):
    name: str
    passed: bool
    actual: str
    threshold: str
    detail: Optional[str] = None


class QualityGateResult(TQModel):
    project_id: int
    passed: bool
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    evaluated_run_ids: List[int] = []
    checks: List[QualityGateCheck] = []


class ReportTestResult(TQModel):
    test_name: str
    status: str
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    trace_url: Optional[str] = None


class RunReport(TQModel):
    """Consolidated per-run report; `markdown` is ready to paste into a PR."""
    run_id: int
    project_id: Optional[int] = None
    status: str
    suite_name: Optional[str] = None
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    duration_ms: Optional[float] = None
    results: List[ReportTestResult] = []
    security: Dict[str, int] = {}
    git: Optional[Dict[str, Any]] = None
    gate: Optional[QualityGateResult] = None
    markdown: str = ""


class EffectivenessRow(TQModel):
    test_name: str
    runs: int = 0
    failures: int = 0
    failure_rate: float = 0.0
    clusters_surfaced: int = 0
    avg_duration_ms: Optional[float] = None


class EffectivenessList(TQModel):
    items: List[EffectivenessRow] = []


class FailureCluster(TQModel):
    id: int
    project_id: int
    signature: Optional[str] = None
    title: str
    category: str
    status: str
    occurrence_count: int = 0
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    last_run_id: Optional[int] = None
    sample_error: Optional[str] = None
    resolution_note: Optional[str] = None


class FailureOccurrence(TQModel):
    result_id: int
    run_id: int
    test_name: str
    status: str
    created_at: Optional[datetime] = None


class FailureClusterDetail(FailureCluster):
    occurrences: List[FailureOccurrence] = []


class FailureClusterList(TQModel):
    items: List[FailureCluster] = []


class FlakeRecordRead(TQModel):
    id: int
    test_case_id: int
    step_id: Optional[str] = None
    flake_score: float = 0.0
    sample_count: int = 0
    is_quarantined: bool = False
    last_failure_message: Optional[str] = None
    last_observed_at: Optional[datetime] = None


class FlakeList(TQModel):
    items: List[FlakeRecordRead] = []


class HealProposal(TQModel):
    id: int
    test_case_id: int
    step_id: str
    old_selector: Optional[str] = None
    new_selector: str
    intent: Optional[str] = None
    confidence: float = 0.0
    rationale: Optional[str] = None
    source_run_id: Optional[int] = None
    status: str
    created_at: Optional[datetime] = None


class HealProposalList(TQModel):
    items: List[HealProposal] = []


class ComparisonDelta(TQModel):
    test_name: str
    baseline_status: Optional[str] = None
    candidate_status: Optional[str] = None
    baseline_duration_ms: Optional[float] = None
    candidate_duration_ms: Optional[float] = None
    regressed: bool = False
    recovered: bool = False


class ComparisonResult(TQModel):
    baseline_run_id: int
    candidate_run_id: int
    target_url: Optional[str] = None
    summary: Dict[str, int] = {}
    deltas: List[ComparisonDelta] = []


class ExternalReport(TQModel):
    id: int
    project_id: int
    source: str = "junit"
    suite_name: Optional[str] = None
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    time_seconds: float = 0.0
    failed_cases: Optional[List[Dict[str, Any]]] = None
    uploaded_by: Optional[str] = None
    created_at: Optional[datetime] = None


class ExternalReportList(TQModel):
    items: List[ExternalReport] = []
