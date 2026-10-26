"""See and clear the jobs waiting in the worker queues.

    GET  /api/admin/job-queue         jobs no worker has finished yet, per stream
    POST /api/admin/job-queue/clear   cancel runs and drop their queued jobs

Instance-admin only, like the dead-letter endpoints next door: a queued job
is the job as dispatched, including resolved project secrets, so entries are
summarised (run, case, browser, age, who holds it) and the payload is never
echoed.

"Waiting" is not the stream: workers XACK but never XDEL, so a stream keeps
every finished job too. A job is waiting when it is after the consumer
group's last-delivered id (no worker has it yet) or in the group's pending
list (a worker claimed it and has not acknowledged it).

Clearing a job **cancels its run** — cancel is per run, so "clear this job"
means "clear every job of this run". Only deleting the entries would leave the
run "Running" until the stale sweep timed it out. Unclaimed entries are
deleted; claimed ones are left to their worker, which reads the cancel key
before every step and stops. Side jobs (crawls, page checks, persona
refreshes — `run_id` 0) have no run to cancel: unclaimed ones are deleted and
their collector records an error at its deadline.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.core.config import settings
from app.core.database import get_session
from app.models import Project, TestRun, User
from app.services import run_status, worker_fleet
from app.services.audit import record as audit_record

logger = logging.getLogger(__name__)

router = APIRouter()

# Read in batches when clearing; the list view is capped per stream.
SCAN_BATCH = 1000
LIST_MAX = 500


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


def _entry_ms(entry_id: str) -> Optional[int]:
    try:
        return int(str(entry_id).split("-", 1)[0])
    except (TypeError, ValueError):
        return None


def _run_id(value: Any) -> Optional[int]:
    try:
        rid = int(value)
    except (TypeError, ValueError):
        return None
    return rid if rid > 0 else None


def summarize_job(stream: str, entry_id: Any, fields: Dict[Any, Any],
                  claim: Optional[Dict[str, Any]] = None,
                  now_ms: Optional[int] = None) -> Dict[str, Any]:
    """One queued job, safe to return: names and ids, never the payload."""
    clean = {_decode(k): _decode(v) for k, v in (fields or {}).items()}
    try:
        job = json.loads(clean.get("payload") or "{}")
    except (TypeError, ValueError):
        job = {}
    if not isinstance(job, dict):
        job = {}
    case = job.get("test_case") if isinstance(job.get("test_case"), dict) else {}
    cases = job.get("test_cases") if isinstance(job.get("test_cases"), list) else []
    entry_id = _decode(entry_id)
    queued_ms = _entry_ms(entry_id)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return {
        "stream": stream,
        "entry_id": entry_id,
        "job_id": clean.get("job_id"),
        "run_id": _run_id(clean.get("run_id") or job.get("run_id")),
        "job_type": job.get("job_type") or "test",
        "test_case_id": job.get("test_case_id") or case.get("id"),
        "test_case_name": case.get("name"),
        "case_count": len(cases) if cases else (1 if case else 0),
        "browser": job.get("browser"),
        "device": job.get("device"),
        "retry_count": job.get("retry_count"),
        "queued_at_ms": queued_ms,
        "waiting_seconds": round((now_ms - queued_ms) / 1000, 1) if queued_ms else None,
        "claimed": bool(claim),
        "consumer": (claim or {}).get("consumer"),
        "claimed_for_seconds": (round((claim or {}).get("idle_ms", 0) / 1000, 1)
                                if claim else None),
        "deliveries": (claim or {}).get("deliveries"),
    }


async def _last_delivered(r, stream: str, group: str) -> Tuple[str, Optional[int]]:
    """(the group's read position, its lag). "0-0" when no worker group
    exists yet — then everything in the stream is waiting."""
    try:
        for g in await r.xinfo_groups(stream):
            if _decode(g.get("name")) == group:
                lag = g.get("lag")
                return (_decode(g.get("last-delivered-id")) or "0-0",
                        int(lag) if lag is not None else None)
    except Exception:
        pass
    return "0-0", None


async def _claims(r, stream: str, group: str, count: int) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = await r.xpending_range(stream, group, min="-", max="+", count=count)
    except Exception:
        return out
    for p in rows or []:
        out[_decode(p.get("message_id"))] = {
            "consumer": _decode(p.get("consumer")),
            "idle_ms": int(p.get("time_since_delivered") or 0),
            "deliveries": int(p.get("times_delivered") or 0),
        }
    return out


async def waiting_in_stream(r, stream: str, group: str,
                            limit: Optional[int]) -> Tuple[List[Dict[str, Any]], int]:
    """(summaries, total waiting). `limit` None = read everything (clearing)."""
    now_ms = int(time.time() * 1000)
    cap = limit if limit is not None else 10 ** 9
    claims = await _claims(r, stream, group, min(cap, 100_000))
    out: List[Dict[str, Any]] = []

    for entry_id, claim in claims.items():
        if len(out) >= cap:
            break
        try:
            found = await r.xrange(stream, min=entry_id, max=entry_id)
        except Exception:
            found = []
        if found:  # an entry can be in the PEL after it was deleted
            out.append(summarize_job(stream, found[0][0], found[0][1], claim, now_ms))

    start, lag = await _last_delivered(r, stream, group)
    unclaimed_total = 0
    stopped_early = False
    while True:
        try:
            batch = await r.xrange(stream, min=f"({start}", max="+", count=SCAN_BATCH)
        except Exception:
            break
        if not batch:
            break
        for entry_id, fields in batch:
            unclaimed_total += 1
            if len(out) < cap:
                out.append(summarize_job(stream, entry_id, fields, None, now_ms))
        start = _decode(batch[-1][0])
        if len(batch) < SCAN_BATCH:
            break
        if limit is not None and len(out) >= cap:
            stopped_early = True
            break
    if stopped_early and lag is not None:
        # The list view stops reading at the cap; Redis knows the rest.
        unclaimed_total = max(unclaimed_total, lag)
    total = len(claims) + unclaimed_total
    return out, total


async def _streams(r) -> List[Tuple[str, str]]:
    return await worker_fleet._discover_streams(r)


async def _redis():
    import redis.asyncio as redis
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


async def _close(client) -> None:
    try:
        await client.aclose() if hasattr(client, "aclose") else await client.close()
    except Exception:
        pass


async def _run_info(session: AsyncSession, run_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    ids = sorted({i for i in run_ids if i})
    if not ids:
        return {}
    rows = (await session.exec(
        select(TestRun, Project.name)
        .join(Project, Project.id == TestRun.project_id, isouter=True)
        .where(TestRun.id.in_(ids))
    )).all()
    out = {}
    for run, project_name in rows:
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        out[run.id] = {
            "run_id": run.id,
            "status": status,
            "live": run.status in run_status.LIVE,
            "project_id": run.project_id,
            "project_name": project_name,
            "suite_name": run.suite_name,
            "test_case_name": run.test_case_name,
            "created_at": run.created_at.isoformat() if getattr(run, "created_at", None) else None,
        }
    return out


@router.get("/admin/job-queue")
async def list_job_queue(
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
) -> Dict[str, Any]:
    """Waiting jobs per stream, oldest claimed first, then oldest unclaimed."""
    limit = max(1, min(limit, LIST_MAX))
    client = await _redis()
    try:
        streams = []
        for stream, group in await _streams(client):
            jobs, total = await waiting_in_stream(client, stream, group, limit)
            streams.append({"stream": stream, "group": group, "total": total,
                            "shown": len(jobs), "jobs": jobs})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Cannot read the queue: {exc}")
    finally:
        await _close(client)

    runs = await _run_info(session, (j["run_id"] for s in streams for j in s["jobs"]))
    return {"generated_at": time.time(), "streams": streams,
            "runs": {str(k): v for k, v in runs.items()}}


class ClearRequest(BaseModel):
    run_ids: Optional[List[int]] = None
    # Side jobs only need this; a test job's entry cancels its run anyway.
    entries: Optional[List[Dict[str, str]]] = None
    clear_all: bool = False


@router.post("/admin/job-queue/clear")
async def clear_job_queue(
    request: Request,
    body: ClearRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
) -> Dict[str, Any]:
    """Cancel the runs and delete their waiting jobs.

    Pass `run_ids`, `entries` ([{stream, entry_id}]) or `clear_all: true`.
    """
    from app.services.run_cancel import RunNotCancellable, cancel_run

    want_runs = {int(r) for r in (body.run_ids or []) if int(r) > 0}
    want_entries = {(e.get("stream"), e.get("entry_id")) for e in (body.entries or [])}
    if not body.clear_all and not want_runs and not want_entries:
        raise HTTPException(status_code=400,
                            detail="Pass run_ids, entries, or clear_all=true")

    client = await _redis()
    try:
        streams = await _streams(client)
        known = {s for s, _ in streams}
        unknown = [s for s, _ in want_entries if s not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Not a job queue: {unknown[0]}")

        targets: List[Dict[str, Any]] = []
        for stream, group in streams:
            jobs, _total = await waiting_in_stream(client, stream, group, None)
            for job in jobs:
                if (body.clear_all or job["run_id"] in want_runs
                        or (stream, job["entry_id"]) in want_entries):
                    targets.append(job)

        run_ids = want_runs | {j["run_id"] for j in targets if j["run_id"]}
        runs = await _run_info(session, run_ids)
        cancelled, already_finished = [], []
        for rid in sorted(run_ids):
            run = await session.get(TestRun, rid)
            if run is None:
                continue
            info = runs.get(rid) or {}
            if not info.get("live"):
                already_finished.append(rid)
                continue
            project = await session.get(Project, run.project_id) if run.project_id else None
            # Added before the conditional update so it commits with the
            # cancel, and rolls back with it when the run had just finished.
            await audit_record(
                session, entity_type="test_run", entity_id=rid, action="cancel",
                workspace_id=project.workspace_id if project else None,
                user_id=admin.id, request=request,
                changes={"previous_status": info.get("status"),
                         "project_id": run.project_id, "via": "job_queue"},
            )
            try:
                await cancel_run(session, run, redis=client,
                                 reason="Cleared from the job queue by an administrator.")
                cancelled.append(rid)
            except RunNotCancellable:
                already_finished.append(rid)

        deleted, left_running = 0, 0
        by_stream: Dict[str, List[str]] = {}
        for job in targets:
            if job["claimed"]:
                left_running += 1
            else:
                by_stream.setdefault(job["stream"], []).append(job["entry_id"])
        for stream, ids in by_stream.items():
            for i in range(0, len(ids), 500):
                deleted += int(await client.xdel(stream, *ids[i:i + 500]) or 0)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Clear failed: {exc}")
    finally:
        await _close(client)

    await audit_record(
        session, entity_type="job_queue", entity_id=0, action="clear",
        user_id=admin.id, request=request,
        changes={"clear_all": body.clear_all, "runs_cancelled": len(cancelled),
                 "jobs_deleted": deleted, "jobs_left_running": left_running},
    )
    await session.commit()
    logger.info("[job-queue] %s cleared %d job(s), cancelled %d run(s)",
                admin.email, deleted, len(cancelled))
    return {
        "cancelled_run_ids": cancelled,
        "already_finished_run_ids": already_finished,
        "jobs_deleted": deleted,
        # Claimed by a worker: it stops at its next step (runs) or finishes
        # (side jobs). Deleting the entry would not stop it.
        "jobs_left_running": left_running,
    }
