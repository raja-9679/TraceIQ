"""What happens exactly once, when a run reaches a terminal state.

Until WP15f the only trigger for these side effects was
``POST /api/runs/{id}/finalize``, called by the LEGACY execution engine
(``execution-engine/src/server.ts`` → ``controller/run-monitor.ts``, which
listens for ``run:<id>:complete`` on Redis pub/sub). The community, prod and
AIO stacks and the Helm chart do not run that service, so on every shipped
deployment a finished distributed run never sent notifications, outbound
webhooks, monitor checks, pages, triage clusters, passive scans or leak
scans. The aggregator now finalizes the run itself, in the same transaction
that gives the run its terminal status; the endpoint stays for the legacy
engine and is idempotent with this path through the same claim.

The claim is a conditional UPDATE on ``testrun.finalized_at``: of two
concurrent finalizers (two aggregator processes, or the aggregator and the
legacy endpoint) exactly one updates the row, and only that one queues the
side effects — every task below is externally visible (customer email,
outbound CI webhooks, paging).
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import update

from app.models import TestRun

logger = logging.getLogger(__name__)

# (module, task, takes the finalize payload). Order is the order they used
# to be queued in the endpoint; each one is best-effort and independent.
SIDE_EFFECT_TASKS: Tuple[Tuple[str, str, bool], ...] = (
    ("app.tasks.notification_tasks", "send_run_notifications", True),
    ("app.tasks.outbound_webhook_tasks", "dispatch_run_webhooks", False),
    ("app.tasks.heal_tasks", "propose_selector_heals_for_run", False),
    ("app.tasks.monitor_tasks", "evaluate_monitor_for_run", False),
    ("app.tasks.paging_tasks", "page_for_run", False),
    ("app.tasks.security_tasks", "scan_run_passive", False),
    ("app.tasks.security_tasks", "scan_run_leaks", False),
    ("app.tasks.triage_tasks", "cluster_run_failures", False),
    # WP16b: owner email on a case's first failure after a pass.
    ("app.tasks.notification_tasks", "notify_case_owners_for_run", False),
    # WP16c: settle the repair proposal a validation run was testing (no-op
    # for every other run).
    ("app.tasks.repair_validation_tasks", "record_validation_outcome", False),
)

VALIDATION_RUN_TASKS = frozenset({"record_validation_outcome"})


def _is_validation_run(run_id: int) -> bool:
    """One primary-key read. Errors read as "not a validation run" — the
    worst case is a notification for a validation run, never a lost one."""
    try:
        from sqlmodel import Session
        from app.services.llm_usage import _engine
        with Session(_engine) as s:
            run = s.get(TestRun, run_id)
            return getattr(run, "is_validation", False) is True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Finalize] validation lookup failed for run %s: %s", run_id, e)
        return False


def _claim_statement(run_id: int):
    return (
        update(TestRun)
        .where(TestRun.id == run_id, TestRun.finalized_at.is_(None))
        .values(finalized_at=datetime.utcnow())
    )


def claim_sync(session, run_id: int) -> bool:
    """Claim the run inside the caller's transaction. True = we won.

    The row stays locked until the caller commits, so a concurrent claimer
    blocks and then sees ``finalized_at`` set (READ COMMITTED re-evaluates
    the WHERE) and updates nothing.
    """
    result = session.execute(_claim_statement(run_id))
    return getattr(result, "rowcount", 0) == 1


async def claim_async(session, run_id: int) -> bool:
    """Claim and COMMIT (the endpoint has nothing else to write first)."""
    result = await session.exec(_claim_statement(run_id))
    await session.commit()
    return getattr(result, "rowcount", 0) == 1


def queue_side_effects(run_id: int, payload: Optional[Dict[str, Any]] = None,
                       *, loader: Callable[[str], Any] = importlib.import_module,
                       is_validation: Optional[bool] = None) -> List[str]:
    """Queue every side-effect task; returns the names that were queued.

    One failing import or broker hiccup never stops the others.

    Also drops the run's exclusive-environment lease (WP16c) first, inline
    rather than as a task: every terminal path (aggregator completion, stale
    timeout, dispatch error, cancel, force-complete, legacy finalize) arrives
    here exactly once, and the next run waiting on that environment should not
    wait for a Celery round trip.

    Validation runs (WP16c, decision V4 — a proposed repair applied to one run
    only) get only the tasks in VALIDATION_RUN_TASKS: they must not notify,
    fire CI webhooks, page, update a monitor, propose heals, cluster failures
    or touch the security-finding lifecycle, since what ran is not the stored
    test.
    """
    from app.services.environment_lock import release_for_run
    release_for_run(run_id)
    validation = _is_validation_run(run_id) if is_validation is None else is_validation
    queued: List[str] = []
    for module_name, task_name, takes_payload in SIDE_EFFECT_TASKS:
        if validation and task_name not in VALIDATION_RUN_TASKS:
            continue
        try:
            task = getattr(loader(module_name), task_name)
            if takes_payload:
                task.delay(run_id, payload or {})
            else:
                task.delay(run_id)
            queued.append(task_name)
        except Exception as e:  # pragma: no cover - logged, never raised
            logger.warning("[Finalize] Could not queue %s for run %s: %s", task_name, run_id, e)
    return queued


def finalize_on_commit(session, run_id: int,
                       payload: Optional[Dict[str, Any]] = None) -> bool:
    """Claim the run now; queue the side effects once the transaction commits.

    Queued AFTER commit on purpose: a task picked up immediately must see the
    run's final status and results, which are written in this transaction.
    Returns whether this caller won the claim.
    """
    try:
        won = claim_sync(session, run_id)
    except Exception as e:
        logger.warning("[Finalize] Could not claim run %s: %s", run_id, e)
        return False
    if not won:
        return False
    try:
        validation = getattr(session.get(TestRun, run_id), "is_validation", False) is True
    except Exception:  # noqa: BLE001 — fall back to the lookup after commit
        validation = None
    try:
        from sqlalchemy import event
        event.listen(session, "after_commit",
                     lambda _s: queue_side_effects(run_id, payload, is_validation=validation),
                     once=True)
    except Exception as e:
        # Should not happen with a real Session; never lose the side effects
        # silently if it does.
        logger.warning("[Finalize] after_commit hook failed for run %s (%s); queueing now", run_id, e)
        queue_side_effects(run_id, payload, is_validation=validation)
    return True
