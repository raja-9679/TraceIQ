"""Impact analysis v2, discovery, run history, code-paths writes.

These mirror the backend's ImpactAnalysisResponse (app/models.py) — the
contract that answers "I changed these files: which tests should RUN, and
which likely need to be ALTERED first?"
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class ImpactMatch(TQModel):
    """One (changed file ↔ code_paths pattern) hit."""
    file: str
    pattern: str


class ImpactLastResult(TQModel):
    status: str
    run_id: int
    at: Optional[datetime] = None
    git_commit: Optional[str] = None


class ImpactFlake(TQModel):
    flake_score: float = 0.0
    is_quarantined: bool = False


class ImpactedCase(TQModel):
    id: int
    name: str
    test_suite_id: Optional[int] = None
    suite_name: Optional[str] = None
    is_ai_authored: bool = False
    matched_paths: List[str] = []
    matched: List[ImpactMatch] = []
    tags: List[str] = []
    priority: Optional[str] = None
    ai_confidence: Optional[float] = None
    last_human_reviewed_at: Optional[datetime] = None
    last_validated_commit: Optional[str] = None
    last_validated_at: Optional[datetime] = None
    last_result: Optional[ImpactLastResult] = None
    flake: Optional[ImpactFlake] = None
    # "run" — just re-run it. "review" — the case itself likely needs editing
    # first. "run_then_review" — run it, but its code_paths mapping looks
    # stale/too coarse; re-derive after running.
    suggested_action: str = "run"
    reasons: List[str] = []


class ImpactAnalysis(TQModel):
    matched_cases: List[ImpactedCase] = []
    cases_without_code_paths: int = 0
    # Changed files no case covers — candidates for new proposals.
    unmatched_files: List[str] = []


class AppSurface(TQModel):
    """What's already tested in a project. Tree/coverage sub-shapes are
    backend-versioned, so they stay free dicts."""
    project_id: Optional[int] = None
    suites: List[Dict[str, Any]] = []
    routes_covered: List[str] = []
    code_paths_covered: List[str] = []
    recent_runs: List[Dict[str, Any]] = []
    case_counts: Dict[str, Any] = {}


class CrawlResult(TQModel):
    """Mode-2 crawl of a live app: interactable surface per page."""
    status: Optional[str] = None
    discovery_id: Optional[str] = None
    base_url: Optional[str] = None
    pages: List[Dict[str, Any]] = []
    detail: Optional[str] = None


class RunHistoryEntry(TQModel):
    run_id: int
    status: str
    created_at: Optional[str] = None
    duration_ms: Optional[float] = None
    git_commit: Optional[str] = None
    triggered_by: Optional[str] = None
    via_suite_run: Optional[bool] = None
    # "id" — exact link via TestCaseResult.test_case_id; "name" — legacy
    # name matching, can conflate same-named cases.
    matched_by: Optional[str] = None


class RunHistoryCase(TQModel):
    id: int
    name: str
    is_ai_authored: bool = False
    ai_confidence: Optional[float] = None
    last_human_reviewed_at: Optional[str] = None


class RunHistorySummary(TQModel):
    sample_size: int = 0
    passes: int = 0
    failures: int = 0
    last_failure_at: Optional[str] = None


class RunHistory(TQModel):
    case: RunHistoryCase
    history: List[RunHistoryEntry] = []
    summary: RunHistorySummary


class CodePathsItem(TQModel):
    case_id: int
    status: str  # "updated" | "skipped" | "error"
    error: Optional[str] = None


class CodePathsResult(TQModel):
    """Mirrors the backend's BulkCodePathsResponse."""
    project_id: Optional[int] = None
    submitted: int = 0
    updated: int = 0
    results: List[CodePathsItem] = []
