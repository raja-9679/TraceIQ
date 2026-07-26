"""Passive security scanning (PLATFORM_VISION.md P-4, phase 1).

`scan_run_passive` runs the pure analyzer (app.services.passive_security) over a
finalised run's already-captured responses and persists the results as
`SecurityFinding` rows. It is idempotent per run (re-scanning replaces the prior
passive findings) and is hooked best-effort at run finalize.

Zero risk: it only reads data the run already recorded — no traffic is sent to
the target. Gated by PASSIVE_SECURITY_SCAN_ENABLED (default on).
"""
import logging
from typing import Dict

from sqlmodel import Session, create_engine, delete, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import SecurityFinding, TestRun
from app.services.passive_security import analyze_run, summarize

logger = logging.getLogger(__name__)

sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=False)


def _persist_sync(session: Session, run: TestRun) -> Dict[str, int]:
    """Replace this run's passive findings with a fresh scan. Caller commits."""
    findings = analyze_run(run)
    session.exec(
        delete(SecurityFinding).where(
            SecurityFinding.run_id == run.id,
            SecurityFinding.scan_type == "passive",
        )
    )
    for f in findings:
        session.add(SecurityFinding(
            run_id=run.id,
            project_id=run.project_id,
            scan_type="passive",
            category=f["category"],
            severity=f["severity"],
            title=f["title"],
            description=f["description"],
            evidence=f["evidence"],
            target_url=f["target_url"],
        ))
    return summarize(findings)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def scan_run_passive(self, run_id: int):
    """Passive-scan a finalised run (no-op when disabled or run missing)."""
    if not getattr(settings, "PASSIVE_SECURITY_SCAN_ENABLED", True):
        return
    try:
        with Session(sync_engine) as session:
            run = session.get(TestRun, run_id)
            if not run:
                return
            counts = _persist_sync(session, run)
            session.commit()
            logger.info("[Security] Passive scan run=%s findings=%s", run_id, counts)
    except Exception as e:
        logger.error("[Security] Passive scan failed for run %s: %s", run_id, e)
        raise self.retry(exc=e)
