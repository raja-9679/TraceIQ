"""Proactive selector-heal — Celery task that runs after each finalized
run and proposes selector updates wherever the stored selector now looks
brittle relative to the captured DOM.

Pipeline:
1. For each result of the run that carries a `step_dom` map (step id ->
   compact DOM, recorded by the worker), load its test case.
2. For each step that has an `intent` and a captured DOM, ask the LLM:
   "Given this DOM, does `selector` still uniquely identify the element
    described by `intent`? If not, what's the new selector?"
3. If the LLM returns a different selector with confidence >= 0.7, rank it
   with the deterministic candidates (W5, app/services/heal_candidates.py)
   on the same redacted DOM and insert a pending SelectorHealProposal for the
   best candidate that matches exactly one element (deduplicated against
   pending ones), with the ranked list and a stored snapshot.

Gated by `PROACTIVE_HEAL_ENABLED=true`. Workers only record step DOMs when
that flag is on AND the run's capture level is `full` (see
`app.worker.wants_step_dom`), so on most projects this task finds nothing to
do and says so.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import (
    Project,
    SelectorHealProposal,
    TestCase,
    TestCaseResult,
    TestRun,
)
from app.services.data_policy import resolve_for_project
from app.services.heal_candidates import choose_candidate, rank_candidates, store_snapshot
from app.services.llm_usage import llm_call_context
from app.services.redaction import RedactionPolicy, redact_dom

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
        # Workspace AI opt-out / token quota / agent budget (ai.use): stop
        # here rather than have every step's LLM call refused one by one.
        from app.services.policy import ai_allowed_sync
        _ws_project = session.get(Project, run.project_id) if run.project_id else None
        verdict = ai_allowed_sync(_ws_project.workspace_id if _ws_project else None,
                                  agent_id=run.agent_id, agent_session_id=run.agent_session_id,
                                  project_id=run.project_id)
        if not verdict.allowed:
            return {"status": "ai_not_allowed", "rule": verdict.rule}

        results = session.exec(
            select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)
        ).all()
        doms_by_case = step_doms_by_case(results, run.test_case_id)
        if not doms_by_case:
            return {"status": "no_step_dom"}

        project = session.get(Project, run.project_id) if run.project_id else None
        # The worker already redacted step_dom with the project's policy; this
        # second pass guarantees an older worker's raw excerpt never reaches
        # the LLM or the snapshot store.
        blob = resolve_for_project(project) if project else {}
        policy = RedactionPolicy.from_data_policy(blob)
        masks = blob.get("mask_selectors") or []
        proposals_written = 0
        for case_id, doms in doms_by_case.items():
            case = session.get(TestCase, case_id)
            if not case:
                continue
            for step in case.steps or []:
                step = step if isinstance(step, dict) else step.dict()
                step_id, intent, selector = step.get("id"), step.get("intent"), step.get("selector")
                dom = doms.get(step_id) if step_id else None
                if not (intent and selector and dom):
                    continue
                safe_dom = redact_dom(dom, policy, masks)

                prompt = (
                    f"You are auditing a UI test selector.\n"
                    f"Intent: {intent}\n"
                    f"Current selector: {selector}\n"
                    f"DOM snapshot (truncated):\n{safe_dom[:8000]}\n\n"
                    "Question: does the current selector still uniquely identify "
                    "the element described by intent? Reply STRICTLY in JSON:\n"
                    '{"still_valid": true|false, "new_selector": "...", "confidence": 0.0-1.0, "rationale": "..."}'
                )
                with llm_call_context(
                    feature="selector_heal",
                    workspace_id=project.workspace_id if project else None,
                    project_id=run.project_id, run_id=run_id,
                    agent_id=run.agent_id, agent_session_id=run.agent_session_id,
                ):
                    raw = llm.complete(prompt, max_tokens=300)
                verdict = _parse_verdict(raw)
                if not verdict or verdict.get("still_valid"):
                    continue
                new_selector = verdict.get("new_selector")
                confidence = float(verdict.get("confidence") or 0.0)
                if not new_selector or new_selector == selector or confidence < 0.7:
                    continue

                # W5: rank the LLM's pick against the deterministic strategies
                # on the same snapshot; propose the best candidate that matches
                # exactly one element, or nothing.
                ranked = rank_candidates(safe_dom, selector, intent, new_selector, confidence)
                chosen = choose_candidate(ranked)
                if chosen is None:
                    continue
                if session.exec(select(SelectorHealProposal).where(
                        SelectorHealProposal.test_case_id == case_id,
                        SelectorHealProposal.step_id == step_id,
                        SelectorHealProposal.new_selector == chosen["selector"],
                        SelectorHealProposal.status == "pending")).first():
                    continue

                session.add(SelectorHealProposal(
                    test_case_id=case_id,
                    step_id=step_id,
                    old_selector=selector,
                    new_selector=chosen["selector"],
                    intent=intent,
                    confidence=chosen["score"],
                    rationale=verdict.get("rationale"),
                    source_run_id=run_id,
                    status="pending",
                    created_at=datetime.utcnow(),
                    candidates=ranked,
                    chosen_selector=chosen["selector"],
                    dom_snapshot_key=store_snapshot(run_id, case_id, step_id, safe_dom),
                ))
                proposals_written += 1

        session.commit()
        return {"status": "ok", "proposals_written": proposals_written}


def step_doms_by_case(results, run_case_id=None) -> Dict[int, Dict[str, str]]:
    """{test_case_id: {step_id: dom}} from a run's results' `step_dom` maps.

    Results with no resolvable case are skipped (a single-case run's results
    may predate the aggregator stamping test_case_id, hence the fallback).
    """
    out: Dict[int, Dict[str, str]] = {}
    for res in results:
        doms = (res.result_payload or {}).get("step_dom")
        case_id = res.test_case_id or run_case_id
        if not (isinstance(doms, dict) and doms and case_id):
            continue
        out.setdefault(case_id, {}).update(
            {k: v for k, v in doms.items() if isinstance(v, str) and v})
    return out


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
