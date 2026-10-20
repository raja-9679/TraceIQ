"""Cancelling a run (WP15f L1).

The run is marked CANCELLED in the database first — that is the decision,
and nothing afterwards can turn it back into a pass or failure (the
aggregator keeps a cancelled run's status; see run_status). Workers learn of
it through a Redis key they read before every step
(``execution-engine/src/core/run-control.ts``):

* jobs still queued are reported ``cancelled`` without starting a browser;
* a running case stops before its next step — the step in flight finishes
  (Playwright cannot be interrupted mid-call), then suite teardown and
  cleanups run as after any failure (WP5 contract);
* local-worker jobs not yet polled are dropped.

Cancelled runs send no notifications and never page; outbound webhooks get a
``run.cancelled`` event; monitors record no check. The cancel is audited.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import update

from app.models import TestRun, TestStatus
from app.services import run_status

logger = logging.getLogger(__name__)

# Must match execution-engine/src/core/run-control.ts cancelKey().
CANCEL_KEY = "run:cancelled:{}"
# Long enough for any job still queued behind a busy fleet to see it.
CANCEL_TTL_SECONDS = 72 * 3600

CANCELLED_MESSAGE = "Run cancelled."


class RunNotCancellable(Exception):
    def __init__(self, status: TestStatus):
        super().__init__(f"Run already finished ({status.value})")
        self.status = status


async def cancel_run(session, run: TestRun, *, redis=None,
                     reason: Optional[str] = None) -> Dict[str, Any]:
    """Cancel a live run. Raises RunNotCancellable when it already finished.

    Conditional on the run still being live, so a cancel racing the
    aggregator's own finish loses cleanly instead of overwriting a verdict.
    Commits. Returns what happened for the API response.
    """
    now = datetime.utcnow()
    message = CANCELLED_MESSAGE if not reason else f"{CANCELLED_MESSAGE} {reason}"
    result = await session.exec(
        update(TestRun)
        .where(TestRun.id == run.id, TestRun.status.in_(run_status.LIVE))
        .values(status=TestStatus.CANCELLED, cancelled_at=now, error_message=message[:500])
    )
    if getattr(result, "rowcount", 0) != 1:
        await session.rollback()
        await session.refresh(run)
        raise RunNotCancellable(run.status)
    await session.commit()
    await session.refresh(run)

    workers_notified = await _notify_workers(run.id, redis)

    from app.services.run_completion import claim_async, queue_side_effects
    finalized = await claim_async(session, run.id)
    if finalized:
        queue_side_effects(run.id, {})

    return {
        "run_id": run.id,
        "status": run.status.value,
        "cancelled_at": now.isoformat(),
        "workers_notified": workers_notified,
    }


async def _notify_workers(run_id: int, redis=None) -> bool:
    """Set the cancel key. False when Redis is unreachable: the run is still
    cancelled (its status is final), queued jobs just run to no effect."""
    try:
        if redis is None:
            from app.core.redis import RedisClient
            redis = RedisClient.get_instance()
        await redis.set(CANCEL_KEY.format(run_id), "1", ex=CANCEL_TTL_SECONDS)
        return True
    except Exception as e:
        logger.warning("[Cancel] Could not notify workers for run %s: %s", run_id, e)
        return False
