"""Tautology detector — Phase D guard-rail against AI-authored test cases
that pass without actually verifying anything.

Heuristic: a TestCase is suspect when ALL of the following hold:
  • is_ai_authored = true
  • last_human_reviewed_at IS NULL (never explicitly approved by a human)
  • It has executed at least MIN_RUNS times (default 10)
  • Every single result was "passed" — never failed, never errored
  • Mean duration is below a low threshold (cheap tests are more likely to
    be no-op assertions)

Output: a CaseProposal of action=UPDATE with payload describing the
suspicion. A reviewer accepts (which marks the case as `last_human_reviewed_at`
and clears suspicion) or rejects after manually rewriting the case.

Gated by env var `TAUTOLOGY_DETECTOR_ENABLED=true`.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import (
    CaseProposal,
    CaseProposalAction,
    TestCase,
    TestCaseResult,
)

_sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
_engine = create_engine(_sync_db_url, echo=False)

MIN_RUNS = int(os.getenv("TAUTOLOGY_MIN_RUNS", "10"))
SUSPECT_DURATION_MS = float(os.getenv("TAUTOLOGY_SUSPECT_DURATION_MS", "500"))


@celery_app.task(name="app.tasks.tautology_tasks.scan_for_tautologies")
def scan_for_tautologies() -> Dict[str, Any]:
    if os.getenv("TAUTOLOGY_DETECTOR_ENABLED", "false").lower() != "true":
        return {"status": "disabled"}

    proposals_written = 0
    cases_examined = 0
    with Session(_engine) as session:
        # Candidate cases: AI-authored, never human-reviewed.
        candidate_stmt = select(TestCase).where(
            TestCase.is_ai_authored == True,  # noqa: E712
            TestCase.last_human_reviewed_at.is_(None),
        )
        for case in session.exec(candidate_stmt):
            cases_examined += 1
            results_stmt = (
                select(TestCaseResult)
                .where(TestCaseResult.test_name == case.name)
                .order_by(TestCaseResult.id.desc())
                .limit(MIN_RUNS + 5)
            )
            results: List[TestCaseResult] = list(session.exec(results_stmt))
            if len(results) < MIN_RUNS:
                continue
            if not all(_is_pass(r.status) for r in results):
                continue
            avg_duration = sum((r.duration_ms or 0) for r in results) / len(results)
            if avg_duration > SUSPECT_DURATION_MS:
                continue

            # Avoid duplicating a still-pending tautology proposal for this case.
            existing_stmt = select(CaseProposal).where(
                CaseProposal.target_case_id == case.id,
                CaseProposal.status == "pending",
                CaseProposal.action == CaseProposalAction.UPDATE,
            )
            if session.exec(existing_stmt).first():
                continue

            rationale = (
                f"AI-authored case has passed {len(results)} consecutive runs in "
                f"avg {avg_duration:.0f} ms and was never reviewed by a human. "
                "It may be tautological (always passes regardless of app state)."
            )
            proposal = CaseProposal(
                project_id=case.project_id,
                test_suite_id=case.test_suite_id,
                target_case_id=case.id,
                action=CaseProposalAction.UPDATE,
                payload={"suspicion": "tautology", "sample_size": len(results)},
                rationale=rationale,
                ai_confidence=0.5,
                agent_id="tautology-detector",
            )
            session.add(proposal)
            proposals_written += 1

        if proposals_written:
            session.commit()
    return {
        "status": "ok",
        "cases_examined": cases_examined,
        "proposals_written": proposals_written,
    }


def _is_pass(status: Any) -> bool:
    s = status.value if hasattr(status, "value") else status
    return str(s).lower() == "passed"
