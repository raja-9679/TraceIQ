"""Typed failure-report schema.

Replaces the freeform ai_analysis dict for run/case failure analysis. The
shape is stable and versioned so MCP consumers (coding agents) can branch on
`root_cause_category` to decide whether to fix the app, fix the test, or
retry — without parsing prose.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RootCause = Literal["app_bug", "test_bug", "environment", "flake", "unknown"]
FixTarget = Literal["app", "test", "infra", "none"]


class FailureEvidence(BaseModel):
    error_message: Optional[str] = None
    failing_step_index: Optional[int] = None
    failing_step: Optional[dict] = None
    selector: Optional[str] = None
    http_status: Optional[int] = None
    request_url: Optional[str] = None
    network_failures: List[dict] = Field(default_factory=list)


class FailureReport(BaseModel):
    schema_version: int = 1
    root_cause_category: RootCause = "unknown"
    summary: str = ""
    details: str = ""
    suggested_fix: Optional[str] = None
    # What a coding agent should act on: the application code, the test
    # definition, the environment/infra, or nothing (e.g. flake → retry).
    fix_target: FixTarget = "none"
    confidence: float = 0.0
    evidence: FailureEvidence = Field(default_factory=FailureEvidence)
    # Model that produced the report; "heuristic" when no LLM was available.
    analyzed_by: str = "heuristic"


class RunFailureAnalysis(BaseModel):
    """Run-level rollup stored in TestRun.ai_analysis."""
    schema_version: int = 1
    summary: str = ""
    failed_cases_analyzed: int = 0
    failed_cases_total: int = 0
    categories: dict = Field(default_factory=dict)  # category -> count
    reports: List[FailureReport] = Field(default_factory=list)
