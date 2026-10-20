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
    # Browser console errors/warnings during the test (redacted by the
    # worker, re-redacted on ingestion). Empty for results from older workers.
    console_errors: List[dict] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """One citable piece of evidence. `id` is what observations and
    hypotheses reference: `s3` (step 4), `s3.c0` (its first console line),
    `s3.r1` (its second failed request), `s3.dom`, `s3.shot`, `s3.trace`
    (backend error spans under the step, WP16a), `error`,
    `run:<id>` (a prior run of this case); results from workers that predate
    per-step evidence use `console.<n>` / `net.<n>`."""
    id: str
    kind: str  # step | console | request | dom | screenshot | trace | error | history
    label: str
    step_index: Optional[int] = None


class ObservedFact(BaseModel):
    """Something that happened — never an interpretation."""
    text: str
    evidence_refs: List[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    category: RootCause = "unknown"
    fix_target: FixTarget = "none"
    summary: str = ""
    confidence: float = 0.0
    evidence_refs: List[str] = Field(default_factory=list)
    # "heuristic" or "llm:<provider>"
    source: str = "heuristic"


class HistoryEntry(BaseModel):
    run_id: int
    result_id: int
    status: str
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    created_at: Optional[str] = None
    error_excerpt: Optional[str] = None
    # Same failure fingerprint (failure_signature) as this failure.
    same_signature: bool = False


class ClusterRef(BaseModel):
    id: int
    title: str
    status: str
    occurrence_count: int


class FailureHistory(BaseModel):
    runs_considered: int = 0
    prior_failures: int = 0
    # Consecutive failing runs of this case ending with this one (>= 1).
    failing_streak: int = 1
    first_failing_run_id: Optional[int] = None
    # The run's git_commit where the current failing streak began, when known.
    first_failing_commit: Optional[str] = None
    last_pass_run_id: Optional[int] = None
    last_pass_commit: Optional[str] = None
    # pass<->fail transitions across the runs considered (flakiness signal).
    flip_count: int = 0
    cluster: Optional[ClusterRef] = None
    entries: List[HistoryEntry] = Field(default_factory=list)


class ChangeRange(BaseModel):
    """Commits between the last passing run and this one."""
    from_commit: str
    to_commit: str
    last_pass_run_id: int
    repo: Optional[str] = None
    compare_url: Optional[str] = None


class FailureReport(BaseModel):
    # v2 adds the investigation fields below; every v1 field keeps its
    # meaning (they mirror the top-ranked hypothesis), so v1 readers work.
    schema_version: int = 2
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

    # ── v2: investigation ──
    result_id: Optional[int] = None
    test_name: Optional[str] = None
    observed: List[ObservedFact] = Field(default_factory=list)
    hypotheses: List[Hypothesis] = Field(default_factory=list)  # ranked, best first
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    history: Optional[FailureHistory] = None
    changed_since_last_pass: Optional[ChangeRange] = None
    # LLM citations that named no existing evidence item and were removed.
    dropped_citations: int = 0


class RunFailureAnalysis(BaseModel):
    """Run-level rollup stored in TestRun.ai_analysis."""
    schema_version: int = 1
    summary: str = ""
    failed_cases_analyzed: int = 0
    failed_cases_total: int = 0
    categories: dict = Field(default_factory=dict)  # category -> count
    reports: List[FailureReport] = Field(default_factory=list)
