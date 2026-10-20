"""Validated repairs (WP16c, decision V4).

Before a reviewer accepts a selector heal or a case edit, TraceIQ can try it:
a VALIDATION RUN executes the affected case once with the proposed change
applied to THAT RUN ONLY. The change travels as an overlay computed at
dispatch from the proposal (``overlay_for_run``) and lives only in the job
payload — the stored TestCase, its suite, its revisions, git-sync export and
repro bundles all read the database and never see it.

A validation run is an ordinary TestRun with ``is_validation`` set, so it has a
run page, artifacts and cancel. It is kept out of every statistic by
``app/services/run_scope.py`` and gets none of the completion side effects
except settling the proposal (``run_completion.VALIDATION_RUN_TASKS``).

Conditions. The run copies the browser, device, environment, persona and
emulation of the run the proposal came from (the failing run), so a pass means
"it works where it broke". With no source run it uses the defaults and records
``matches_source: false``. A repair is *successful* (RepairOutcome
``validation_passed`` + ``conditions_matched``) only when the run passed, on
the first attempt (retries are switched off), without a runtime self-heal, and
under those same conditions.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

HEAL = "heal"
CASE = "case"
KINDS = (HEAL, CASE)

VALIDATING = "validating"
VALIDATED = "validated"
FAILED = "validation_failed"

# Redis marker the aggregator sets when a validation run only passed because
# the worker re-pointed a selector at run time (older workers ignore the
# job's validation flag). Read when the outcome is settled.
HEALED_KEY = "validation:healed:{}"

_CONDITION_FIELDS = ("browser", "device", "environment_id", "persona_id", "anonymous", "emulation")


@dataclass
class RepairGuard:
    """What the proposal.validation_required / proposal.protected_suite rules
    need, computed up front by whoever has the session."""
    kind: str
    proposal_id: Optional[int]
    validatable: bool
    workspace_flag: bool
    protected_suite_id: Optional[int]
    status: Optional[str]
    validated_selector: Optional[str] = None
    selector: Optional[str] = None


# ---------------------------------------------------------------------------
# What a proposal touches
# ---------------------------------------------------------------------------

def is_validatable(kind: str, proposal: Any) -> bool:
    """Heals always (they edit one step of an existing case). Case proposals
    only when they are an UPDATE that changes behaviour (steps / executor)."""
    if kind == HEAL:
        return True
    action = getattr(getattr(proposal, "action", None), "value", getattr(proposal, "action", None))
    payload = getattr(proposal, "payload", None) or {}
    return (action == "update" and getattr(proposal, "target_case_id", None) is not None
            and ("steps" in payload or bool(payload.get("executor"))))


async def _suite_ids_touched(session, kind: str, proposal: Any) -> List[int]:
    from app.models import TestCase
    ids: List[int] = []
    if kind == HEAL:
        case = await session.get(TestCase, proposal.test_case_id)
        if case and case.test_suite_id:
            ids.append(case.test_suite_id)
        return ids
    if getattr(proposal, "test_suite_id", None):
        ids.append(proposal.test_suite_id)
    if getattr(proposal, "target_case_id", None):
        case = await session.get(TestCase, proposal.target_case_id)
        if case and case.test_suite_id:
            ids.append(case.test_suite_id)
    dest = (getattr(proposal, "payload", None) or {}).get("new_test_suite_id")
    if isinstance(dest, int):
        ids.append(dest)
    return ids


async def protected_suite_for(session, suite_ids: List[int]) -> Optional[int]:
    """The first protected suite among these suites or their ancestors.
    Protecting a suite protects everything below it."""
    from app.models import TestSuite
    seen = set()
    for start in suite_ids:
        current = start
        while current and current not in seen:
            seen.add(current)
            suite = await session.get(TestSuite, current)
            if suite is None:
                break
            if getattr(suite, "protected", False):
                return suite.id
            current = suite.parent_id
    return None


async def _project_workspace_flag(session, project_id: Optional[int]) -> bool:
    from app.models import Project, Workspace
    project = await session.get(Project, project_id) if project_id else None
    workspace = await session.get(Workspace, project.workspace_id) if project else None
    return bool(getattr(workspace, "require_validation_before_accept", False))


async def project_of(session, kind: str, proposal: Any) -> Optional[int]:
    if kind == CASE:
        return proposal.project_id
    from app.models import TestCase
    case = await session.get(TestCase, proposal.test_case_id)
    return case.project_id if case else None


async def repair_guard(session, kind: str, proposal: Any,
                       selector: Optional[str] = None) -> RepairGuard:
    project_id = await project_of(session, kind, proposal)
    status = getattr(proposal, "validation_status", None)
    if status == VALIDATING and not getattr(proposal, "validation_run_id", None):
        status = None   # its run was deleted: not validated any more
    conditions = getattr(proposal, "validation_conditions", None) or {}
    return RepairGuard(
        kind=kind, proposal_id=getattr(proposal, "id", None),
        validatable=is_validatable(kind, proposal),
        workspace_flag=await _project_workspace_flag(session, project_id),
        protected_suite_id=await protected_suite_for(
            session, await _suite_ids_touched(session, kind, proposal)),
        status=status,
        validated_selector=conditions.get("selector") if kind == HEAL else None,
        selector=selector if kind == HEAL else None)


# ---------------------------------------------------------------------------
# Starting a validation run
# ---------------------------------------------------------------------------

class ValidationRefused(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def conditions_from(source_run: Any) -> Dict[str, Any]:
    if source_run is None:
        return {"browser": "chromium", "device": None, "environment_id": None,
                "persona_id": None, "anonymous": None, "emulation": None,
                "source_run_id": None, "matches_source": False}
    out = {f: getattr(source_run, f, None) for f in _CONDITION_FIELDS}
    out["browser"] = out["browser"] or "chromium"
    out.update(source_run_id=source_run.id, matches_source=True)
    return out


def conditions_matched(conditions: Dict[str, Any], run: Any) -> bool:
    """Did the validation run execute under the failing run's conditions?"""
    if not conditions or not conditions.get("matches_source"):
        return False
    return all(getattr(run, f, None) == conditions.get(f)
               for f in ("browser", "device", "environment_id"))


async def start_validation(session, principal: Any, kind: str, proposal: Any,
                           selector: Optional[str] = None):
    """Create (not dispatch) the validation TestRun and mark the proposal
    validating. Caller has checked editor access; caller commits, then
    queues app.worker.run_test_suite."""
    from app.models import RunTrigger, TestCase, TestRun, TestStatus
    from app.services.test_service import test_service

    if getattr(proposal, "status", None) != "pending":
        raise ValidationRefused(409, f"Proposal is already {proposal.status}")
    if not is_validatable(kind, proposal):
        raise ValidationRefused(400, "Only selector heals and case edits that change steps "
                                     "can be validated.")
    if getattr(proposal, "validation_status", None) == VALIDATING and proposal.validation_run_id:
        live = await session.get(TestRun, proposal.validation_run_id)
        if live is not None and live.status in (TestStatus.PENDING, TestStatus.RUNNING):
            raise ValidationRefused(409, f"Validation run #{live.id} is still running.")

    case_id = proposal.test_case_id if kind == HEAL else proposal.target_case_id
    case = await session.get(TestCase, case_id)
    project_id = await project_of(session, kind, proposal)
    if case is None or case.project_id != project_id:
        raise ValidationRefused(404, "The case this proposal changes no longer exists.")
    if getattr(getattr(case, "executor", None), "value", case.executor) == "manual":
        raise ValidationRefused(400, "Manual cases are not run by workers; nothing to validate.")

    source = None
    if getattr(proposal, "source_run_id", None):
        source = await session.get(TestRun, proposal.source_run_id)
        if source is not None and source.project_id != project_id:
            source = None   # never borrow another project's conditions
    conditions = conditions_from(source)
    if kind == HEAL:
        conditions["selector"] = selector

    settings = await test_service.get_effective_settings(case.test_suite_id, session)
    run = TestRun(
        status=TestStatus.PENDING, test_suite_id=case.test_suite_id, test_case_id=case.id,
        project_id=project_id, suite_name=await test_service.get_suite_path(case.test_suite_id, session),
        test_case_name=case.name,
        request_headers=settings.get("headers", {}), request_params=settings.get("params", {}),
        allowed_domains=settings.get("allowed_domains", []),
        domain_settings=settings.get("domain_settings", {}),
        browser=conditions["browser"], device=conditions["device"],
        environment_id=conditions["environment_id"], persona_id=conditions["persona_id"],
        anonymous=conditions["anonymous"], emulation=conditions["emulation"],
        user_id=principal.user.id if principal.user else None,
        selection_mode="case", selection_total=1,
        triggered_by=RunTrigger.API_AGENT if principal.is_api_caller else RunTrigger.HUMAN,
        agent_id=getattr(principal, "agent_id", None),
        agent_session_id=getattr(principal, "agent_session_id", None),
        api_key_id=principal.api_key.id if principal.api_key else None,
        is_validation=True, validation_proposal_kind=kind, validation_proposal_id=proposal.id)
    session.add(run)
    await session.flush()
    proposal.validation_status = VALIDATING
    proposal.validation_run_id = run.id
    proposal.validation_conditions = conditions
    proposal.validation_detail = None
    proposal.validated_at = None
    session.add(proposal)
    return run


# ---------------------------------------------------------------------------
# Dispatch: the overlay
# ---------------------------------------------------------------------------

class OverlayCase:
    """A read-only stand-in for a TestCase with some attributes replaced.

    The dispatcher works on ORM rows inside a session it commits; assigning
    the proposed steps to the real row would PERSIST them. This wrapper keeps
    the row untouched while every reader of `case.<attr>` sees the overlay."""

    def __init__(self, case: Any, **overrides: Any):
        object.__setattr__(self, "_case", case)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_case"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_overrides")[name] = value


def _proposal_row(run: Any, session: Any):
    from app.models import CaseProposal, SelectorHealProposal
    model = SelectorHealProposal if run.validation_proposal_kind == HEAL else CaseProposal
    return session.get(model, run.validation_proposal_id) if run.validation_proposal_id else None


def overlay_for_run(run: Any, case: Any, session: Any) -> Dict[str, Any]:
    """The attribute overrides a validation run applies to `case`.

    Raises ValueError when the proposal is gone, decided, belongs to another
    case, or its target step disappeared — the run then errors instead of
    "validating" the stored test."""
    from app.services.test_service import normalize_steps
    proposal = _proposal_row(run, session)
    if proposal is None:
        raise ValueError("the proposal this run validates no longer exists")
    if proposal.status != "pending":
        raise ValueError(f"the proposal this run validates is already {proposal.status}")
    if run.validation_proposal_kind == HEAL:
        if proposal.test_case_id != case.id:
            raise ValueError("the heal proposal targets another case")
        selector = ((proposal.validation_conditions or {}).get("selector")
                    or proposal.chosen_selector or proposal.new_selector)
        steps = [copy.deepcopy(s if isinstance(s, dict) else s.dict()) for s in (case.steps or [])]
        idx = next((i for i, s in enumerate(steps) if s.get("id") == proposal.step_id), None)
        if idx is None:
            raise ValueError("the step this heal targets no longer exists in the case")
        steps[idx]["selector"] = selector
        return {"steps": steps}
    if proposal.target_case_id != case.id:
        raise ValueError("the case proposal targets another case")
    payload = proposal.payload or {}
    overrides: Dict[str, Any] = {}
    if "steps" in payload:
        overrides["steps"] = normalize_steps(copy.deepcopy(payload["steps"]))
    if payload.get("executor"):
        from app.api.agent_ownership import _validated_executor
        overrides["executor"] = _validated_executor(payload)
    return overrides


def apply_overlay(run: Any, cases: List[Any], session: Any) -> List[Any]:
    return [OverlayCase(c, **overlay_for_run(run, c, session)) for c in cases]


# Suite-chain settings a validation run must not inherit: a retry turns a
# flaky pass into "validated", so every validation is a first-attempt pass.
def harden_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    settings["auto_retry"] = False
    settings["max_retries"] = 0
    settings["validation_run"] = True
    return settings


# ---------------------------------------------------------------------------
# Settling the outcome
# ---------------------------------------------------------------------------

def _redis():
    from app.services import environment_lock
    return environment_lock.client()


def note_runtime_heal(run_id: int) -> None:
    try:
        _redis().set(HEALED_KEY.format(run_id), "1", ex=7 * 24 * 3600)
    except Exception as exc:  # noqa: BLE001
        print(f"[Validation] could not record runtime heal for run {run_id}: {exc}")


def _runtime_healed(run_id: int) -> bool:
    try:
        return bool(_redis().get(HEALED_KEY.format(run_id)))
    except Exception:  # noqa: BLE001
        return False


def verdict(run: Any, results: List[Any], healed: bool) -> Optional[str]:
    """None = passed cleanly; otherwise why it is not a validated repair."""
    from app.models import TestStatus
    if run.status != TestStatus.PASSED:
        value = getattr(run.status, "value", run.status)
        return f"validation run #{run.id} ended {value}"
    if not results:
        return f"validation run #{run.id} produced no results"
    bad = [r for r in results if r.status != TestStatus.PASSED]
    if bad:
        return f"{len(bad)} of {len(results)} result(s) did not pass"
    if healed:
        return "passed only because the worker re-pointed a selector at run time"
    return None


def settle_sync(session: Any, run: Any) -> Optional[str]:
    """Record a finished validation run's outcome on its proposal. Returns
    the new validation status (None when there was nothing to settle)."""
    from sqlmodel import select
    from app.models import RepairOutcome, TestCaseResult
    if getattr(run, "is_validation", False) is not True:
        return None
    proposal = _proposal_row(run, session)
    if proposal is None or proposal.validation_run_id != run.id:
        return None   # a newer validation superseded this one
    results = session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id)).all()
    why = verdict(run, results, _runtime_healed(run.id))
    conditions = proposal.validation_conditions or {}
    matched = conditions_matched(conditions, run)
    proposal.validation_status = VALIDATED if why is None else FAILED
    proposal.validation_detail = (
        why or ("passed under the failing run's browser, device and environment" if matched
                else "passed (no failing run to compare conditions with)" if not conditions.get("matches_source")
                else "passed, but not under the failing run's conditions"))[:500]
    proposal.validated_at = datetime.utcnow()
    session.add(proposal)
    session.add(RepairOutcome(
        project_id=run.project_id, proposal_kind=run.validation_proposal_kind,
        proposal_id=proposal.id, event="validation_passed" if why is None else "validation_failed",
        run_id=run.id, conditions_matched=matched, conditions=conditions,
        user_id=getattr(run, "user_id", None)))
    return proposal.validation_status


async def record_decision(session, kind: str, proposal: Any, event: str, project_id: int,
                          user_id: Optional[int]) -> None:
    """accepted / rejected / auto_applied — with whether it had been validated.
    Only for repairs (validatable proposals): a CREATE or DELETE is not a
    repair and would skew a success rate."""
    from app.models import RepairOutcome
    if not is_validatable(kind, proposal) and not getattr(proposal, "validation_run_id", None):
        return
    conditions = getattr(proposal, "validation_conditions", None)
    session.add(RepairOutcome(
        project_id=project_id, proposal_kind=kind, proposal_id=proposal.id, event=event,
        run_id=getattr(proposal, "validation_run_id", None),
        validated=getattr(proposal, "validation_status", None) == VALIDATED,
        conditions=conditions, user_id=user_id))


# ---------------------------------------------------------------------------
# API glue shared by the heal and case proposal endpoints
# ---------------------------------------------------------------------------

async def launch(session, principal: Any, kind: str, proposal: Any,
                 selector: Optional[str] = None) -> Dict[str, Any]:
    """Policy check (run.dispatch/create: quota, agent kill switch + run
    budget), create the run, audit, commit, dispatch. Raises HTTPException."""
    from fastapi import HTTPException
    from app.services import billing
    from app.services.agent_controls import record_usage
    from app.services.audit import record as audit_record
    from app.services.policy import PolicyContext, evaluate

    project_id = await project_of(session, kind, proposal)
    ws_id = await billing.workspace_id_for_project(session, project_id)
    verdict = await evaluate("run.dispatch", PolicyContext.from_principal(
        principal, workspace_id=ws_id, project_id=project_id, stage="create", session=session))
    if not verdict.allowed:
        raise verdict.http_exception()
    try:
        run = await start_validation(session, principal, kind, proposal, selector)
    except ValidationRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    await audit_record(session, entity_type=f"{kind}_proposal", entity_id=proposal.id,
                       action="validation_started", workspace_id=ws_id, principal=principal,
                       changes={"run_id": run.id, "conditions": proposal.validation_conditions})
    await session.commit()
    await session.refresh(proposal)
    from app.worker import run_test_suite
    try:
        run_test_suite.delay(run.id)
    except Exception as exc:  # noqa: BLE001 — the run stays PENDING, visible on its page
        print(f"[Validation] could not queue run {run.id}: {exc}")
    if ws_id:
        try:
            await billing.record_usage(session, ws_id, "runs", 1)
        except Exception as exc:  # noqa: BLE001
            print(f"[Validation] usage not recorded for workspace {ws_id}: {exc}")
        await record_usage(session, principal, ws_id, runs=1)
    return {"run_id": run.id, "validation_status": proposal.validation_status,
            "conditions": proposal.validation_conditions}


async def enforce_accept(session, kind: str, proposal: Any, approver_id: Optional[int],
                         selector: Optional[str] = None, *, include_separation: bool = False):
    """proposal.approve with the repair facts. For case proposals the caller
    also wants separation of duties (same action); heals have no proposer."""
    from app.models import Project
    from app.services.policy import PolicyContext, evaluate
    project_id = await project_of(session, kind, proposal)
    project = await session.get(Project, project_id) if project_id else None
    guard = await repair_guard(session, kind, proposal, selector)
    facts = {"repair": guard, "approver_id": approver_id}
    if include_separation:
        facts["proposal"] = proposal
    decision = await evaluate("proposal.approve", PolicyContext(
        workspace_id=getattr(project, "workspace_id", None), project_id=project_id,
        user_id=approver_id, session=session, facts=facts),
        only=None if include_separation else ["proposal.validation_required"])
    if not decision.allowed:
        raise decision.http_exception()
    return guard


async def annotate(session, kind: str, proposal: Any, read: Any) -> Any:
    """Fill the read model's validation_required / protected_suite_id for a
    pending proposal, so the review UI can explain a disabled Accept before
    anyone clicks it. The accept endpoint stays the authority."""
    if getattr(proposal, "status", None) != "pending":
        return read
    from app.services.proposal_policy import validation_required
    try:
        g = await repair_guard(session, kind, proposal)
    except Exception as exc:  # noqa: BLE001 — informational only
        print(f"[Validation] could not annotate {kind} proposal {proposal.id}: {exc}")
        return read
    read.protected_suite_id = g.protected_suite_id
    read.validation_required = g.validatable and validation_required(
        workspace_flag=g.workspace_flag, suite_protected=g.protected_suite_id is not None)
    return read
