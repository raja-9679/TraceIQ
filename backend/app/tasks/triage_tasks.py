"""Cluster a run's failing results into failure signatures (item 2).

Hooked best-effort at run finalize. Each failing TestCaseResult is fingerprinted
(app/services/failure_signature.py) and upserted into a per-project
FailureCluster so identical root causes across runs collapse into one triage
item. Idempotent: only results not yet assigned to a cluster are processed.
"""
import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import FailureCluster, TestCaseResult, TestRun, TestStatus
from app.services.failure_signature import compute_signature

logger = logging.getLogger(__name__)

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

_FAILING = (TestStatus.FAILED, TestStatus.ERROR)


def _get_or_create_cluster(session: Session, project_id: int, signature: str,
                           title: str, category: str, sample: str, run_id: int) -> FailureCluster:
    cluster = session.exec(
        select(FailureCluster).where(
            FailureCluster.project_id == project_id,
            FailureCluster.signature == signature)).first()
    if cluster:
        return cluster
    cluster = FailureCluster(
        project_id=project_id, signature=signature, title=title[:255], category=category,
        status="open", occurrence_count=0, first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(), last_run_id=run_id,
        sample_error=(sample or "")[:2000])
    session.add(cluster)
    try:
        session.commit()
    except IntegrityError:
        # Concurrent finalize created it first — re-fetch.
        session.rollback()
        cluster = session.exec(
            select(FailureCluster).where(
                FailureCluster.project_id == project_id,
                FailureCluster.signature == signature)).first()
    session.refresh(cluster)
    return cluster


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def cluster_run_failures(self, run_id: int):
    try:
        with Session(sync_engine) as s:
            run = s.get(TestRun, run_id)
            if not run or not run.project_id:
                return
            results = s.exec(
                select(TestCaseResult).where(
                    TestCaseResult.test_run_id == run_id,
                    TestCaseResult.cluster_id == None)).all()  # noqa: E711
            failing = [r for r in results if r.status in _FAILING]
            if not failing:
                return

            clustered = 0
            for r in failing:
                sig, title, category = compute_signature(r.error_message, r.test_name)
                cluster = _get_or_create_cluster(
                    s, run.project_id, sig, title, category, r.error_message, run_id)
                cluster.occurrence_count += 1
                cluster.last_seen_at = datetime.utcnow()
                cluster.last_run_id = run_id
                r.cluster_id = cluster.id
                s.add(cluster)
                s.add(r)
                clustered += 1
            s.commit()
            logger.info("[Triage] run %s: clustered %s failing result(s)", run_id, clustered)
    except Exception as e:  # noqa: BLE001
        logger.error("[Triage] clustering run %s failed: %s", run_id, e)
        raise self.retry(exc=e)
