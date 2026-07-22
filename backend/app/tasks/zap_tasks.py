"""OWASP ZAP scan orchestration (PLATFORM_VISION.md P-4, item 6).

`run_zap_scan` drives a SecurityScan through spider → passive (baseline) and
optionally active scan, feeding the project's stored auth session for
authenticated coverage, then persists ZAP alerts as SecurityFinding rows
(scan_type="zap"). Requires a running ZAP daemon (ZAP_API_URL); the scan is
marked errored when unconfigured or on transport failure.

Authorization/allowlist enforcement happens at the API layer before a scan is
created — this task assumes the target was already authorized.
"""
import logging
import time
from datetime import datetime

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import AuthSession, SecurityFinding, SecurityScan
from app.services.zap_client import (
    ZapClient, ZapError, map_alerts, cookie_header_from_storage_state)

logger = logging.getLogger(__name__)

sync_engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", ""), echo=False)


def _auth_cookie(session: Session, project_id: int):
    row = session.exec(
        select(AuthSession).where(AuthSession.project_id == project_id)).first()
    return cookie_header_from_storage_state(row.storage_state) if row else None


def _poll_to_complete(status_fn, timeout_s: int, interval_s: int = 3) -> bool:
    """Poll a 0–100 status fn until it reaches 100 or the budget elapses."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if status_fn() >= 100:
                return True
        except ZapError:
            raise
        time.sleep(interval_s)
    return False


def _fail(session: Session, scan: SecurityScan, msg: str):
    scan.status = "error"
    scan.error = msg[:1000]
    scan.finished_at = datetime.utcnow()
    session.add(scan)
    session.commit()


@celery_app.task(bind=True, max_retries=0)
def run_zap_scan(self, scan_id: int):
    with Session(sync_engine) as s:
        scan = s.get(SecurityScan, scan_id)
        if not scan:
            return
        if not settings.ZAP_API_URL:
            _fail(s, scan, "ZAP is not configured (ZAP_API_URL unset).")
            return

        scan.status = "running"
        scan.started_at = datetime.utcnow()
        s.add(scan)
        s.commit()

        budget = settings.ZAP_SCAN_TIMEOUT_SECONDS
        try:
            client = ZapClient(settings.ZAP_API_URL, settings.ZAP_API_KEY, timeout=30)

            if scan.authenticated:
                cookie = _auth_cookie(s, scan.project_id)
                if cookie:
                    client.add_cookie_header(cookie)
                    logger.info("[ZAP] scan %s: authenticated via stored session", scan_id)
                else:
                    logger.warning("[ZAP] scan %s: authenticated requested but no auth session", scan_id)

            # Spider (crawl) then let the passive scanner drain its queue.
            spider_id = client.spider(scan.target_url)
            _poll_to_complete(lambda: client.spider_status(spider_id), budget)
            p_deadline = time.time() + min(120, budget)
            while client.passive_records_to_scan() > 0 and time.time() < p_deadline:
                time.sleep(3)

            # Active (attacking) scan only when explicitly requested.
            if scan.scan_type == "active":
                ascan_id = client.active_scan(scan.target_url)
                _poll_to_complete(lambda: client.active_scan_status(ascan_id), budget)

            findings = map_alerts(client.alerts(scan.target_url))
            counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
            for f in findings:
                counts[f["severity"]] = counts.get(f["severity"], 0) + 1
                s.add(SecurityFinding(
                    scan_id=scan.id, project_id=scan.project_id, scan_type="zap",
                    category=f["category"], severity=f["severity"], title=f["title"],
                    description=f["description"], evidence=f["evidence"],
                    target_url=f["target_url"]))

            scan.counts = counts
            scan.status = "completed"
            scan.finished_at = datetime.utcnow()
            s.add(scan)
            s.commit()
            logger.info("[ZAP] scan %s completed: %s", scan_id, counts)

        except ZapError as e:
            _fail(s, scan, f"ZAP error: {e}")
        except Exception as e:  # noqa: BLE001
            _fail(s, scan, f"Scan failed: {e}")
