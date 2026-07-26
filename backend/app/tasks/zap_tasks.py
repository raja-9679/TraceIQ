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
from urllib.parse import urlsplit

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import AuthSession, SecurityFinding, SecurityScan
from app.services.zap_client import (
    ZapClient, ZapError, map_alerts, cookie_header_from_storage_state)

logger = logging.getLogger(__name__)

sync_engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", ""), echo=False)


def _scan_origin(url: str) -> str:
    """scheme://host[:port] of a URL, so alerts are collected for the whole
    origin the spider crawled rather than only the entry path."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else url


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
    scan.auth_header_value = None  # never retain the secret past a run
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

            # Header auth (item 7): inject a bearer token / API key header. Then
            # scrub the secret from the row — ZAP holds the rule from here on.
            if scan.auth_header_value:
                client.add_header_rule(
                    scan.auth_header_name or "Authorization", scan.auth_header_value,
                    description="traceiq-auth-header")
                logger.info("[ZAP] scan %s: authenticated via %s header",
                            scan_id, scan.auth_header_name or "Authorization")
                scan.auth_header_value = None
                s.add(scan)
                s.commit()

            # API import (item 6): pull an OpenAPI/Swagger spec so endpoints the
            # crawler can't reach by following links are added to the site tree.
            if scan.openapi_url:
                try:
                    client.import_openapi(scan.openapi_url, host_override=_scan_origin(scan.target_url))
                    logger.info("[ZAP] scan %s: imported OpenAPI spec %s", scan_id, scan.openapi_url)
                except ZapError as e:
                    logger.warning("[ZAP] scan %s: OpenAPI import failed: %s", scan_id, e)

            # Spider (crawl). Widen depth for large sites, then optionally run
            # the AJAX spider so JS-rendered / SPA content is also discovered.
            if settings.ZAP_SPIDER_MAX_DEPTH:
                try:
                    client.set_spider_max_depth(settings.ZAP_SPIDER_MAX_DEPTH)
                except ZapError:
                    logger.warning("[ZAP] scan %s: could not set spider depth", scan_id)
            spider_id = client.spider(
                scan.target_url, max_children=settings.ZAP_SPIDER_MAX_CHILDREN)
            spidered = _poll_to_complete(lambda: client.spider_status(spider_id), budget)
            if not spidered:
                logger.warning("[ZAP] scan %s: HTML spider hit the time budget", scan_id)

            if settings.ZAP_AJAX_SPIDER:
                try:
                    client.ajax_spider(scan.target_url)
                    a_deadline = time.time() + budget
                    while client.ajax_spider_running() and time.time() < a_deadline:
                        time.sleep(3)
                except ZapError as e:
                    # AJAX spider is best-effort (needs a browser in the image);
                    # never fail the whole scan if it's unavailable.
                    logger.warning("[ZAP] scan %s: AJAX spider unavailable: %s", scan_id, e)

            # Let the passive scanner drain its queue against the full budget —
            # a large site won't finish in a fixed 2-minute window.
            p_deadline = time.time() + budget
            while client.passive_records_to_scan() > 0 and time.time() < p_deadline:
                time.sleep(3)

            # Active (attacking) scan only when explicitly requested.
            if scan.scan_type == "active":
                ascan_id = client.active_scan(scan.target_url)
                _poll_to_complete(lambda: client.active_scan_status(ascan_id), budget)

            # Report on the whole scanned origin, not just the entry path — a
            # deep entry URL (e.g. a single article) would otherwise hide every
            # finding outside its subtree.
            findings = map_alerts(client.alerts(_scan_origin(scan.target_url)))

            # Carry triage forward: a finding marked false_positive in the
            # previous completed scan of this project+target stays
            # false_positive instead of resurfacing as open every scan.
            from sqlmodel import select as _select

            from app.models import SecurityScan as _Scan
            from app.services.passive_security import finding_fingerprint
            prev_scan = s.exec(_select(_Scan).where(
                _Scan.project_id == scan.project_id,
                _Scan.target_url == scan.target_url,
                _Scan.status == "completed",
                _Scan.id != scan.id).order_by(_Scan.finished_at.desc())).first()
            prev_fp_status: dict = {}
            if prev_scan:
                for pf in s.exec(_select(SecurityFinding).where(
                        SecurityFinding.scan_id == prev_scan.id)).all():
                    if pf.fingerprint:
                        prev_fp_status[pf.fingerprint] = pf.status

            counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
            for f in findings:
                counts[f["severity"]] = counts.get(f["severity"], 0) + 1
                fp = finding_fingerprint(f["category"], f["title"], f["target_url"])
                s.add(SecurityFinding(
                    scan_id=scan.id, project_id=scan.project_id, scan_type="zap",
                    category=f["category"], severity=f["severity"], title=f["title"],
                    description=f["description"], evidence=f["evidence"],
                    target_url=f["target_url"], fingerprint=fp,
                    status="false_positive" if prev_fp_status.get(fp) == "false_positive" else "open"))

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
