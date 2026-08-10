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
    """Delete finished TestRuns past their retention window (workstream G2).

    Retention is **per project** now: `Project.data_policy.retention_days`
    combined with the global `RUN_RETENTION_DAYS`, shorter window wins
    (`app/services/retention.py`). Before this, the project setting was
    scaffolding nothing read — a project whose data-policy screen said "keep runs
    30 days" kept them forever, which made that screen a false statement to
    whoever was reading it during a security review.

    Removes the run's MinIO artifacts (video/trace/screenshots/logs) and its
    TestCaseResult rows, then the run itself. Bounded to RETENTION_BATCH_SIZE
    runs per pass so a large backlog drains over several scheduled runs rather
    than one long transaction.
    """
    from app.models import Project
    from app.services.retention import project_retention_days

    batch_size = getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500
    purged = 0

    try:
        # Import here to avoid a hard dependency at module import time.
        from app.core.storage import minio_client

        with Session(sync_engine) as session:
            # Resolve the window once per project rather than once per run: a
            # backlog is usually thousands of runs across a handful of projects.
            windows = {}
            for project in session.exec(select(Project)).all():
                windows[project.id] = project_retention_days(project)
            if not any(w for w in windows.values()):
                return 0

            candidates = session.exec(
                select(TestRun)
                .where(TestRun.status.in_(_PURGEABLE_STATUSES))
                .order_by(TestRun.created_at.asc())
                .limit(batch_size * 4)
            ).all()

            now = datetime.utcnow()
            for run in candidates:
                if purged >= batch_size:
                    break
                days = windows.get(run.project_id)
                if not days:
                    continue
                if run.created_at is None or run.created_at >= now - timedelta(days=days):
                    continue

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
                print(f"[Retention] Purged {purged} run(s) past their retention window")

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


@celery_app.task(name="app.tasks.cleanup_tasks.purge_derived_records")
def purge_derived_records():
    """Expire records that accumulate forever alongside runs (workstream G2).

    Three tables grow without bound and none of them were ever cleaned:

    * `testcaserevision` — every edit ever made, snapshot included. The snapshot
      is redacted on write, but it is still a copy of the case, and "we keep
      every version of everything forever" is not an answer a data-protection
      questionnaire accepts.
    * `llmusageevent` — one row per provider call, kept for the /ai-usage
      dashboard. Monthly totals already roll into UsageRecord, so the raw events
      are only needed for a recent window.
    * `flakerecord` for cases that no longer exist — orphaned by case deletion.

    Deliberately governed by its own setting rather than RUN_RETENTION_DAYS:
    these are operational records, not customer test artifacts, and an operator
    shortening artifact retention to save disk should not silently lose their
    edit history. Defaults to keeping forever.
    """
    from sqlalchemy import text
    from app.services.instance_settings import effective

    retention_days = int(effective('DERIVED_RETENTION_DAYS') or 0)
    if retention_days <= 0:
        return {"revisions": 0, "llm_events": 0, "orphan_flakes": 0}

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    batch = int(getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500)
    counts = {}

    with Session(sync_engine) as session:
        # Keep at least the newest revision of every case: retention must not
        # leave a case with no recorded history at all, which would break the
        # restore path the revisions exist for.
        counts["revisions"] = session.execute(text("""
            DELETE FROM testcaserevision
             WHERE id IN (
                SELECT id FROM testcaserevision r
                 WHERE r.created_at < :cutoff
                   AND r.id <> (SELECT max(id) FROM testcaserevision
                                 WHERE test_case_id = r.test_case_id)
                 ORDER BY id LIMIT :batch)
        """), {"cutoff": cutoff, "batch": batch}).rowcount or 0

        counts["llm_events"] = session.execute(text("""
            DELETE FROM llmusageevent
             WHERE id IN (SELECT id FROM llmusageevent
                           WHERE created_at < :cutoff
                           ORDER BY id LIMIT :batch)
        """), {"cutoff": cutoff, "batch": batch}).rowcount or 0

        counts["orphan_flakes"] = session.execute(text("""
            DELETE FROM flakerecord
             WHERE test_case_id IS NOT NULL
               AND test_case_id NOT IN (SELECT id FROM testcase)
        """)).rowcount or 0

        session.commit()

    if any(counts.values()):
        print(f"[Retention] Derived records expired: {counts}")
    return counts


@celery_app.task(name="app.tasks.cleanup_tasks.purge_orphaned_artifacts")
def purge_orphaned_artifacts():
    """Delete MinIO objects whose owning row is gone (workstream G2).

    Only `runs/{id}/` was ever deleted, and only when the run was purged
    through the retention path. Anything orphaned another way — a run deleted
    through the API before this, a failed upload, a workspace deleted by the old
    delete_workspace that removed no objects at all — leaked permanently.
    `baselines/` and mobile app binaries were never deleted by anything.

    Opt-in (`ARTIFACT_ORPHAN_SWEEP_ENABLED`) and it lists before it deletes: a
    sweep keyed on "the database doesn't mention this" is exactly the job you
    want to be able to run in report-only mode first, because a bug in the
    reachability query deletes live customer artifacts. `dry_run` is the default.
    """
    from sqlalchemy import text
    from app.services.instance_settings import effective

    if not effective('ARTIFACT_ORPHAN_SWEEP_ENABLED'):
        return {"skipped": "ARTIFACT_ORPHAN_SWEEP_ENABLED is off"}
    dry_run = bool(effective('ARTIFACT_ORPHAN_SWEEP_DRY_RUN'))

    from app.core.storage import minio_client

    report = {"dry_run": dry_run, "orphans": 0, "deleted": 0, "errors": []}
    try:
        with Session(sync_engine) as session:
            live_runs = {str(r) for (r,) in session.execute(
                text("SELECT id FROM testrun")).all()}
            live_baselines = {k for (k,) in session.execute(
                text("SELECT image_url FROM visualbaseline "
                     "WHERE image_url IS NOT NULL")).all()}
            live_builds = {k for (k,) in session.execute(
                text("SELECT file_key FROM mobileappbuild "
                     "WHERE file_key IS NOT NULL")).all()}

        orphans = []
        for prefix in minio_client.list_prefixes("runs/"):
            # "runs/123/" -> "123"
            run_id = prefix.rstrip("/").split("/")[-1]
            if run_id not in live_runs:
                orphans.append(prefix)
        for prefix in minio_client.list_prefixes("baselines/"):
            # Baseline keys are full object keys, not directories, so compare on
            # prefix membership rather than equality.
            if not any(k.startswith(prefix) for k in live_baselines):
                orphans.append(prefix)
        for prefix in minio_client.list_prefixes("app-builds/"):
            if not any(k.startswith(prefix) for k in live_builds):
                orphans.append(prefix)

        report["orphans"] = len(orphans)
        if dry_run:
            report["sample"] = orphans[:20]
            print(f"[Retention] Orphan sweep (dry run): {len(orphans)} prefix(es)")
            return report

        for prefix in orphans:
            try:
                report["deleted"] += minio_client.delete_prefix(prefix)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{prefix}: {exc}")
        print(f"[Retention] Orphan sweep deleted {report['deleted']} object(s)")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(str(exc))
        print(f"[Retention] Orphan sweep failed: {exc}")
    return report
