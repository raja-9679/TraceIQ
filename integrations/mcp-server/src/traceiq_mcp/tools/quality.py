"""Quality & results services: gate, report, analytics, triage, flakes,
heal proposals, comparison runs, external (JUnit) results."""
from __future__ import annotations

from typing import Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.quality import (
    ComparisonResult,
    EffectivenessList,
    ExternalReport,
    ExternalReportList,
    FailureClusterDetail,
    FailureClusterList,
    FlakeList,
    HealProposalList,
    QualityGateResult,
    QualitySnapshot,
    RunReport,
)
from traceiq_mcp.schemas.runs import RunRef


@mcp.tool()
async def get_quality_snapshot(project_id: int) -> QualitySnapshot:
    """Aggregated project quality over a rolling window: run pass rate +
    trend, flaky/quarantined counts, monitor uptime, security finding counts.
    The one-call project health check."""
    data = await new_client().quality_snapshot(project_id)
    return QualitySnapshot.model_validate(data)


@mcp.tool()
async def evaluate_quality_gate(project_id: int,
                                git_commit: Optional[str] = None,
                                git_branch: Optional[str] = None) -> QualityGateResult:
    """Go/no-go for a release: evaluates the runs for a commit (or branch, or
    the latest run) against the project's gate policy. Use after your runs
    finish to decide whether the change is safe to merge."""
    data = await new_client().quality_gate(project_id, git_commit, git_branch)
    return QualityGateResult.model_validate(data)


@mcp.tool()
async def get_run_report(run_id: int) -> RunReport:
    """Consolidated report for one run: per-test results, security counts,
    gate verdict, git context, and ready-to-paste markdown for a PR comment."""
    data = await new_client().get_run_report(run_id)
    return RunReport.model_validate(data)


@mcp.tool()
async def get_test_effectiveness(project_id: int, days: int = 30,
                                 limit: int = 100) -> EffectivenessList:
    """Per-test signal metrics: run count, failure rate, distinct failure
    clusters surfaced, average duration. High-failure/low-signal tests are
    candidates for review or deletion proposals."""
    data = await new_client().test_effectiveness(project_id, days, limit)
    return EffectivenessList(items=data)


@mcp.tool()
async def list_failure_clusters(project_id: int,
                                status: Optional[str] = None,
                                category: Optional[str] = None) -> FailureClusterList:
    """Failure clusters: N identical failures deduped into one root cause.
    Check whether your failure is a known cluster before diagnosing from
    scratch. status: open | investigating | resolved | ignored."""
    data = await new_client().list_failure_clusters(project_id, status, category)
    return FailureClusterList(items=data)


@mcp.tool()
async def get_failure_cluster(cluster_id: int) -> FailureClusterDetail:
    """One failure cluster with its recent occurrences (result + run ids)."""
    data = await new_client().get_failure_cluster(cluster_id)
    return FailureClusterDetail.model_validate(data)


@mcp.tool()
async def list_flakes(project_id: Optional[int] = None,
                      test_case_id: Optional[int] = None,
                      quarantined_only: bool = False) -> FlakeList:
    """Flake records (alternation-based score over the last 20 results;
    quarantined cases are skipped at dispatch). Scope by project_id or
    test_case_id — one is required."""
    if project_id is None and test_case_id is None:
        raise ValueError(
            "list_flakes requires a scope: pass project_id or test_case_id.")
    data = await new_client().list_flakes(project_id, test_case_id,
                                          quarantined_only)
    return FlakeList(items=data)


@mcp.tool()
async def list_heal_proposals(status: str = "pending",
                              test_case_id: Optional[int] = None,
                              project_id: Optional[int] = None) -> HealProposalList:
    """Selector-heal proposals the workers generated when selectors broke
    (old → suggested new selector, with confidence). Read-only here: accepting
    is a human action in the TraceIQ UI, but you can fold the suggestion into
    a propose_update_case of your own."""
    data = await new_client().list_heal_proposals(status, test_case_id,
                                                  project_id)
    return HealProposalList(items=data)


@mcp.tool()
async def create_comparison_run(baseline_run_id: int, target_url: str,
                                browser: Optional[str] = None,
                                device: Optional[str] = None) -> RunRef:
    """Re-execute the suite behind a baseline run against a different
    target_url (e.g. staging vs prod, or a preview deploy). Poll with
    wait_for_run, then read get_comparison for the regressed/recovered diff."""
    data = await new_client().create_comparison_run(
        baseline_run_id, target_url, browser, device)
    return RunRef.model_validate(data)


@mcp.tool()
async def get_comparison(run_id: int) -> ComparisonResult:
    """Side-by-side diff of a comparison run vs its baseline: per-test
    regressed/recovered flags and summary counts."""
    data = await new_client().get_comparison(run_id)
    return ComparisonResult.model_validate(data)


@mcp.tool()
async def ingest_junit_report(project_id: int, junit_xml: str,
                              git_commit: Optional[str] = None,
                              git_branch: Optional[str] = None,
                              suite: Optional[str] = None) -> ExternalReport:
    """Ingest a JUnit XML report (e.g. from your unit-test framework) so
    TraceIQ correlates it with browser-run results on the same commit. Body =
    the raw XML string, max 5 MB."""
    data = await new_client().ingest_junit(project_id, junit_xml,
                                           git_commit, git_branch, suite)
    return ExternalReport.model_validate(data)


@mcp.tool()
async def list_external_results(project_id: int,
                                git_commit: Optional[str] = None,
                                limit: int = 20) -> ExternalReportList:
    """List ingested external (JUnit) reports, optionally for one commit."""
    data = await new_client().list_external_results(project_id, git_commit,
                                                    limit)
    return ExternalReportList(items=data)
