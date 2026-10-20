"""Celery task: typed failure analysis for completed runs.

Fired by the result aggregator when a run finalizes with failures. Analyzes
up to FAILURE_ANALYSIS_MAX_PER_RUN failed cases (default 5), stores a typed
FailureReport JSON on each TestCaseResult.ai_analysis and a RunFailureAnalysis
rollup on TestRun.ai_analysis — the payload behind the `get_failure_analysis`
MCP tool.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import FailureCluster, Project, TestCase, TestCaseResult, TestRun, TestStatus
from app.services.llm_usage import llm_call_context

_sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
_engine = create_engine(_sync_db_url, echo=False)

_MAX_PER_RUN = int(os.getenv("FAILURE_ANALYSIS_MAX_PER_RUN", "5"))


@celery_app.task(name="app.tasks.analysis_tasks.analyze_run_failures")
def analyze_run_failures(run_id: int, provider_id: int | None = None) -> Dict[str, Any]:
    from app.services.failure_analysis import analyze_case_failure, build_run_rollup

    with Session(_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            return {"status": "run_not_found"}

        failed_results = session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run_id,
                TestCaseResult.status.in_([TestStatus.FAILED, TestStatus.ERROR]),  # type: ignore[attr-defined]
            )
        ).all()
        if not failed_results:
            return {"status": "no_failures"}

        project = session.get(Project, run.project_id) if run.project_id else None
        usage_ctx = llm_call_context(
            workspace_id=project.workspace_id if project else None,
            project_id=run.project_id, run_id=run_id,
            agent_id=run.agent_id, agent_session_id=run.agent_session_id,
        )

        reports = []
        with usage_ctx:
            for result in failed_results[:_MAX_PER_RUN]:
                steps = []
                case = None
                # Prefer the precise link stamped by the aggregator.
                if getattr(result, "test_case_id", None):
                    case = session.get(TestCase, result.test_case_id)
                if case is None and run.test_case_id:
                    case = session.get(TestCase, run.test_case_id)
                if case is None:
                    # Name fallback MUST be scoped to this run's project — a
                    # global name match could pull another tenant's case steps
                    # into this tenant's failure report.
                    case = session.exec(
                        select(TestCase).where(
                            TestCase.name == result.test_name,
                            TestCase.project_id == run.project_id)
                    ).first()
                if case:
                    steps = [s if isinstance(s, dict) else s.dict() for s in (case.steps or [])]

                history = change = None
                if case is not None:
                    try:
                        history, change = case_history(session, run, result, case.id)
                    except Exception as exc:  # noqa: BLE001 — history is context, not a gate
                        print(f"[FailureAnalysis] history lookup failed for result {result.id}: {exc}")

                payload = result.result_payload or {}
                step_traces = backend_error_spans(run, project, payload.get("step_evidence"))
                report = analyze_case_failure(
                    test_name=result.test_name,
                    error_message=result.error_message or "",
                    steps=steps,
                    response_status=result.response_status,
                    request_url=result.request_url,
                    network_failures=network_failures_for(
                        run.network_events, result.test_name),
                    console_errors=payload.get("console_errors"),
                    provider_id=provider_id,
                    step_evidence=payload.get("step_evidence"),
                    history=history,
                    changed_since_last_pass=change,
                    result_id=result.id,
                    step_traces=step_traces,
                )
                result.ai_analysis = report.model_dump_json()
                session.add(result)
                reports.append(report)

        rollup = build_run_rollup(reports, failed_total=len(failed_results))
        run.ai_analysis = json.loads(rollup.model_dump_json())
        session.add(run)
        session.commit()

        print(f"[FailureAnalysis] Run {run_id}: analyzed "
              f"{len(reports)}/{len(failed_results)} failures "
              f"({rollup.categories})")
        return {"status": "ok", "analyzed": len(reports),
                "categories": rollup.categories}


def backend_error_spans(run, project, step_evidence) -> dict | None:
    """{step index: error-span summary} from the trace backend (WP16a), or
    None when there is nothing to look up. Never raises: traces are evidence
    for the report, and spans that have not been ingested yet simply mean no
    `sN.trace` item (re-running the analysis later picks them up)."""
    from app.services import trace_backends
    if run.local_worker_id or not trace_backends.step_trace_refs(step_evidence):
        return None
    cfg = trace_backends.configured()
    if cfg is None:
        return None
    try:
        from app.services.data_policy import resolve_for_project
        from app.services.redaction import RedactionPolicy
        policy = RedactionPolicy.from_data_policy(resolve_for_project(project) if project else None)
        payload = trace_backends.build_result_traces(step_evidence, cfg=cfg, policy=policy)
        return trace_backends.error_spans_by_step(payload) or None
    except Exception as exc:  # noqa: BLE001
        print(f"[FailureAnalysis] trace lookup failed for run {run.id}: {exc}")
        return None


def network_failures_for(events, test_name: str) -> list:
    """This test's failed responses (HTTP >= 400) from the run's network log.

    Events carry only redacted URLs and headers; bodies are not included.
    Trimmed to the fields the analysis needs so the prompt stays small.
    """
    out = []
    for e in events or []:
        if not isinstance(e, dict) or e.get("testCaseName") != test_name:
            continue
        status = e.get("status")
        if isinstance(status, int) and status >= 400:
            out.append({"method": e.get("method"), "url": e.get("url"),
                        "status": status, "resourceType": e.get("resourceType")})
    return out[:10]


HISTORY_LIMIT = 20


def case_history(session: Session, run: TestRun, result: TestCaseResult, case_id: int):
    """(FailureHistory, ChangeRange | None) for one failing result.

    Prior results of the SAME case (and the same data-driven row, via the
    result name) in the SAME project, from runs older than this one, newest
    first. The project filter is belt-and-braces: `case_id` already pins the
    project, but history must never be able to cross a tenant boundary.
    Signatures are recomputed from each prior error rather than read from
    `cluster_id`, because clustering runs concurrently with this task and the
    current result may not be clustered yet.
    """
    from app.schemas.failure_report import ClusterRef, HistoryEntry
    from app.services.failure_investigation import summarise_history
    from app.services.failure_signature import compute_signature

    rows = session.exec(
        select(TestCaseResult, TestRun)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(
            TestCaseResult.test_case_id == case_id,
            TestCaseResult.test_name == result.test_name,
            TestCaseResult.test_run_id < run.id,
            TestRun.project_id == run.project_id,
            TestCaseResult.status.in_([TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]),  # type: ignore[attr-defined]
        )
        .order_by(TestCaseResult.test_run_id.desc(), TestCaseResult.id.desc())  # type: ignore[attr-defined]
        .limit(HISTORY_LIMIT)
    ).all()

    signature = compute_signature(result.error_message, result.test_name)[0]
    entries = []
    for prior, prior_run in rows:
        status = prior.status.value if hasattr(prior.status, "value") else str(prior.status)
        failed = status in ("failed", "error")
        entries.append(HistoryEntry(
            run_id=prior_run.id,
            result_id=prior.id,
            status=status,
            git_commit=prior_run.git_commit,
            git_branch=prior_run.git_branch,
            created_at=prior_run.created_at.isoformat() if prior_run.created_at else None,
            error_excerpt=((prior.error_message or "")[:200] or None) if failed else None,
            same_signature=failed and compute_signature(prior.error_message, prior.test_name)[0] == signature,
        ))

    cluster = None
    if run.project_id:
        c = session.exec(select(FailureCluster).where(
            FailureCluster.project_id == run.project_id,
            FailureCluster.signature == signature)).first()
        if c:
            cluster = ClusterRef(id=c.id, title=c.title[:200], status=c.status,
                                 occurrence_count=c.occurrence_count)
    return summarise_history(entries, run.git_commit, run.id, run.git_repo, cluster)
