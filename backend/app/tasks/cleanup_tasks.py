"""
Celery tasks for cleaning up stuck test runs and enforcing data retention.
"""
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import TestRun, TestStatus, TestCaseResult
from sqlmodel import create_engine

# Use sync engine for Celery worker
sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
sync_engine = create_engine(sync_db_url, echo=False)

TIMEOUT_MINUTES = getattr(settings, 'STALE_RUN_INACTIVITY_MINUTES', 15)


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_stuck_tests")
def cleanup_stuck_tests():
    """
    Find and mark tests that have been in RUNNING status for too long.
    Runs periodically to detect stuck tests that never received completion webhooks.
    """
    timeout_threshold = datetime.utcnow() - timedelta(minutes=TIMEOUT_MINUTES)
    cleaned_count = 0
    
    try:
        with Session(sync_engine) as session:
            # Find tests in RUNNING status older than timeout threshold
            statement = select(TestRun).where(
                TestRun.status == TestStatus.RUNNING,
                TestRun.updated_at < timeout_threshold
            )
            
            stuck_runs = session.exec(statement).all()
            
            for run in stuck_runs:
                # Calculate how long it's been running
                running_duration = datetime.utcnow() - run.updated_at
                minutes_running = int(running_duration.total_seconds() / 60)
                
                # Mark as ERROR with timeout message
                run.status = TestStatus.ERROR
                run.error_message = f"Test execution timed out after {minutes_running} minutes - no completion webhook received"
                run.updated_at = datetime.utcnow()
                
                session.add(run)
                cleaned_count += 1
                
                print(f"[Cleanup] Marked run {run.id} as ERROR (timeout after {minutes_running} minutes)")
            
            if cleaned_count > 0:
                session.commit()
                print(f"[Cleanup] Cleaned up {cleaned_count} stuck test runs")

    except Exception as e:
        print(f"[Cleanup] Error cleaning up stuck tests: {e}")

    return cleaned_count


# Terminal statuses safe to purge (never delete an in-flight run).
_PURGEABLE_STATUSES = (
    TestStatus.PASSED,
    TestStatus.FAILED,
    TestStatus.ERROR,
)


@celery_app.task(name="app.tasks.cleanup_tasks.purge_old_runs")
def purge_old_runs():
    """Delete finished TestRuns older than RUN_RETENTION_DAYS.

    Removes the run's MinIO artifacts (video/trace/screenshots/logs) and its
    TestCaseResult rows, then the run itself. No-op when RUN_RETENTION_DAYS<=0.
    Bounded to RETENTION_BATCH_SIZE runs per pass so a large backlog drains
    over several scheduled runs rather than in one long transaction.
    """
    from app.services.instance_settings import effective
    retention_days = int(effective('RUN_RETENTION_DAYS') or 0)
    if retention_days <= 0:
        return 0

    batch_size = getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    purged = 0

    try:
        # Import here to avoid a hard dependency at module import time.
        from app.core.storage import minio_client

        with Session(sync_engine) as session:
            statement = (
                select(TestRun)
                .where(
                    TestRun.status.in_(_PURGEABLE_STATUSES),
                    TestRun.created_at < cutoff,
                )
                .order_by(TestRun.created_at.asc())
                .limit(batch_size)
            )
            old_runs = session.exec(statement).all()

            for run in old_runs:
                # Best-effort artifact deletion; never block the DB purge on it.
                try:
                    minio_client.delete_run_artifacts(run.id)
                except Exception as art_err:
                    print(f"[Retention] Artifact delete failed for run {run.id}: {art_err}")

                results = session.exec(
                    select(TestCaseResult).where(TestCaseResult.test_run_id == run.id)
                ).all()
                for res in results:
                    session.delete(res)
                session.delete(run)
                purged += 1

            if purged:
                session.commit()
                print(f"[Retention] Purged {purged} run(s) older than {retention_days}d "
                      f"(cutoff {cutoff.isoformat()})")

    except Exception as e:
        print(f"[Retention] Error purging old runs: {e}")

    return purged


@celery_app.task(name="app.tasks.cleanup_tasks.purge_old_audit_logs")
def purge_old_audit_logs():
    """Expire audit rows past AUDIT_RETENTION_DAYS.

    Deliberately separate from purge_old_runs. Audit retention is a compliance
    obligation with its own clock — PCI DSS Requirement 10 wants a year, with
    three months immediately available — and coupling it to how long you keep
    test artifacts would mean that shortening one to save disk silently
    shortens the other.

    Defaults to 0 (keep forever), which is the right default for a compliance
    record: nobody is harmed by keeping too much history, and deletion here is
    irreversible by construction.

    This is the ONLY path permitted to delete from `auditlog`. The append-only
    trigger rejects DELETE unless the session announces itself with
    `traceiq.audit_retention = 'on'`. SET LOCAL scopes that to the surrounding
    transaction, so it cannot leak into any other statement or connection.
    """
    from sqlalchemy import text
    from app.services.instance_settings import effective

    retention_days = int(effective('AUDIT_RETENTION_DAYS') or 0)
    if retention_days <= 0:
        return 0

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    batch = int(getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500)

    with Session(sync_engine) as session:
        session.execute(text("SET LOCAL traceiq.audit_retention = 'on'"))
        result = session.execute(
            text("""
                DELETE FROM auditlog
                 WHERE id IN (
                    SELECT id FROM auditlog
                     WHERE timestamp < :cutoff
                     ORDER BY id
                     LIMIT :batch
                 )
            """),
            {"cutoff": cutoff, "batch": batch},
        )
        deleted = result.rowcount or 0
        session.commit()

    if deleted:
        print(f"[Cleanup] Expired {deleted} audit row(s) older than {retention_days} days")
    return deleted
