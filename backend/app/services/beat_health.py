"""Is celery_beat alive? — workstream H1.

There is one `celery_beat`, with no leader election, and it drains
`jobs:results` every two seconds. When it dies the whole execution pipeline
stalls: nothing finalises, nothing aggregates, no schedule fires, no retention
runs. Runs sit at RUNNING indefinitely.

What made that a production hazard was not the single point of failure — it was
that **nothing noticed**. No metric, no alert, no log anyone reads. The first
signal was a user asking why their run had been "running" for an hour.

Leader election (redbeat, see `docs/OPERATIONS.md`) lets you run two schedulers.
This module is the other half: beat writes a heartbeat every tick, and the
absence of one becomes a number Prometheus can alert on
(`traceiq_beat_heartbeat_age_seconds`) and a field on `/health/beat`.

Deliberately NOT wired into `/health/ready`: readiness gates load-balancer
rotation, and taking the API out of service because a scheduler is down would
turn a degraded system into an outage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "traceiq:beat:heartbeat"

# Beat ticks every 30s (see BEAT_HEARTBEAT_SECONDS). Three missed ticks is a real
# fault rather than a slow moment.
DEFAULT_STALE_AFTER = 180

# Beyond this, a future timestamp is a misconfigured clock rather than skew.
_SKEW_TOLERANCE = 120


@dataclass(frozen=True)
class BeatHealth:
    state: str                    # ok | stale | unknown | skewed
    age_seconds: Optional[int]
    last_tick: Optional[str]
    detail: str

    @property
    def healthy(self) -> bool:
        return self.state == "ok"

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "age_seconds": self.age_seconds,
            "last_tick": self.last_tick,
            "detail": self.detail,
        }


def evaluate_beat_health(raw_last_tick: Optional[str], *, now: datetime,
                         stale_after: int = DEFAULT_STALE_AFTER) -> BeatHealth:
    """Classify a heartbeat timestamp.

    "Never seen" is `unknown`, not `stale`: a just-started instance and a
    deployment deliberately running without beat both look like this, and
    neither should page anybody.
    """
    if not raw_last_tick:
        return BeatHealth(
            state="unknown", age_seconds=None, last_tick=None,
            detail="celery_beat has not reported a heartbeat yet. Normal for the "
                   "first minute after start; if it persists, beat is not running.")
    try:
        last = datetime.fromisoformat(str(raw_last_tick))
    except (TypeError, ValueError):
        return BeatHealth(
            state="unknown", age_seconds=None, last_tick=str(raw_last_tick),
            detail="heartbeat value is not a timestamp")

    age = (now - last).total_seconds()
    if age < -_SKEW_TOLERANCE:
        return BeatHealth(
            state="skewed", age_seconds=int(-age), last_tick=last.isoformat(),
            detail=f"celery_beat's clock is {int(-age)}s ahead of this process. "
                   "Heartbeat age cannot be trusted until the clocks agree.")
    # Small negative ages are ordinary skew between two containers. Clamped
    # rather than kept: a negative age compares as healthy forever.
    age = max(0, int(age))

    if age >= stale_after:
        return BeatHealth(
            state="stale", age_seconds=age, last_tick=last.isoformat(),
            detail=f"celery_beat last reported {age}s ago (limit {stale_after}s). "
                   "While it is down nothing finalises runs, aggregates results, "
                   "fires schedules or enforces retention — runs will stall at "
                   "RUNNING with no other symptom.")
    return BeatHealth(state="ok", age_seconds=age, last_tick=last.isoformat(),
                      detail="celery_beat is reporting normally")


def stale_after_seconds() -> int:
    from app.core.config import settings
    return int(getattr(settings, "BEAT_STALE_SECONDS", DEFAULT_STALE_AFTER)
               or DEFAULT_STALE_AFTER)


async def read_beat_health() -> BeatHealth:
    """Current heartbeat from Redis. Never raises — a Redis failure reports
    `unknown`, because this is a monitoring path and must not itself become the
    thing that breaks."""
    raw = None
    try:
        from app.core.redis import RedisClient
        raw = await RedisClient.get_instance().get(HEARTBEAT_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[beat-health] cannot read heartbeat: %s", exc)
        return BeatHealth(state="unknown", age_seconds=None, last_tick=None,
                          detail=f"cannot read the heartbeat from Redis: {exc}")
    return evaluate_beat_health(raw, now=datetime.utcnow(),
                                stale_after=stale_after_seconds())
