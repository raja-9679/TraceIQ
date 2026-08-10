"""Operational observability — Prometheus metrics, readiness, queue health.

Three endpoints, three audiences:
    GET /metrics                  Prometheus scraper (text exposition). Set
                                  METRICS_TOKEN and scrape with a bearer token;
                                  without one configured, a logged-in principal
                                  is required. Never anonymous.
    GET /health/ready             orchestrator readiness probe: DB + Redis must
                                  answer (unlike /health, which is a bare 200)
    GET /api/admin/queue-health   humans/dashboards (JWT): queue depths,
                                  consumer-group backlogs, dead letters,
                                  stale runs, engine worker metrics
"""
import hmac
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import text
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_principal, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.models import TestRun, TestStatus, User

router = APIRouter()


def metrics_token_accepted(configured: str, authorization: Optional[str]) -> bool:
    """Whether an Authorization header satisfies the configured METRICS_TOKEN.

    An unset token never grants access — the caller falls back to requiring an
    authenticated principal instead. Comparison is constant-time so the token
    cannot be recovered a byte at a time.
    """
    if not configured or not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return False
    return hmac.compare_digest(value, configured)

_JOB_STREAMS = ("jobs:pending", "jobs:mobile:pending", "jobs:results", "jobs:dead-letter")


async def _redis():
    import redis.asyncio as redis
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


async def _queue_snapshot() -> Dict[str, Any]:
    """Depths + consumer-group backlogs for every job stream. Missing streams
    read as 0 rather than erroring (they're created lazily on first dispatch)."""
    snapshot: Dict[str, Any] = {"streams": {}, "groups": [], "engine_metrics": {}}
    r = await _redis()
    try:
        for stream in _JOB_STREAMS:
            try:
                snapshot["streams"][stream] = await r.xlen(stream)
            except Exception:
                snapshot["streams"][stream] = 0
            try:
                for group in await r.xinfo_groups(stream):
                    if isinstance(group, dict):
                        snapshot["groups"].append({
                            "stream": stream,
                            "group": group.get("name"),
                            "pending": group.get("pending", 0),
                            "consumers": group.get("consumers", 0),
                        })
            except Exception:
                pass  # stream or group not created yet
        try:
            # Written by the engine's metrics-collector (queue/worker/throughput).
            snapshot["engine_metrics"] = await r.hgetall("controller:metrics") or {}
        except Exception:
            pass
    finally:
        await r.close()
    return snapshot


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
) -> Response:
    """Prometheus text exposition. Hand-rendered (a dozen gauges — not worth a
    client dependency). Aggregate counts only; nothing tenant-identifiable —
    but queue depth and run volume are still operational intelligence about a
    deployment, so this is never anonymous.

    Scrapers set METRICS_TOKEN and send `Authorization: Bearer <token>`. With
    no token configured, any authenticated principal may read it.
    """
    if not metrics_token_accepted(settings.METRICS_TOKEN, authorization):
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1]
        try:
            await get_current_principal(request, token, session)
        except HTTPException:
            raise HTTPException(
                status_code=401,
                detail="Metrics require a METRICS_TOKEN bearer token or an authenticated session",
                headers={"WWW-Authenticate": "Bearer"},
            )

    lines = [
        "# HELP traceiq_runs_total Test runs by status (all time)",
        "# TYPE traceiq_runs_total gauge",
    ]
    result = await session.exec(
        select(TestRun.status, func.count(TestRun.id)).group_by(TestRun.status)
    )
    for status, count in result.all():
        value = status.value if hasattr(status, "value") else str(status)
        lines.append(f'traceiq_runs_total{{status="{value}"}} {count}')

    hour_ago = await session.exec(
        select(func.count(TestRun.id)).where(
            TestRun.created_at > func.now() - text("interval '1 hour'")
        )
    )
    lines += [
        "# HELP traceiq_runs_created_last_hour Runs created in the last hour",
        "# TYPE traceiq_runs_created_last_hour gauge",
        f"traceiq_runs_created_last_hour {hour_ago.one_or_none() or 0}",
    ]

    snapshot = await _queue_snapshot()
    lines += [
        "# HELP traceiq_queue_depth Entries in each Redis job stream",
        "# TYPE traceiq_queue_depth gauge",
    ]
    for stream, depth in snapshot["streams"].items():
        lines.append(f'traceiq_queue_depth{{stream="{stream}"}} {depth}')
    lines += [
        "# HELP traceiq_queue_group_pending Claimed-but-unacked jobs per consumer group",
        "# TYPE traceiq_queue_group_pending gauge",
        "# HELP traceiq_queue_group_consumers Live consumers per group",
        "# TYPE traceiq_queue_group_consumers gauge",
    ]
    for g in snapshot["groups"]:
        label = f'stream="{g["stream"]}",group="{g["group"]}"'
        lines.append(f"traceiq_queue_group_pending{{{label}}} {g['pending']}")
        lines.append(f"traceiq_queue_group_consumers{{{label}}} {g['consumers']}")

    # celery_beat proof of life (workstream H1). Beat drains jobs:results every
    # two seconds; when it dies nothing finalises, aggregates, schedules or
    # expires, and the only previous symptom was a user asking why their run had
    # been "running" for an hour. -1 means "never reported" so an alert rule can
    # tell "not running yet" from "stopped".
    from app.services.beat_health import read_beat_health
    beat = await read_beat_health()
    lines += [
        "# HELP traceiq_beat_heartbeat_age_seconds Seconds since celery_beat last reported (-1 = never)",
        "# TYPE traceiq_beat_heartbeat_age_seconds gauge",
        f"traceiq_beat_heartbeat_age_seconds {beat.age_seconds if beat.age_seconds is not None else -1}",
        "# HELP traceiq_beat_healthy 1 when celery_beat is reporting within BEAT_STALE_SECONDS",
        "# TYPE traceiq_beat_healthy gauge",
        f"traceiq_beat_healthy {1 if beat.healthy else 0}",
    ]

    # Dead-lettered jobs (H2). Already computed in the snapshot but never
    # exposed as a metric, so nothing could alert on it: dead jobs sat forever
    # and the only trace was a console.error every hundred loop iterations.
    lines += [
        "# HELP traceiq_dead_letter_depth Jobs that exhausted their retries and are awaiting replay",
        "# TYPE traceiq_dead_letter_depth gauge",
        f"traceiq_dead_letter_depth {snapshot['streams'].get('jobs:dead-letter', 0)}",
    ]

    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/health/beat", include_in_schema=False)
async def beat_health() -> Response:
    """Is the scheduler alive?

    Deliberately NOT folded into /health/ready: readiness gates load-balancer
    rotation, and pulling the API out of service because a scheduler is down
    turns a degraded system into an outage. This is a separate probe for
    monitoring to watch, and it is unauthenticated for the same reason
    /health/ready is — it exposes one timestamp and no tenant data.
    """
    import json

    from app.services.beat_health import read_beat_health

    health = await read_beat_health()
    return Response(
        json.dumps(health.as_dict()),
        # 200 for unknown: a just-started instance, or a deployment deliberately
        # running no beat, must not read as broken.
        status_code=503 if health.state in ("stale", "skewed") else 200,
        media_type="application/json",
    )


@router.get("/health/ready", include_in_schema=False)
async def readiness(session: AsyncSession = Depends(get_session)) -> Response:
    """Readiness: the app can actually serve — DB answers and Redis pings.
    503 with the failing dependency named, so probes and humans agree."""
    import json
    checks: Dict[str, str] = {}
    healthy = True

    start = time.monotonic()
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = f"ok ({(time.monotonic() - start) * 1000:.0f}ms)"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"fail: {exc}"[:200]
        healthy = False

    start = time.monotonic()
    try:
        r = await _redis()
        try:
            await r.ping()
        finally:
            await r.close()
        checks["redis"] = f"ok ({(time.monotonic() - start) * 1000:.0f}ms)"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"fail: {exc}"[:200]
        healthy = False

    return Response(
        json.dumps({"ready": healthy, "checks": checks}),
        status_code=200 if healthy else 503,
        media_type="application/json",
    )


@router.get("/api/admin/queue-health")
async def queue_health(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Authenticated operational summary for dashboards and debugging."""
    snapshot = await _queue_snapshot()

    stale = await session.exec(
        select(func.count(TestRun.id)).where(
            TestRun.status == TestStatus.RUNNING,
            TestRun.created_at < func.now() - text("interval '10 minutes'"),
        )
    )
    running = await session.exec(
        select(func.count(TestRun.id)).where(TestRun.status == TestStatus.RUNNING)
    )
    return {
        **snapshot,
        "runs_running": running.one_or_none() or 0,
        "runs_stale_10m": stale.one_or_none() or 0,
        "dead_letter_depth": snapshot["streams"].get("jobs:dead-letter", 0),
    }
