"""celery_beat's proof of life — workstream H1.

Beat is a single point of failure that stalls the whole execution pipeline when
it dies, and until now it died *silently*: no metric, no alert, no log anyone
reads. This writes a timestamp on a schedule so its absence is observable —
`traceiq_beat_heartbeat_age_seconds` in `/metrics`, and `/health/beat`.

The TTL matters. Without one, a heartbeat left by a beat process that died would
sit in Redis forever, and a monitor keyed on "does the key exist" would read
healthy. With it, the key disappears on its own — so the failure mode of this
mechanism is "reports unknown", not "reports healthy".
"""
import logging
from datetime import datetime

from app.core.celery_app import celery_app
from app.services.beat_health import HEARTBEAT_KEY

logger = logging.getLogger(__name__)

# Generous relative to the 30s tick: expiring at exactly the tick interval would
# make an ordinary scheduling jitter look like a death.
_TTL_SECONDS = 900


@celery_app.task(name="app.tasks.heartbeat_tasks.beat_heartbeat")
def beat_heartbeat():
    """Record that the scheduler is alive. Scheduled by beat itself.

    Runs on a worker rather than in the beat process, which is the point: it
    proves beat can *dispatch*, not merely that its own process exists. A beat
    that is running but cannot reach the broker is just as broken, and the
    original failure was exactly that class of silent stall.
    """
    import redis

    from app.core.config import settings

    stamp = datetime.utcnow().isoformat()
    try:
        client = redis.Redis.from_url(settings.CELERY_BROKER_URL,
                                      decode_responses=True,
                                      socket_connect_timeout=5)
        client.set(HEARTBEAT_KEY, stamp, ex=_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        # Nothing to fall back to: if Redis is unreachable the heartbeat cannot
        # be recorded, and the monitor correctly reports the scheduler as stale.
        logger.warning("[beat-heartbeat] could not record heartbeat: %s", exc)
        return None
    return stamp
