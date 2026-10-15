"""Execution-worker fleet: heartbeats, stream backlog, version skew (WP8).

Workers (web `worker.ts`, `mobile-worker.ts`) write a heartbeat every
HEARTBEAT_INTERVAL seconds straight to Redis — they already hold a Redis
connection, and routing proof-of-life through the API would make "the API is
down" look like "every worker is dead". Local (developer) workers have no
Redis access and report through `POST /api/jobs/heartbeat` instead, which
writes the same shape here.

Keys:
    workers:hb:<worker_id>   JSON payload, EX HEARTBEAT_TTL. Its absence is
                             the death signal — a crashed worker cannot say so.
    workers:index            ZSET worker_id -> last-seen epoch, so a worker
                             that just died is still listed (as lost) for
                             LOST_RETENTION seconds instead of silently
                             vanishing from the page.

The worker id is the worker's Redis consumer name, so XPENDING's per-consumer
counts join directly onto the heartbeat.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional

HEARTBEAT_PREFIX = "workers:hb:"
INDEX_KEY = "workers:index"
HEARTBEAT_INTERVAL = 15
HEARTBEAT_TTL = 45
LOST_RETENTION = 600

GLOBAL_STREAMS = (("jobs:pending", "execution-workers"),
                  ("jobs:mobile:pending", "mobile-workers"))
DEAD_LETTER_STREAM = "jobs:dead-letter"

# Catalog entries that declare an executor rather than run in the step switch
# (a `load-test` step makes the case executor=load, run by k6). A worker not
# listing them is not out of date.
EXECUTOR_ONLY_STEP_TYPES = frozenset({"load-test"})

REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class InvalidRegion(ValueError):
    pass


def normalize_region(value: Optional[str]) -> Optional[str]:
    """Validate a region label. Labels become Redis key suffixes, so they are
    restricted to a safe alphabet; blank means "no region"."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if not REGION_RE.match(text):
        raise InvalidRegion(
            f"invalid region {value!r}: use 1-32 lowercase letters, digits or '-'")
    return text


class RegionNotPermitted(PermissionError):
    pass


def parse_region_grants(entries: Optional[Iterable[str]]) -> Dict[str, Optional[set]]:
    """`WORKER_REGIONS` entries → {region: None (every workspace) | {ws ids}}.

    `eu` opens the region to every workspace; `acme-onprem=12,15` restricts it
    to those workspaces. A malformed restriction grants nobody rather than
    everybody — a line meant to restrict a region must never open it.
    """
    grants: Dict[str, Optional[set]] = {}
    for raw in entries or []:
        text = str(raw or "").strip()
        if not text:
            continue
        name, sep, rest = text.partition("=")
        try:
            region = normalize_region(name)
        except InvalidRegion:
            continue
        if not region:
            continue
        if not sep:
            # An open line never widens a restriction stated elsewhere.
            grants.setdefault(region, None)
            continue
        parts = [p for p in re.split(r"[\s,;]+", rest.strip()) if p]
        ids = {int(p) for p in parts} if parts and all(p.isdigit() for p in parts) else set()
        # A restriction we cannot read grants nobody, and any restricted line
        # turns an open region into a restricted one.
        if grants.get(region) is None:
            grants[region] = ids
        else:
            grants[region] |= ids
    return grants


def check_region_access(region: Optional[str], workspace_id: Optional[int],
                        entries: Optional[Iterable[str]] = None) -> None:
    """Refuse a region the caller's workspace may not dispatch to.

    A region is a routing label, and the workers behind it may sit inside one
    customer's network (`WORKER_REGION_EXCLUSIVE`). Letting any caller pick
    any label would let tenant A run its journeys — with its resolved secrets —
    on tenant B's workers, and use them to reach B's internal network. So a
    region must be declared by the instance admin in `WORKER_REGIONS`; an
    undeclared region is refused, never treated as open.
    """
    if not region:
        return
    if entries is None:
        from app.services.instance_settings import effective
        entries = effective("WORKER_REGIONS") or []
    grants = parse_region_grants(entries)
    if region not in grants:
        raise RegionNotPermitted(
            f"region {region!r} is not configured on this instance "
            "(an instance admin declares regions in WORKER_REGIONS)")
    allowed = grants[region]
    if allowed is not None and (workspace_id is None or int(workspace_id) not in allowed):
        raise RegionNotPermitted(f"region {region!r} is not available to this workspace")


def stream_for(base: str, region: Optional[str]) -> str:
    """`jobs:pending` + region → `jobs:pending:<region>`. No region = the base
    stream, which is what makes unlabelled deployments behave as before."""
    return f"{base}:{region}" if region else base


def catalog_step_types() -> List[str]:
    from app.api.agent_reference import _STEP_TYPES
    return sorted({s["type"] for s in _STEP_TYPES})


def missing_step_types(kind: str, worker_types: Iterable[str],
                       catalog: Iterable[str]) -> List[str]:
    """Catalog step types this worker cannot execute. Web workers are judged
    against the non-mobile catalog, mobile workers against `mobile-*` only.
    A worker that reported no list at all (an image older than WP8) is not
    judged — 'unknown' is reported separately rather than 'everything missing'."""
    have = set(worker_types or [])
    if not have:
        return []
    if kind == "mobile":
        wanted = {t for t in catalog if t.startswith("mobile-")}
    else:
        wanted = {t for t in catalog
                  if not t.startswith("mobile-") and t not in EXECUTOR_ONLY_STEP_TYPES}
    return sorted(wanted - have)


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def record_heartbeat(r, payload: Dict[str, Any]) -> None:
    """Write one heartbeat (used for local workers; server workers write the
    same keys from Node)."""
    worker_id = str(payload["worker_id"])
    now = time.time()
    payload = {**payload, "last_seen": now}
    pipe = r.pipeline()
    pipe.set(HEARTBEAT_PREFIX + worker_id, json.dumps(payload), ex=HEARTBEAT_TTL)
    pipe.zadd(INDEX_KEY, {worker_id: now})
    await pipe.execute()


async def _discover_streams(r) -> List[tuple]:
    """Global streams plus every regional `jobs:*pending:<region>` stream."""
    found = list(GLOBAL_STREAMS)
    seen = {s for s, _ in found}
    for base, group in GLOBAL_STREAMS:
        try:
            async for key in r.scan_iter(match=f"{base}:*", count=200):
                name = _decode(key)
                region = name[len(base) + 1:]
                # jobs:pending:<region> only — not jobs:pending:something:else
                if name not in seen and REGION_RE.match(region or ""):
                    seen.add(name)
                    found.append((name, group))
        except Exception:
            continue
    return found


async def stream_report(r) -> List[Dict[str, Any]]:
    out = []
    for stream, group in await _discover_streams(r):
        entry: Dict[str, Any] = {"stream": stream, "group": group, "length": 0,
                                 "lag": None, "pending": 0, "consumers": []}
        try:
            entry["length"] = int(await r.xlen(stream))
        except Exception:
            pass
        try:
            for g in await r.xinfo_groups(stream):
                if _decode(g.get("name")) == group:
                    entry["pending"] = int(g.get("pending") or 0)
                    lag = g.get("lag")
                    entry["lag"] = int(lag) if lag is not None else None
        except Exception:
            pass
        try:
            summary = await r.xpending(stream, group)
            entry["consumers"] = [
                {"name": _decode(c.get("name")), "pending": int(c.get("pending") or 0)}
                for c in (summary or {}).get("consumers") or []
            ]
        except Exception:
            pass
        base = "jobs:mobile:pending" if stream.startswith("jobs:mobile:pending") else "jobs:pending"
        entry["region"] = stream[len(base) + 1:] if stream != base else None
        out.append(entry)
    return out


async def fleet_snapshot(r) -> Dict[str, Any]:
    """Everything the fleet page and the Prometheus gauges need."""
    now = time.time()
    catalog = catalog_step_types()
    workers: List[Dict[str, Any]] = []
    stale: List[str] = []
    try:
        index = await r.zrange(INDEX_KEY, 0, -1, withscores=True)
    except Exception:
        index = []
    ids = [_decode(w) for w, _ in index]
    last = {_decode(w): float(s) for w, s in index}
    payloads = await r.mget([HEARTBEAT_PREFIX + w for w in ids]) if ids else []
    for worker_id, raw in zip(ids, payloads):
        if raw:
            try:
                hb = json.loads(_decode(raw))
            except ValueError:
                hb = {}
            alive = True
        else:
            if now - last.get(worker_id, 0) > LOST_RETENTION:
                stale.append(worker_id)
                continue
            hb = {}
            alive = False
        # Both end up as Prometheus label values and in the UI: anything a
        # worker reports outside the expected alphabet is dropped, not echoed.
        kind = hb.get("kind") if hb.get("kind") in ("web", "mobile", "local") else "web"
        region = hb.get("region") if REGION_RE.match(str(hb.get("region") or "")) else None
        types = hb.get("step_types") or []
        missing = missing_step_types(kind, types, catalog)
        workers.append({
            "worker_id": worker_id,
            "alive": alive,
            "last_seen": last.get(worker_id),
            "age_seconds": round(now - last.get(worker_id, now), 1),
            "kind": kind,
            "hostname": hb.get("hostname"),
            "version": hb.get("version"),
            "git_sha": hb.get("git_sha"),
            "region": region,
            "streams": hb.get("streams") or [],
            "capabilities": hb.get("capabilities") or {},
            "current_job": hb.get("current_job"),
            "jobs_processed": hb.get("jobs_processed", 0),
            "jobs_failed": hb.get("jobs_failed", 0),
            "started_at": hb.get("started_at"),
            "workspace_id": hb.get("workspace_id"),
            "step_types_reported": bool(types),
            "missing_step_types": missing,
            "version_skew": bool(missing),
        })
    if stale:
        try:
            await r.zrem(INDEX_KEY, *stale)
        except Exception:
            pass

    streams = await stream_report(r)
    try:
        dlq = int(await r.xlen(DEAD_LETTER_STREAM))
    except Exception:
        dlq = 0

    served = {s for w in workers if w["alive"] for s in w["streams"]}
    warnings: List[str] = []
    for s in streams:
        backlog = (s["lag"] or 0) + s["pending"]
        if s["region"] and s["stream"] not in served and backlog:
            warnings.append(
                f"{s['stream']} has queued jobs but no live worker in region "
                f"'{s['region']}' — they will wait until one starts")
        elif not s["region"] and backlog and s["stream"] not in served \
                and any(w["alive"] for w in workers):
            warnings.append(f"{s['stream']} has queued jobs but no live worker consumes it")
    skewed = [w for w in workers if w["alive"] and w["version_skew"]]
    if skewed:
        warnings.append(
            f"{len(skewed)} live worker(s) are older than the backend's step catalog — "
            "cases using the missing step types will fail on them; rebuild the worker image")
    if dlq:
        warnings.append(f"{dlq} job(s) in the dead-letter queue")

    return {
        "generated_at": now,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL,
        "heartbeat_ttl_seconds": HEARTBEAT_TTL,
        "workers": sorted(workers, key=lambda w: (not w["alive"], w["kind"], w["worker_id"])),
        "streams": streams,
        "dead_letter_depth": dlq,
        "catalog_step_types": catalog,
        "warnings": warnings,
        "summary": {
            "alive": sum(1 for w in workers if w["alive"]),
            "lost": sum(1 for w in workers if not w["alive"]),
            "busy": sum(1 for w in workers if w["alive"] and w["current_job"]),
            "skewed": len(skewed),
        },
    }
