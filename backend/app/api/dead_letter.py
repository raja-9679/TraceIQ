"""Inspect and replay dead-lettered jobs — workstream H2.

`core/job-queue.ts` dead-letters a job after three failed claims and writes it to
the `jobs:dead-letter` stream. Nothing read that stream. The only signal was a
`console.error` every hundred loop iterations, and there was **no requeue path at
all** — a job that hit a transient worker crash three times was simply gone, and
its run stayed short one result forever.

Three endpoints, all instance-admin gated because a dead-letter payload contains
the job as dispatched, including resolved project secrets:

    GET    /api/admin/dead-letter          list entries (payloads redacted)
    POST   /api/admin/dead-letter/replay   requeue by id, or all
    DELETE /api/admin/dead-letter          discard entries

Replay re-adds the original payload to `jobs:pending` and **clears the retry
counter**, otherwise the job arrives already over its limit and is dead-lettered
again on first claim — a replay that silently does nothing is worse than no
replay button.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.instance_settings import get_current_instance_admin
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

DLQ_STREAM = "jobs:dead-letter"
JOBS_STREAM = "jobs:pending"
RETRY_HASH = "jobs:retries"


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


def summarize_entry(entry_id: Any, fields: Dict[Any, Any]) -> Dict[str, Any]:
    """One dead-letter entry, safe to return over the API.

    The stored payload is the job as dispatched, which carries resolved project
    secrets — so it is summarised, never echoed. Replay works from the stored
    copy, so nothing is lost by not showing it.
    """
    clean = {_decode(k): _decode(v) for k, v in (fields or {}).items()}
    payload = {}
    try:
        payload = json.loads(clean.get("payload") or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "entry_id": _decode(entry_id),
        "job_id": clean.get("job_id"),
        "run_id": clean.get("run_id"),
        "error": clean.get("error"),
        "test_case_id": payload.get("test_case_id"),
        "retry_count": payload.get("retry_count"),
        # Deliberately not the payload: it contains decrypted project secrets.
        "payload_present": bool(clean.get("payload")),
    }


async def _redis():
    from redis.asyncio import Redis

    from app.core.config import settings
    return Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


@router.get("/admin/dead-letter")
async def list_dead_letter(
    limit: int = 100,
    admin: User = Depends(get_current_instance_admin),
) -> Dict[str, Any]:
    """Dead-lettered jobs, newest first. Payloads are summarised, not echoed."""
    client = await _redis()
    try:
        depth = await client.xlen(DLQ_STREAM)
        entries = await client.xrevrange(DLQ_STREAM, count=max(1, min(limit, 500)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Cannot read the queue: {exc}")
    finally:
        await client.aclose() if hasattr(client, "aclose") else await client.close()

    return {
        "depth": depth,
        "entries": [summarize_entry(entry_id, fields) for entry_id, fields in entries],
    }


@router.post("/admin/dead-letter/replay")
async def replay_dead_letter(
    entry_ids: Optional[List[str]] = Body(default=None, embed=True),
    replay_all: bool = Body(default=False, embed=True),
    admin: User = Depends(get_current_instance_admin),
) -> Dict[str, Any]:
    """Requeue dead-lettered jobs onto `jobs:pending`.

    Clearing `jobs:retries` for the job is not optional: the counter is what
    dead-lettered it, so a replay that leaves it in place is re-killed on first
    claim. That would make this endpoint look like it worked while changing
    nothing.
    """
    if not entry_ids and not replay_all:
        raise HTTPException(status_code=400,
                            detail="Pass entry_ids, or replay_all=true")

    client = await _redis()
    replayed, skipped = [], []
    try:
        if replay_all:
            entries = await client.xrange(DLQ_STREAM)
        else:
            entries = []
            for entry_id in entry_ids or []:
                found = await client.xrange(DLQ_STREAM, min=entry_id, max=entry_id)
                if found:
                    entries.extend(found)
                else:
                    skipped.append({"entry_id": entry_id, "reason": "not found"})

        for entry_id, fields in entries:
            clean = {_decode(k): _decode(v) for k, v in fields.items()}
            payload = clean.get("payload")
            job_id = clean.get("job_id")
            if not payload:
                skipped.append({"entry_id": _decode(entry_id),
                                "reason": "no stored payload to replay"})
                continue
            pipe = client.pipeline()
            pipe.xadd(JOBS_STREAM, {"payload": payload})
            if job_id:
                pipe.hdel(RETRY_HASH, job_id)
            pipe.xdel(DLQ_STREAM, entry_id)
            await pipe.execute()
            replayed.append({"entry_id": _decode(entry_id), "job_id": job_id})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Replay failed: {exc}")
    finally:
        await client.aclose() if hasattr(client, "aclose") else await client.close()

    logger.info("[dead-letter] %s replayed %d job(s)", admin.email, len(replayed))
    return {"replayed": replayed, "replayed_count": len(replayed),
            "skipped": skipped}


@router.delete("/admin/dead-letter")
async def discard_dead_letter(
    entry_ids: Optional[List[str]] = Body(default=None, embed=True),
    discard_all: bool = Body(default=False, embed=True),
    admin: User = Depends(get_current_instance_admin),
) -> Dict[str, Any]:
    """Drop dead-letter entries without replaying them.

    Separate from replay on purpose: "I have looked at these and they are not
    coming back" is a different decision from "try again", and conflating them
    means an operator clearing a backlog can silently re-run production traffic.
    """
    if not entry_ids and not discard_all:
        raise HTTPException(status_code=400,
                            detail="Pass entry_ids, or discard_all=true")
    client = await _redis()
    try:
        if discard_all:
            removed = await client.xlen(DLQ_STREAM)
            await client.delete(DLQ_STREAM)
        else:
            removed = await client.xdel(DLQ_STREAM, *(entry_ids or []))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Discard failed: {exc}")
    finally:
        await client.aclose() if hasattr(client, "aclose") else await client.close()

    logger.warning("[dead-letter] %s discarded %s entr(ies)", admin.email, removed)
    return {"discarded": removed}
