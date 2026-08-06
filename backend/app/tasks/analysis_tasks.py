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
from app.core.config import settings
from app.models import Project, TestCase, TestCaseResult, TestRun, TestStatus
from app.services.llm_usage import llm_call_context

_sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
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

                report = analyze_case_failure(
                    test_name=result.test_name,
                    error_message=result.error_message or "",
                    steps=steps,
                    response_status=result.response_status,
                    request_url=result.request_url,
                    provider_id=provider_id,
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
