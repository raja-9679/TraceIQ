"""Passive security scanning and leak detection over finished runs.

`scan_run_passive` runs the pure analyzer (app.services.passive_security) over a
finalised run's already-captured responses and persists the results as
`SecurityFinding` rows. `scan_run_leaks` (WP7) does the same with the leak
detectors (app.services.leak_detection). Both are idempotent per run
(re-scanning replaces that run's findings of that type, inheriting their
workflow state) and hooked best-effort at run finalize.

Zero risk: they only read data the run already recorded — no traffic is sent to
the target. Gated by PASSIVE_SECURITY_SCAN_ENABLED / LEAK_DETECTION_ENABLED.

`reopen_expired_risk_acceptances` is the beat-driven half of the findings
workflow: accepted risks whose acceptance has lapsed go back to open.
"""
import logging
from typing import Dict

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import Project, SecurityFinding, TestCaseResult, TestRun
from app.services import finding_lifecycle as lifecycle
from app.services.passive_security import analyze_run, finding_fingerprint, summarize

logger = logging.getLogger(__name__)

sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
sync_engine = create_engine(sync_db_url, echo=False)


def _replace_run_findings(session: Session, run: TestRun, scan_type: str):
    """Delete this run's findings of one type. Returns their workflow state by
    fingerprint (so the replacements inherit it) and a `relink(new_rows)` to
    call after ingest, which points the deleted rows' predecessors at their
    replacements."""
    old = session.exec(select(SecurityFinding).where(
        SecurityFinding.run_id == run.id, SecurityFinding.scan_type == scan_type)).all()
    snapshot = {r.fingerprint: lifecycle.state_snapshot(r) for r in old if r.fingerprint}
    old_fp = {r.id: r.fingerprint for r in old}
    preds = session.exec(select(SecurityFinding).where(
        SecurityFinding.superseded_by_id.in_(list(old_fp)))).all() if old_fp else []
    for row in old:
        session.delete(row)
    session.flush()

    def relink(new_rows) -> None:
        by_fp = {r.fingerprint: r.id for r in new_rows if r.fingerprint}
        for pred in preds:
            # A predecessor whose finding vanished on the rescan becomes current
            # again rather than pointing at a row that no longer exists.
            pred.superseded_by_id = by_fp.get(old_fp.get(pred.superseded_by_id))
            session.add(pred)
        session.flush()

    return snapshot, relink


def _persist_sync(session: Session, run: TestRun) -> Dict[str, int]:
    """Replace this run's passive findings with a fresh scan. Caller commits."""
    findings = analyze_run(run)
    replaced, relink = _replace_run_findings(session, run, "passive")
    rows = []
    for f in findings:
        row = SecurityFinding(
            run_id=run.id,
            project_id=run.project_id,
            scan_type="passive",
            category=f["category"],
            severity=f["severity"],
            title=f["title"],
            description=f["description"],
            evidence=f["evidence"],
            target_url=f["target_url"],
            fingerprint=finding_fingerprint(f["category"], f["title"], f["target_url"]),
        )
        session.add(row)
        rows.append(row)
    lifecycle.ingest(session, rows, project_id=run.project_id, scan_type="passive",
                     replaced=replaced)
    relink(rows)
    _verify_run_scope(session, run, "passive", rows)
    return summarize(findings)


def _verify_run_scope(session: Session, run: TestRun, scan_type: str, rows) -> None:
    """Run-sourced findings are verified by a later run of the SAME suite that
    actually exercised the finding's target: the same origin for passive
    findings, the same test case for leak findings."""
    origins = lifecycle.run_origins(run)
    case_ids = {r for r in session.exec(select(TestCaseResult.test_case_id).where(
        TestCaseResult.test_run_id == run.id)).all() if r}
    if run.test_case_id:
        case_ids.add(run.test_case_id)

    def in_scope(finding: SecurityFinding) -> bool:
        if not finding.run_id or finding.run_id >= run.id:
            return False   # only a LATER run can verify
        source = session.get(TestRun, finding.run_id)
        if not source or source.test_suite_id != run.test_suite_id:
            return False
        if scan_type == "leak":
            case_id = (finding.location or {}).get("case_id")
            return bool(case_id and case_id in case_ids)
        return lifecycle.origin_of(finding.target_url) in origins

    lifecycle.verify_fixes(
        session, project_id=run.project_id, scan_type=scan_type,
        produced=[r.fingerprint for r in rows], in_scope=in_scope,
        evidence={"verified_by_run_id": run.id})


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


def persist_leaks_sync(session: Session, run: TestRun) -> Dict[str, int]:
    """Replace this run's leak findings with a fresh pass. Caller commits."""
    from app.services import leak_detection

    project = session.get(Project, run.project_id) if run.project_id else None
    results = session.exec(select(TestCaseResult).where(
        TestCaseResult.test_run_id == run.id)).all()
    hits = leak_detection.scan_run(run, results,
                                   data_policy=project.data_policy if project else None)
    replaced, relink = _replace_run_findings(session, run, leak_detection.SCAN_TYPE)
    rows = []
    for f in hits:
        row = SecurityFinding(
            run_id=run.id, project_id=run.project_id, scan_type=leak_detection.SCAN_TYPE,
            category=f["category"], severity=f["severity"], title=f["title"],
            description=f["description"], evidence=f["evidence"],
            target_url=f["target_url"], fingerprint=f["fingerprint"],
            location=f["location"])
        session.add(row)
        rows.append(row)
    lifecycle.ingest(session, rows, project_id=run.project_id,
                     scan_type=leak_detection.SCAN_TYPE, replaced=replaced)
    relink(rows)
    _verify_run_scope(session, run, leak_detection.SCAN_TYPE, rows)
    return summarize(hits)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def scan_run_leaks(self, run_id: int):
    """Leak-detection pass over a finalised run's stored data."""
    from app.services.instance_settings import effective
    if not effective("LEAK_DETECTION_ENABLED"):
        return
    try:
        with Session(sync_engine) as session:
            run = session.get(TestRun, run_id)
            if not run:
                return
            counts = persist_leaks_sync(session, run)
            session.commit()
            if any(counts.values()):
                logger.warning("[Security] Leak detection run=%s findings=%s", run_id, counts)
    except Exception as e:
        logger.error("[Security] Leak detection failed for run %s: %s", run_id, e)
        raise self.retry(exc=e)


@celery_app.task
def reopen_expired_risk_acceptances():
    with Session(sync_engine) as session:
        n = lifecycle.reopen_expired_risks(session)
        session.commit()
    if n:
        logger.info("[Security] Re-opened %d finding(s) whose risk acceptance expired", n)
    return n
