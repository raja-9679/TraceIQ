"""Site crawl dispatch/collection and the crawl schedule sweep (WP10).

Same shape as access-matrix cells: one `job_type=site_crawl` side job on
`jobs:pending` (no TestRun), the worker parks its report under
`site_crawl:result:{run}` and `collect_site_crawl` re-schedules itself with a
countdown until the report is there or the deadline passes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import redis
from sqlmodel import Session, create_engine, delete, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings

_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

JOBS_STREAM = "jobs:pending"
CONSUMER_GROUP = "execution-workers"
POLL_COUNTDOWN = 5
# The worker gives a crawl 15 minutes; allow for queueing behind test jobs.
DEADLINE = timedelta(minutes=45)


def _redis():
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def project_known_hosts_sync(session, project_id: int):
    from app.models import Project, ProjectEnvironment, TestSuite
    from app.services.site_crawl import known_hosts_from
    envs = session.exec(select(ProjectEnvironment.base_url).where(
        ProjectEnvironment.project_id == project_id)).all()
    suites = session.exec(select(TestSuite.settings).where(TestSuite.project_id == project_id)).all()
    project = session.get(Project, project_id)
    sec = []
    if project:
        from app.services.access_matrix_runtime import security_settings_for
        sec = security_settings_for(project).get("allowed_domains") or []
    return known_hosts_from(envs, suites, sec)


@celery_app.task(name="app.tasks.frontend_quality_tasks.dispatch_site_crawl")
def dispatch_site_crawl(crawl_run_id: int) -> Dict[str, Any]:
    from app.models import SiteCrawlRun
    from app.services.site_crawl import build_job, host_allowed

    with Session(_engine) as session:
        run = session.get(SiteCrawlRun, crawl_run_id)
        if not run or run.status != "pending":
            return {"status": "skipped"}
        config = run.config or {}
        # Re-checked at dispatch: the host may have been removed from the
        # project since the crawl was saved (or since a schedule last fired).
        known = project_known_hosts_sync(session, run.project_id)
        if not all(host_allowed(h, known) for h in config.get("crawl_hosts") or []):
            run.status, run.error = "error", "a crawl host is no longer one of the project's hosts"
            run.completed_at = datetime.utcnow()
            session.add(run)
            session.commit()
            return {"status": "refused"}
        from app.worker import _load_data_policy
        job = build_job(run.id, config, _load_data_policy(run.project_id, session))
        run.status = "running"
        session.add(run)
        session.commit()

    rc = _redis()
    try:
        rc.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    rc.xadd(JOBS_STREAM, {"job_id": job["job_id"], "run_id": "0", "payload": json.dumps(job)})
    collect_site_crawl.apply_async((crawl_run_id,), countdown=POLL_COUNTDOWN)
    return {"status": "dispatched", "job_id": job["job_id"]}


def collect_once(session, rc, crawl_run_id: int, now: Optional[datetime] = None) -> bool:
    """Fold the worker report into the run. True when the run is done.
    `rc` needs `get` and `delete` (a fake in tests)."""
    from app.models import SiteCrawl, SiteCrawlIssue, SiteCrawlRun
    from app.services.site_crawl import RESULT_KEY, normalise_result

    now = now or datetime.utcnow()
    run = session.get(SiteCrawlRun, crawl_run_id)
    if not run or run.status in ("completed", "error"):
        return True
    key = RESULT_KEY.format(crawl_run_id)
    raw = rc.get(key)
    if raw is None:
        if now - run.created_at > DEADLINE:
            run.status, run.error = "error", "the crawl did not report back in time"
            run.completed_at = now
            session.add(run)
            session.commit()
            return True
        return False
    try:
        report = json.loads(raw)
    except (TypeError, ValueError):
        report = None
    from app.services.redaction import redact_crawl_report
    # Defence in depth: an older worker image may not scrub its report.
    fields, issues = normalise_result(redact_crawl_report(report))
    # Idempotent: a re-delivered report replaces, never doubles, the issues.
    session.exec(delete(SiteCrawlIssue).where(SiteCrawlIssue.crawl_run_id == run.id))
    for item in issues:
        session.add(SiteCrawlIssue(crawl_run_id=run.id, **item))
    for k, v in fields.items():
        setattr(run, k, v)
    run.completed_at = now
    session.add(run)
    crawl = session.get(SiteCrawl, run.crawl_id)
    if crawl:
        crawl.last_run_at = now
        session.add(crawl)
    session.commit()
    rc.delete(key)
    return True


@celery_app.task(name="app.tasks.frontend_quality_tasks.collect_site_crawl")
def collect_site_crawl(crawl_run_id: int) -> Dict[str, Any]:
    rc = _redis()
    with Session(_engine) as session:
        done = collect_once(session, rc, crawl_run_id)
    if not done:
        collect_site_crawl.apply_async((crawl_run_id,), countdown=POLL_COUNTDOWN)
    return {"done": done}


def due_crawls(session, now: datetime):
    from app.models import SiteCrawl
    return session.exec(select(SiteCrawl).where(
        SiteCrawl.is_active == True,  # noqa: E712
        SiteCrawl.cron_expression.is_not(None),
        SiteCrawl.next_run_at.is_not(None),
        SiteCrawl.next_run_at <= now)).all()


def start_crawl_run(session, crawl, *, trigger: str, user_id: Optional[int]):
    """Create the run row (snapshotting the config). Caller commits + dispatches."""
    from app.models import SiteCrawlRun
    from app.services.site_crawl import snapshot
    run = SiteCrawlRun(crawl_id=crawl.id, project_id=crawl.project_id, trigger=trigger,
                       config=snapshot(crawl), requested_by_id=user_id)
    session.add(run)
    return run


@celery_app.task(name="app.tasks.frontend_quality_tasks.process_due_crawls")
def process_due_crawls() -> Dict[str, Any]:
    from croniter import croniter
    from sqlalchemy import func, text
    from app.models import SiteCrawlRun
    from app.services.site_crawl import MAX_ACTIVE_RUNS_PER_PROJECT, RUN_START_LOCK_NAMESPACE

    now = datetime.utcnow()
    started = []
    with Session(_engine) as session:
        for crawl in due_crawls(session, now):
            # Skip (but still advance) when the previous run is still going:
            # a slow site must not pile up overlapping crawls of itself.
            busy = session.exec(select(SiteCrawlRun.id).where(
                SiteCrawlRun.crawl_id == crawl.id,
                SiteCrawlRun.status.in_(("pending", "running")))).first()
            # Same per-project ceiling as manual runs (site_crawl.py), under
            # the same per-project lock so a manual start can't interleave.
            session.exec(text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:pid AS integer))").bindparams(
                ns=RUN_START_LOCK_NAMESPACE, pid=crawl.project_id))
            active = session.exec(select(func.count()).select_from(SiteCrawlRun).where(
                SiteCrawlRun.project_id == crawl.project_id,
                SiteCrawlRun.status.in_(("pending", "running")))).one()
            if not busy and active < MAX_ACTIVE_RUNS_PER_PROJECT:
                run = start_crawl_run(session, crawl, trigger="schedule", user_id=crawl.created_by_id)
                session.flush()
                started.append(run.id)
            crawl.next_run_at = croniter(crawl.cron_expression, now).get_next(datetime)
            session.add(crawl)
        session.commit()
    for run_id in started:
        dispatch_site_crawl.delay(run_id)
    return {"started": len(started)}
