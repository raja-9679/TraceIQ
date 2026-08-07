"""Proactive selector-heal — Celery task that runs after each successful
run and proposes selector updates wherever the stored selector now looks
brittle relative to the captured DOM.

Pipeline:
1. Load the finalized run + its test case + the case's steps.
2. For each step that has an `intent`, ask the LLM:
   "Given this DOM, does `selector` still uniquely identify the element
    described by `intent`? If not, what's the new selector?"
3. If the LLM returns a different selector with confidence ≥ threshold,
   insert a SelectorHealProposal.

This task is gated by env var `PROACTIVE_HEAL_ENABLED=true` so it can be
rolled out incrementally. The full pipeline (DOM capture per step, per-step
intent resolution) is Phase B work-in-progress; this scaffold writes
proposals when the data is available and gracefully no-ops otherwise.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import (
    Project,
    SelectorHealProposal,
    TestCase,
    TestRun,
)
from app.services.llm_usage import llm_call_context

_sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
_engine = create_engine(_sync_db_url, echo=False)


@celery_app.task(name="app.tasks.heal_tasks.propose_selector_heals_for_run")
def propose_selector_heals_for_run(run_id: int) -> Dict[str, Any]:
    """Generate selector-heal proposals for a finalized run."""
    if os.getenv("PROACTIVE_HEAL_ENABLED", "false").lower() != "true":
        return {"status": "disabled"}

    # Local imports keep cold-start light + avoid circular deps.
    from app.ai.providers import provider as llm

    if llm.name == "null":
        return {"status": "no_llm"}

    with Session(_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            return {"status": "run_not_found"}
        # Without a captured-DOM artifact we can't propose anything meaningful.
        # The full path captures DOM per step inside the worker; that artifact
        # arrives via the run's execution_log JSON (Phase B work continues).
        execution_log = run.execution_log or []
        if not execution_log:
            return {"status": "no_execution_log"}

        case_id = run.test_case_id
        if not case_id:
            return {"status": "no_case_scoped_run"}

        case = session.get(TestCase, case_id)
        if not case:
            return {"status": "case_not_found"}

        proposals_written = 0
        for step in case.steps or []:
            step_id = step.get("id") if isinstance(step, dict) else getattr(step, "id", None)
            intent = step.get("intent") if isinstance(step, dict) else getattr(step, "intent", None)
            selector = step.get("selector") if isinstance(step, dict) else getattr(step, "selector", None)
            if not (step_id and intent and selector):
                continue

            dom = _dom_for_step(execution_log, step_id)
            if not dom:
                continue

            prompt = (
                f"You are auditing a UI test selector.\n"
                f"Intent: {intent}\n"
                f"Current selector: {selector}\n"
                f"DOM snapshot (truncated):\n{dom[:8000]}\n\n"
                "Question: does the current selector still uniquely identify "
                "the element described by intent? Reply STRICTLY in JSON:\n"
                '{"still_valid": true|false, "new_selector": "...", "confidence": 0.0-1.0, "rationale": "..."}'
            )
            project = session.get(Project, run.project_id) if run.project_id else None
            with llm_call_context(
                feature="selector_heal",
                workspace_id=project.workspace_id if project else None,
                project_id=run.project_id, run_id=run_id,
            ):
                raw = llm.complete(prompt, max_tokens=300)
            verdict = _parse_verdict(raw)
            if not verdict or verdict.get("still_valid"):
                continue
            new_selector = verdict.get("new_selector")
            confidence = float(verdict.get("confidence") or 0.0)
            if not new_selector or confidence < 0.7:
                continue

            proposal = SelectorHealProposal(
                test_case_id=case_id,
                step_id=step_id,
                old_selector=selector,
                new_selector=new_selector,
                intent=intent,
                confidence=confidence,
                rationale=verdict.get("rationale"),
                source_run_id=run_id,
                status="pending",
                created_at=datetime.utcnow(),
            )
            session.add(proposal)
            proposals_written += 1

        session.commit()
        return {"status": "ok", "proposals_written": proposals_written}


def _dom_for_step(execution_log: List[dict], step_id: str) -> str:
    """Pull a per-step DOM snapshot out of the execution log if the engine
    happened to record one for this step. Returns empty when missing — the
    task no-ops rather than guessing.
    """
    for entry in execution_log:
        if not isinstance(entry, dict):
            continue
        if entry.get("step_id") == step_id and entry.get("dom"):
            return str(entry.get("dom"))
    return ""


def _parse_verdict(raw: str) -> Dict[str, Any]:
    """Tolerate LLMs that sometimes wrap JSON in prose or fences."""
    if not raw:
        return {}
    import json as _json
    raw = raw.strip()
    if raw.startswith("```"):
        # strip ```json fences
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # extract first {...} block
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return _json.loads(raw[start: end + 1])
    except Exception:
        return {}
