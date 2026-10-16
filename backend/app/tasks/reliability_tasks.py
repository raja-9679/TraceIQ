"""Beat-driven roll-ups for the reliability reports (WP12b).

* `snapshot_flake_trends` — one FlakeDailySnapshot per project and one
  FlakeCaseScoreDaily per case for today (UTC), plus today's and yesterday's
  result counts. Re-running on the same day replaces that day's rows, so the
  task is idempotent; the last run of a day is what the trend shows. Rows older
  than 180 days are pruned.
* `aggregate_vendor_health` — rebuilds VendorHealthDaily for today and
  yesterday from TestRun.network_events. A day is replaced as a whole, so runs
  that finish late are picked up by the next tick and a repeat changes nothing.
  Reads stay on these small tables; nothing ever scans the JSON per request.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import case, delete
from sqlmodel import Session, create_engine, func, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import (
    FlakeCaseScoreDaily, FlakeDailySnapshot, FlakeRecord, Project, ProjectEnvironment,
    TestCase, TestCaseResult, TestRun, TestStatus, VendorHealthDaily,
)
from app.services import flake_patterns, vendor_health

logger = logging.getLogger(__name__)

_engine = None
_FINISHED = [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]
_FAIL = [TestStatus.FAILED, TestStatus.ERROR]
VENDOR_RETENTION_DAYS = 180
_RUN_BATCH = 200


def _sync_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)
    return _engine


def _day_bounds(day: date):
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------------------
# Flake trend snapshots
# ---------------------------------------------------------------------------

def _result_counts(session: Session, project_id: int, day: date) -> Dict[str, int]:
    start, end = _day_bounds(day)
    row = session.exec(
        select(
            func.count(TestCaseResult.id),
            func.sum(case((TestCaseResult.is_flaky == True, 1), else_=0)),  # noqa: E712
            func.sum(case((TestCaseResult.status.in_(_FAIL), 1), else_=0)),
        )
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= start,
               TestRun.created_at < end, TestCaseResult.status.in_(_FINISHED))
    ).one()
    return {"results_total": int(row[0] or 0), "results_flaky": int(row[1] or 0),
            "results_failed": int(row[2] or 0)}


def snapshot_project(session: Session, project_id: int, today: date) -> None:
    """Write today's snapshot rows for one project (replacing any from an
    earlier tick today) and refresh yesterday's result counts. Caller commits."""
    records = session.exec(
        select(FlakeRecord).join(TestCase, TestCase.id == FlakeRecord.test_case_id)
        .where(TestCase.project_id == project_id)).all()
    cases = flake_patterns.case_snapshot_rows(records)

    session.exec(delete(FlakeCaseScoreDaily).where(
        FlakeCaseScoreDaily.project_id == project_id, FlakeCaseScoreDaily.day == today))
    for case_id, e in cases.items():
        session.add(FlakeCaseScoreDaily(project_id=project_id, test_case_id=case_id, day=today, **e))

    snap = session.exec(select(FlakeDailySnapshot).where(
        FlakeDailySnapshot.project_id == project_id, FlakeDailySnapshot.day == today)).first()
    values = {**flake_patterns.project_snapshot(cases), **_result_counts(session, project_id, today)}
    if snap is None:
        snap = FlakeDailySnapshot(project_id=project_id, day=today)
    for k, v in values.items():
        setattr(snap, k, v)
    snap.computed_at = datetime.utcnow()
    session.add(snap)

    # Runs created late yesterday may have finished after yesterday's last tick.
    yesterday = today - timedelta(days=1)
    prev = session.exec(select(FlakeDailySnapshot).where(
        FlakeDailySnapshot.project_id == project_id, FlakeDailySnapshot.day == yesterday)).first()
    if prev is not None:
        for k, v in _result_counts(session, project_id, yesterday).items():
            setattr(prev, k, v)
        session.add(prev)


def run_flake_snapshots(session: Session, today: Optional[date] = None) -> int:
    today = today or datetime.utcnow().date()
    project_ids = session.exec(select(Project.id)).all()
    for pid in project_ids:
        snapshot_project(session, pid, today)
    cutoff = today - timedelta(days=flake_patterns.SNAPSHOT_RETENTION_DAYS)
    session.exec(delete(FlakeCaseScoreDaily).where(FlakeCaseScoreDaily.day < cutoff))
    session.exec(delete(FlakeDailySnapshot).where(FlakeDailySnapshot.day < cutoff))
    session.commit()
    return len(project_ids)


@celery_app.task(name="app.tasks.reliability_tasks.snapshot_flake_trends")
def snapshot_flake_trends() -> int:
    with Session(_sync_engine()) as session:
        n = run_flake_snapshots(session)
    logger.info("flake trend snapshot written for %d projects", n)
    return n


# ---------------------------------------------------------------------------
# Third-party vendor health
# ---------------------------------------------------------------------------

def aggregate_day(session: Session, day: date) -> int:
    """Rebuild every project's VendorHealthDaily rows for `day`. Returns the
    number of rows written. Caller commits."""
    start, end = _day_bounds(day)
    env_urls = {e.id: e.base_url for e in session.exec(select(ProjectEnvironment)).all()}
    per_project: Dict[int, Dict[str, vendor_health.VendorDay]] = {}
    seen_projects = set()
    last_id = 0
    while True:
        runs = session.exec(
            select(TestRun.id, TestRun.project_id, TestRun.network_events,
                   TestRun.target_url, TestRun.environment_id)
            .where(TestRun.created_at >= start, TestRun.created_at < end,
                   TestRun.project_id.is_not(None), TestRun.status.in_(_FINISHED),
                   TestRun.id > last_id)
            .order_by(TestRun.id).limit(_RUN_BATCH)).all()
        if not runs:
            break
        last_id = runs[-1][0]
        ids = [r[0] for r in runs]
        outcome_rows = session.exec(
            select(TestCaseResult.test_run_id, TestCaseResult.status, func.count(TestCaseResult.id))
            .where(TestCaseResult.test_run_id.in_(ids))
            .group_by(TestCaseResult.test_run_id, TestCaseResult.status)).all()
        outcomes: Dict[int, List] = {}
        for run_id, status, count in outcome_rows:
            outcomes.setdefault(run_id, []).append((status, count))
        for run_id, project_id, events, target_url, env_id in runs:
            seen_projects.add(project_id)
            vendors = vendor_health.vendors_in_run(events, (target_url, env_urls.get(env_id)))
            if not vendors:
                continue
            passed, failed = vendor_health.run_outcomes(outcomes.get(run_id, []))
            vendor_health.add_run(per_project.setdefault(project_id, {}), vendors, passed, failed)

    # Replace the day for every project that had runs (or rows) that day.
    existing = set(session.exec(select(VendorHealthDaily.project_id).where(
        VendorHealthDaily.day == day).distinct()).all())
    written = 0
    now = datetime.utcnow()
    for pid in seen_projects | existing:
        session.exec(delete(VendorHealthDaily).where(
            VendorHealthDaily.project_id == pid, VendorHealthDaily.day == day))
        for vendor, agg in per_project.get(pid, {}).items():
            session.add(VendorHealthDaily(project_id=pid, day=day, vendor=vendor,
                                          computed_at=now, **vendor_health.row_values(agg)))
            written += 1
    return written


def run_vendor_aggregation(session: Session, days_back: int = 1,
                           today: Optional[date] = None) -> int:
    today = today or datetime.utcnow().date()
    written = 0
    for i in range(days_back, -1, -1):
        written += aggregate_day(session, today - timedelta(days=i))
        session.commit()
    session.exec(delete(VendorHealthDaily).where(
        VendorHealthDaily.day < today - timedelta(days=VENDOR_RETENTION_DAYS)))
    session.commit()
    return written


@celery_app.task(name="app.tasks.reliability_tasks.aggregate_vendor_health")
def aggregate_vendor_health(days_back: int = 1) -> int:
    """`days_back` > 1 backfills (e.g. after enabling on an existing install)."""
    days_back = max(0, min(int(days_back), VENDOR_RETENTION_DAYS))
    with Session(_sync_engine()) as session:
        n = run_vendor_aggregation(session, days_back)
    logger.info("vendor health rows written: %d", n)
    return n
