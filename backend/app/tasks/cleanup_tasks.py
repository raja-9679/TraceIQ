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


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_stuck_tests")
def cleanup_stuck_tests():
    """Retired in WP15f — kept registered so an old beat schedule entry
    (RedBeat keeps entries in Redis) does not log "unregistered task".

    It selected RUNNING runs by ``TestRun.updated_at``, a column TestRun has
    never had, so every pass raised into its own ``except`` and nothing was
    ever cleaned. Had it worked it would have been harmful: it killed any run
    older than 15 minutes, including healthy 700-case runs that
    ``result_aggregator.check_stale_runs`` (the real sweeper, which looks at
    progress, not age) deliberately leaves alone.
    """
    return 0


# Terminal statuses safe to purge (never delete an in-flight run). Includes
# CANCELLED / TIMED_OUT — without them those runs would be kept forever.
from app.services.run_status import TERMINAL as _PURGEABLE_STATUSES  # noqa: E402


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
            doomed = []
            for run in candidates:
                if len(doomed) >= batch_size:
                    break
                days = windows.get(run.project_id)
                if not days:
                    continue
                if run.created_at is None or run.created_at >= now - timedelta(days=days):
                    continue
                doomed.append(run.id)

            if doomed:
                # Same cascade as DELETE /runs. Deleting results-then-run by
                # hand 500'd on any run with a heal proposal, security finding
                # or monitor check, and because this whole batch is one
                # transaction under a broad except, one such run stalled
                # retention for every run behind it, every pass.
                from app.services.entity_delete import (
                    CrossProjectReference, cascade_delete_sync, delete_objects)
                try:
                    reports = [cascade_delete_sync(session, "testrun", doomed)]
                    session.commit()
                    purged = len(doomed)
                except CrossProjectReference as e:
                    # Some run in the batch is referenced from a project outside
                    # it. Retry one by one so that run is skipped (and logged)
                    # instead of stalling retention for every run behind it.
                    session.rollback()
                    print(f"[Retention] Batch refused ({e}); retrying runs individually")
                    reports = []
                    for run_id in doomed:
                        try:
                            reports.append(cascade_delete_sync(session, "testrun", [run_id]))
                            session.commit()
                            purged += 1
                        except CrossProjectReference as one:
                            session.rollback()
                            print(f"[Retention] Skipped run {run_id}: {one}")
                # Artifacts after the commit; best-effort, never fatal.
                for report in reports:
                    delete_objects(report)
                print(f"[Retention] Purged {purged} run(s) past their retention window")

            # Matrix executions follow the same windows. Own try: a failure
            # here must not undo (or be undone by) the run purge above.
            try:
                purge_old_matrix_runs(session, windows, now, batch_size)
            except Exception as e:
                session.rollback()
                print(f"[Retention] Error purging old access-matrix runs: {e}")

    except Exception as e:
        print(f"[Retention] Error purging old runs: {e}")

    return purged


_MATRIX_RUN_TERMINAL = ("completed", "error")


def purge_old_matrix_runs(session, windows, now, batch_size) -> int:
    """Delete finished AccessMatrixRuns (+ cells + screenshots) past their
    project's run-retention window. Called from `purge_old_runs` with the same
    per-project windows, so a matrix run lives exactly as long as a TestRun.

    The matrix definition is never touched — it is configuration, not history.
    The TestRuns a case row created are ordinary runs and age out through the
    run sweep (their cell link is NULLIFY in entity_delete). Pending/running
    executions are never swept: the collector still owns them.
    """
    from sqlmodel import delete

    from app.models import AccessMatrixCell, AccessMatrixRun
    from app.services.purge import _delete_objects
    from app.services.retention import select_expired

    candidates = session.exec(
        select(AccessMatrixRun.id, AccessMatrixRun.project_id, AccessMatrixRun.created_at)
        .where(AccessMatrixRun.status.in_(_MATRIX_RUN_TERMINAL))
        .order_by(AccessMatrixRun.created_at.asc())
        .limit(batch_size * 4)
    ).all()
    doomed = select_expired(candidates, windows, now, batch_size)
    if not doomed:
        return 0
    session.exec(delete(AccessMatrixCell).where(AccessMatrixCell.matrix_run_id.in_(doomed)))
    session.exec(delete(AccessMatrixRun).where(AccessMatrixRun.id.in_(doomed)))
    session.commit()
    # Page-row screenshots, after the commit; best-effort like run artifacts.
    _delete_objects([f"access-matrix/{rid}/" for rid in doomed])
    print(f"[Retention] Purged {len(doomed)} access-matrix run(s) past their retention window")
    return len(doomed)


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


#: Captured webhook requests exist only for a running wait-for-webhook step.
WEBHOOK_CAPTURE_TTL_HOURS = 24


WEBHOOK_DELIVERY_RETENTION_DAYS = 30


@celery_app.task(name="app.tasks.cleanup_tasks.purge_webhook_deliveries")
def purge_webhook_deliveries(days: int = WEBHOOK_DELIVERY_RETENTION_DAYS):
    """Delete outbound webhook delivery log rows older than `days` (WP15f).

    The log is for troubleshooting a receiver and redelivering what it
    missed; a month covers that, and each row carries the event payload.
    """
    from sqlalchemy import text

    cutoff = datetime.utcnow() - timedelta(days=max(1, int(days)))
    batch = int(getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500)
    total = 0
    with Session(sync_engine) as session:
        while True:
            deleted = session.execute(text("""
                DELETE FROM webhookdelivery
                 WHERE id IN (SELECT id FROM webhookdelivery
                               WHERE created_at < :cutoff
                               ORDER BY id LIMIT :batch)
            """), {"cutoff": cutoff, "batch": batch}).rowcount or 0
            session.commit()
            total += deleted
            if deleted < batch:
                break
    if total:
        print(f"[Cleanup] Expired {total} webhook delivery log row(s) older than {days} days")
    return total


@celery_app.task(name="app.tasks.cleanup_tasks.purge_webhook_captures")
def purge_webhook_captures(ttl_hours: int = WEBHOOK_CAPTURE_TTL_HOURS):
    """Delete webhook captures older than the TTL.

    Not tied to RUN_RETENTION_DAYS on purpose: a capture is a third-party
    payload held only so an in-flight step can read it, so it should not live
    as long as the run's evidence does — even though it is redacted, "we keep
    whatever strangers POST at us for a year" is the wrong default. Batched so
    a backlog (beat down for a week) cannot turn into one huge transaction.
    """
    from sqlalchemy import text

    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(ttl_hours)))
    batch = int(getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500)
    total = 0
    with Session(sync_engine) as session:
        while True:
            deleted = session.execute(text("""
                DELETE FROM webhookcapture
                 WHERE id IN (SELECT id FROM webhookcapture
                               WHERE received_at < :cutoff
                               ORDER BY id LIMIT :batch)
            """), {"cutoff": cutoff, "batch": batch}).rowcount or 0
            session.commit()
            total += deleted
            if deleted < batch:
                break
    if total:
        print(f"[Cleanup] Expired {total} webhook capture(s) older than {ttl_hours} h")
    return total


@celery_app.task(name="app.tasks.cleanup_tasks.purge_assistant_conversations")
def purge_assistant_conversations():
    """Delete assistant messages older than 30 days and conversations idle
    that long (WP14a). A beat task rather than a purge on read: retention has
    to hold for users who never open the assistant again. The read path also
    hides anything older, so the gap between runs is never visible."""
    from app.services.assistant import purge_expired_sync

    with Session(sync_engine) as session:
        total = purge_expired_sync(session)
    if total:
        print(f"[Cleanup] Expired {total} assistant message/conversation row(s)")
    return total


#: Default deny-log retention when POLICY_DENY_RETENTION_DAYS is unset (W4).
POLICY_DENY_RETENTION_DEFAULT_DAYS = 90


def policy_deny_retention_days() -> int:
    from app.services.instance_settings import effective
    raw = effective("POLICY_DENY_RETENTION_DAYS")
    if raw is None or raw == "":
        return POLICY_DENY_RETENTION_DEFAULT_DAYS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return POLICY_DENY_RETENTION_DEFAULT_DAYS


@celery_app.task(name="app.tasks.cleanup_tasks.purge_policy_decisions")
def purge_policy_decisions():
    """Expire PolicyDecisionLog rows (refused actions, WP14b) past retention.

    A bounded window on purpose: the deny log is an operator's "what got
    blocked lately" view, not the audit trail — significant changes (kill
    switch, budgets, AI opt-out) are in the audit log under its own retention.
    """
    from sqlalchemy import text

    days = policy_deny_retention_days()
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    batch = int(getattr(settings, 'RETENTION_BATCH_SIZE', 500) or 500)
    total = 0
    with Session(sync_engine) as session:
        while True:
            deleted = session.execute(text("""
                DELETE FROM policydecisionlog
                 WHERE id IN (SELECT id FROM policydecisionlog
                               WHERE created_at < :cutoff
                               ORDER BY id LIMIT :batch)
            """), {"cutoff": cutoff, "batch": batch}).rowcount or 0
            session.commit()
            total += deleted
            if deleted < batch:
                break
    if total:
        print(f"[Cleanup] Expired {total} policy deny-log row(s) older than {days} days")
    return total
