"""Exclusive environments (WP16c, decision V3).

A project environment marked ``exclusive`` runs one TestRun at a time — a
shared staging database, a payment sandbox with one test merchant, a device
lab with one device. The lock is a Redis lease taken when the dispatcher
ADMITS the run (policy engine, ``run.dispatch``/admit, rule
``environment.exclusive_lock``); a run that finds the environment held is
DEFERRED exactly like the concurrency cap: it stays PENDING and is re-queued
with a countdown. It never fails because someone else is using the target.

Keys (all expire, so nothing here can outlive Redis' own TTLs):

    envlock:env:<env id>    value = holder run id   (the lease itself)
    envlock:meta:<env id>   hash: run_id, since, environment, project_id
    envlock:run:<run id>    value = env id          (reverse index for release)
    envlock:wait:<run id>   JSON: what a deferred run is waiting for

Liveness. The lease TTL is LEASE_TTL_SECONDS; the beat task
``refresh_environment_leases`` (every HEARTBEAT_SECONDS) renews leases whose
holder is still RUNNING and drops the rest. The beat, not the aggregator, is
the heartbeat on purpose: a run's results can legitimately be minutes apart
(one long case), while "is the run still live?" is a database fact the beat
can read every 30 s. A crashed worker leaves its run RUNNING only until
``check_stale_runs`` times it out (no progress for 15 min), which finalizes
the run and so releases the lease; if the beat itself dies the lease simply
expires. A PENDING holder (admitted, but the dispatcher died before marking
it RUNNING) is renewed only for PENDING_GRACE_SECONDS.

Release happens on EVERY terminal transition: ``run_completion.
queue_side_effects`` calls ``release_for_run`` (completion, error, timeout,
cancel, force-complete and the legacy finalize all go through it), and the
dispatcher's exception path releases explicitly as well. Release is
compare-and-delete — a run can only ever drop its own lease.

A run uses at most one environment, so runs cannot deadlock on each other.
Several runs from one POST (browser/device matrix, personas, viewports) on an
exclusive environment queue behind each other.

Redis errors fail OPEN (the run is admitted), like the concurrency cap:
dispatch needs Redis for the job stream anyway, so a closed lock would only
turn an outage into a pile of deferred runs.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

LEASE_TTL_SECONDS = 180
HEARTBEAT_SECONDS = 30
RETRY_SECONDS = 20
PENDING_GRACE_SECONDS = 600
WAIT_TTL_SECONDS = 120

_KEY = "envlock:env:{}"
_META = "envlock:meta:{}"
_RUN = "envlock:run:{}"
_WAIT = "envlock:wait:{}"

# Compare-and-delete / compare-and-expire: only the holder touches its lease.
_CAD = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end")
_CAE = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end")

_client = None


def client():
    """Sync Redis client (Celery + after-commit hooks). Tests replace `_client`."""
    global _client
    if _client is None:
        import redis
        from app.core.config import settings
        _client = redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
    return _client


# ---------------------------------------------------------------------------
# Which environment a run uses
# ---------------------------------------------------------------------------

def environment_for_run(run: Any, project_id: Optional[int], session: Any):
    """The run's pinned environment, else the project default (the same rule
    the dispatcher uses to build the job's {{env.*}} values)."""
    from sqlmodel import select
    from app.models import ProjectEnvironment
    env_id = getattr(run, "environment_id", None)
    if env_id:
        return session.get(ProjectEnvironment, env_id)
    if not project_id:
        return None
    return session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == project_id,
        ProjectEnvironment.is_default == True)).first()  # noqa: E712


def lock_target(run: Any, session: Any):
    """The exclusive environment this run must hold, or None.

    Comparison / benchmark runs (`target_url`) are exempt: the target replaces
    the environment's base URL, so they are not using that environment's
    deployment."""
    if getattr(run, "target_url", None):
        return None
    project_id = getattr(run, "project_id", None)
    if not getattr(run, "environment_id", None) and not project_id:
        return None
    if not hasattr(session, "exec"):
        return None
    env = environment_for_run(run, project_id, session)
    if env is None or not getattr(env, "exclusive", False):
        return None
    return env


# ---------------------------------------------------------------------------
# Lease operations
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def holder(env_id: int, *, r=None) -> Optional[Dict[str, Any]]:
    """Who holds the environment: {run_id, since, environment} or None."""
    r = r or client()
    value = r.get(_KEY.format(env_id))
    if not value:
        return None
    meta = r.hgetall(_META.format(env_id)) or {}
    try:
        run_id = int(value)
    except (TypeError, ValueError):
        return None
    return {"run_id": run_id, "since": meta.get("since"),
            "environment": meta.get("environment")}


def try_acquire(run: Any, session: Any, *, r=None) -> Dict[str, Any]:
    """Take (or keep) the lease for the run's exclusive environment.

    Returns {"acquired": bool, "environment_id", "environment", "holder"}.
    `acquired` is True when the run needs no lock at all."""
    env = lock_target(run, session)
    if env is None:
        return {"acquired": True, "environment_id": None}
    out: Dict[str, Any] = {"environment_id": env.id, "environment": env.name}
    try:
        r = r or client()
        key, run_value = _KEY.format(env.id), str(run.id)
        if r.set(key, run_value, nx=True, ex=LEASE_TTL_SECONDS):
            r.hset(_META.format(env.id), mapping={
                "run_id": run_value, "since": _now(), "environment": env.name,
                "project_id": str(getattr(env, "project_id", "") or "")})
            r.expire(_META.format(env.id), LEASE_TTL_SECONDS)
            r.set(_RUN.format(run.id), str(env.id), ex=LEASE_TTL_SECONDS)
            r.delete(_WAIT.format(run.id))
            return {**out, "acquired": True}
        current = holder(env.id, r=r)
        if current and current["run_id"] == run.id:
            # Re-admitted (Celery redelivery): it is already ours.
            _extend(r, env.id, run.id)
            r.delete(_WAIT.format(run.id))
            return {**out, "acquired": True}
        if current is None:
            # Released between our SET and GET: try once more next round.
            current = {"run_id": None, "since": None}
        r.set(_WAIT.format(run.id), json.dumps({
            "environment_id": env.id, "environment": env.name,
            "holder_run_id": current["run_id"], "holder_since": current.get("since"),
            "checked_at": _now()}), ex=WAIT_TTL_SECONDS)
        return {**out, "acquired": False, "holder": current}
    except Exception as exc:  # noqa: BLE001 — fail open, see module docstring
        print(f"[EnvLock] lease check failed for run {getattr(run, 'id', None)} "
              f"(admitting): {exc}")
        return {**out, "acquired": True, "error": str(exc)}


def _extend(r, env_id: int, run_id: int) -> bool:
    ok = bool(r.eval(_CAE, 1, _KEY.format(env_id), str(run_id), LEASE_TTL_SECONDS))
    if ok:
        r.expire(_META.format(env_id), LEASE_TTL_SECONDS)
        r.set(_RUN.format(run_id), str(env_id), ex=LEASE_TTL_SECONDS)
    return ok


def release_for_run(run_id: int, *, r=None) -> Optional[int]:
    """Drop whatever lease this run holds. Returns the env id released, else
    None. Never raises (callers are completion paths)."""
    try:
        r = r or client()
        r.delete(_WAIT.format(run_id))
        env_value = r.get(_RUN.format(run_id))
        if not env_value:
            return None
        env_id = int(env_value)
        released = bool(r.eval(_CAD, 1, _KEY.format(env_id), str(run_id)))
        if released:
            r.delete(_META.format(env_id))
        r.delete(_RUN.format(run_id))
        return env_id if released else None
    except Exception as exc:  # noqa: BLE001 — the TTL is the backstop
        print(f"[EnvLock] release for run {run_id} failed (lease will expire): {exc}")
        return None


def force_release(env_id: int, *, r=None) -> Optional[Dict[str, Any]]:
    """Editor override: drop the lease whoever holds it. Returns the previous
    holder. The holder run keeps running; it just no longer blocks others."""
    r = r or client()
    current = holder(env_id, r=r)
    if current is None:
        return None
    if r.eval(_CAD, 1, _KEY.format(env_id), str(current["run_id"])):
        r.delete(_META.format(env_id))
        r.delete(_RUN.format(current["run_id"]))
    return current


def held_by_run(run_id: int, *, r=None):
    """(env id, holder dict) when this run holds a lease, else (None, None)."""
    r = r or client()
    env_value = r.get(_RUN.format(run_id))
    if not env_value:
        return None, None
    current = holder(int(env_value), r=r)
    if current and current["run_id"] == run_id:
        return int(env_value), current
    return None, None


def waiting(run_id: int, *, r=None) -> Optional[Dict[str, Any]]:
    """What a deferred run is waiting for, or None."""
    r = r or client()
    raw = r.get(_WAIT.format(run_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def refresh(session: Any, *, r=None, now: Optional[datetime] = None) -> Dict[str, int]:
    """Heartbeat: renew leases of live holders, drop the rest."""
    from app.models import TestRun, TestStatus
    r = r or client()
    now = now or datetime.utcnow()
    counts = {"renewed": 0, "released": 0}
    for key in r.scan_iter(match=_KEY.format("*"), count=200):
        try:
            env_id = int(key.rsplit(":", 1)[1])
        except ValueError:
            continue
        current = holder(env_id, r=r)
        if current is None:
            continue
        run = session.get(TestRun, current["run_id"])
        status = getattr(run, "status", None)
        keep = status == TestStatus.RUNNING
        if status == TestStatus.PENDING:
            try:
                since = datetime.fromisoformat(current.get("since") or "")
            except ValueError:
                since = None
            keep = since is not None and (now - since).total_seconds() < PENDING_GRACE_SECONDS
        if keep:
            if _extend(r, env_id, current["run_id"]):
                counts["renewed"] += 1
        elif r.eval(_CAD, 1, _KEY.format(env_id), str(current["run_id"])):
            r.delete(_META.format(env_id))
            r.delete(_RUN.format(current["run_id"]))
            counts["released"] += 1
    return counts
