"""WP16c — exclusive-environment lease heartbeat and validation-run outcomes.

See app/services/environment_lock.py (V3) and app/services/repair_validation.py
(V4) for the rules; these are only the Celery entry points.
"""
from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)


@celery_app.task(name="app.tasks.repair_validation_tasks.refresh_environment_leases")
def refresh_environment_leases():
    """Beat heartbeat (every 30 s): renew leases of RUNNING holders, drop the
    leases of runs that are over (or never started)."""
    from app.services import environment_lock
    try:
        with Session(sync_engine) as session:
            return environment_lock.refresh(session)
    except Exception as exc:  # noqa: BLE001 — the lease TTL is the backstop
        print(f"[EnvLock] heartbeat failed: {exc}")
        return {"error": str(exc)}


@celery_app.task(name="app.tasks.repair_validation_tasks.record_validation_outcome")
def record_validation_outcome(run_id: int):
    """Queued once per finished run (run_completion.SIDE_EFFECT_TASKS); settles
    the proposal a validation run was testing. No-op for other runs."""
    from app.models import TestRun
    from app.services.repair_validation import settle_sync
    with Session(sync_engine) as session:
        run = session.get(TestRun, run_id)
        if run is None or not run.is_validation:
            return None
        status = settle_sync(session, run)
        session.commit()
        return status
