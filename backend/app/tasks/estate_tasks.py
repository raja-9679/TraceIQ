"""Estate scans and monitored-page checks (WP9). Passive only.

Estate scan (`run_estate_scan`): for every domain of an estate that is on the
project's security allowlist — DNS posture at zone apexes, subdomain discovery
from certificate transparency, takeover, certificate expiry, HSTS and the
HTTP→HTTPS redirect — scored by `estate_checks.score_checks`, kept as
`EstateDomainResult` history, mirrored into SecurityFindings, with certificate
alerts at 30/14/7 days. Runs in the Celery worker (DNS + plain HTTPS; no
browser needed).

Page check (`check_monitored_page`): the page is loaded by an execution worker
as a `job_type=page_check` side job — a normal headless visit; nothing about
the page's scripts is changed. The worker parks its observation under
`page_check:result:{page_id}`; `collect_page_check` polls for it (re-scheduling
itself with a countdown, the access-matrix shape) and folds it into the script
inventory, header baseline and defacement checks.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings

logger = logging.getLogger(__name__)

_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

JOBS_STREAM = "jobs:pending"
CONSUMER_GROUP = "execution-workers"
PAGE_RESULT_KEY = "page_check:result:{}"
POLL_COUNTDOWN = 5
PAGE_DEADLINE = timedelta(minutes=15)
# A scan "running" for longer than this is assumed dead (worker killed), so the
# scheduler may start a new one.
STALE_SCAN = timedelta(hours=2)
MAX_ESTATE_DOMAINS = 500
DEFAULT_CERT_ALERT_DAYS = (30, 14, 7)


def _redis():
    import redis
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def next_run(cron_expression: str, after: datetime) -> Optional[datetime]:
    from croniter import croniter
    try:
        return croniter(cron_expression, after).get_next(datetime)
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Estate scan
# ---------------------------------------------------------------------------

def run_scan(session, scan_id: int, net, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Execute one EstateScan (sync session). `net` is an estate_checks.Net."""
    from app.models import Estate, EstateDomain, EstateDomainResult, EstateScan, Project
    from app.services import estate_checks as ec
    from app.services.access_matrix import host_allowed
    from app.services.access_matrix_runtime import security_allowlist_for
    from app.services.estate_findings import apply_findings, cert_alert_due, send_alert

    now = now or datetime.utcnow()
    scan = session.get(EstateScan, scan_id)
    if not scan:
        return {"status": "not_found"}
    estate = session.get(Estate, scan.estate_id)
    project = session.get(Project, estate.project_id) if estate else None
    if not estate or not project:
        scan.status, scan.error, scan.finished_at = "error", "estate no longer exists", now
        session.add(scan)
        session.commit()
        return {"status": "error"}
    scan.status, scan.started_at = "running", now
    session.add(scan)
    session.commit()

    allowed = security_allowlist_for(project.id, session)
    domains = session.exec(select(EstateDomain).where(
        EstateDomain.estate_id == estate.id).order_by(EstateDomain.id)).all()
    known = {d.hostname for d in domains}
    skipped: List[str] = []
    discovered: List[str] = []
    notes: List[str] = []
    scores: Dict[str, int] = {}
    produced: List[Dict[str, Any]] = []
    scopes = set()
    cert_alerts: List[str] = []
    thresholds = estate.cert_alert_days or list(DEFAULT_CERT_ALERT_DAYS)

    queue = list(domains)
    i = 0
    while i < len(queue):
        dom = queue[i]
        i += 1
        host = dom.hostname
        # Re-checked at scan time: the allowlist may have shrunk since import.
        if not host_allowed(ec.host_url(host), allowed):
            skipped.append(host)
            continue
        rep = ec.scan_host(net, host, is_apex=dom.is_apex,
                           dkim_selectors=estate.dkim_selectors or [], now=now)
        if rep.is_apex and estate.discover_subdomains:
            names, why = ec.discover_subdomains(net, host)
            if why:
                notes.append(f"{host}: {why}")
            for name in names:
                if name in known or len(queue) >= MAX_ESTATE_DOMAINS:
                    continue
                if not host_allowed(ec.host_url(name), allowed):
                    continue
                new = EstateDomain(estate_id=estate.id, hostname=name, source="ct")
                session.add(new)
                session.flush()
                known.add(name)
                queue.append(new)
                discovered.append(name)
            rep.checks.append(ec.Check(
                id="ct_discovery", family="discovery", title="Certificate transparency",
                status="info" if not why else "error",
                summary=why or f"{len(names)} name(s) under {host} in CT logs",
                explanation="Hostnames with publicly logged certificates — the attack surface "
                            "anyone can enumerate."))
        score, deductions = rep.score()
        session.add(EstateDomainResult(
            scan_id=scan.id, domain_id=dom.id, score=score,
            checks=[c.as_dict() for c in rep.checks], deductions=deductions, created_at=now))
        if dom.is_apex is None:
            dom.is_apex = rep.is_apex
        dom.score, dom.last_scanned_at = score, now
        scores[host] = score
        if rep.cert_expires_at is not None:
            if dom.cert_expires_at != rep.cert_expires_at:
                dom.cert_alert_sent_days = None   # renewed (or first seen): alert afresh
            dom.cert_expires_at = rep.cert_expires_at
            days_left = (rep.cert_expires_at - now).days
            due = cert_alert_due(days_left, thresholds, dom.cert_alert_sent_days)
            if due is not None:
                dom.cert_alert_sent_days = due
                cert_alerts.append(f"{host}: certificate expires in {days_left} day(s) "
                                   f"({rep.cert_expires_at:%Y-%m-%d})")
        session.add(dom)
        produced += ec.findings_from_checks(host, rep.checks)
        scopes |= {(ec.FAMILY_SCAN_TYPE[f], ec.host_url(host)) for f in rep.families
                   if f in ec.FAMILY_SCAN_TYPE}

    findings = apply_findings(session, project.id, produced, scopes, now=now)
    worst = sorted(scores.items(), key=lambda kv: kv[1])[:5]
    scan.summary = {
        "domains_scanned": len(scores), "skipped_not_allowlisted": skipped[:50],
        "discovered": discovered[:200], "average_score": round(sum(scores.values()) / len(scores))
        if scores else None, "worst": [{"hostname": h, "score": s} for h, s in worst],
        "findings": findings, "notes": notes[:20], "cert_alerts": len(cert_alerts),
    }
    scan.status, scan.finished_at = "completed", datetime.utcnow()
    estate.last_scan_at = now
    session.add_all([scan, estate])
    session.commit()

    if cert_alerts:
        send_alert(session, project, f"Certificate expiry — estate '{estate.name}'",
                   cert_alerts, estate.alert_emails)
    return {"status": "completed", **scan.summary}


@celery_app.task(name="app.tasks.estate_tasks.run_estate_scan")
def run_estate_scan(estate_id: int, scan_id: Optional[int] = None,
                    trigger: str = "manual") -> Dict[str, Any]:
    from app.models import Estate, EstateScan
    from app.services.estate_net import default_net

    with Session(_engine) as session:
        if scan_id is None:
            if not session.get(Estate, estate_id):
                return {"status": "not_found"}
            scan = EstateScan(estate_id=estate_id, trigger=trigger)
            session.add(scan)
            session.commit()
            scan_id = scan.id
        try:
            return run_scan(session, scan_id, default_net())
        except Exception as e:  # noqa: BLE001 — never leave a scan "running" forever
            logger.exception("[Estate] scan %s failed", scan_id)
            session.rollback()
            scan = session.get(EstateScan, scan_id)
            if scan:
                scan.status, scan.error, scan.finished_at = "error", str(e)[:500], datetime.utcnow()
                session.add(scan)
                session.commit()
            return {"status": "error", "error": str(e)[:500]}


def due_estates(session, now: datetime) -> List[int]:
    """Estates whose schedule is due, with next_scan_at advanced (so a second
    beat tick cannot pick them again) and no live scan in progress."""
    from app.models import Estate, EstateScan

    rows = session.exec(select(Estate).where(
        Estate.schedule_enabled == True,  # noqa: E712
        (Estate.next_scan_at == None) | (Estate.next_scan_at <= now),  # noqa: E711
    )).all()
    out = []
    for est in rows:
        nxt = next_run(est.cron_expression, now)
        first = est.next_scan_at is None
        est.next_scan_at = nxt
        session.add(est)
        if first or nxt is None:
            continue   # just scheduled (or unschedulable): don't scan on enable
        live = session.exec(select(EstateScan.id).where(
            EstateScan.estate_id == est.id, EstateScan.status.in_(("pending", "running")),
            EstateScan.created_at > now - STALE_SCAN)).first()
        if live is None:
            out.append(est.id)
    session.commit()
    return out


@celery_app.task(name="app.tasks.estate_tasks.dispatch_due_estate_scans")
def dispatch_due_estate_scans() -> Dict[str, Any]:
    with Session(_engine) as session:
        ids = due_estates(session, datetime.utcnow())
    for eid in ids:
        run_estate_scan.delay(eid, None, "schedule")
    return {"dispatched": len(ids)}


# ---------------------------------------------------------------------------
# Monitored pages
# ---------------------------------------------------------------------------

def build_page_job(page, allowed_domains: List[str], data_policy: Dict[str, Any]) -> Dict[str, Any]:
    """The worker job for one page check. The worker re-checks the allowlist."""
    return {
        "job_id": str(uuid.uuid4()),
        "job_type": "page_check",
        "run_id": 0,
        "page_id": page.id,
        "result_key": PAGE_RESULT_KEY.format(page.id),
        "browser": "chromium",
        "url": page.url,
        "track_content": bool(page.track_content),
        "element_selectors": [c.get("selector") for c in (page.element_checks or [])
                              if isinstance(c, dict) and c.get("selector")][:50],
        "allowed_domains": list(allowed_domains),
        "data_policy": data_policy or {},
    }


@celery_app.task(name="app.tasks.estate_tasks.check_monitored_page")
def check_monitored_page(page_id: int) -> Dict[str, Any]:
    from app.models import MonitoredPage
    from app.services.access_matrix import host_allowed
    from app.services.access_matrix_runtime import security_allowlist_for
    from app.worker import _load_data_policy

    with Session(_engine) as session:
        page = session.get(MonitoredPage, page_id)
        if not page:
            return {"status": "not_found"}
        allowed = security_allowlist_for(page.project_id, session)
        if not host_allowed(page.url, allowed):
            page.last_status, page.last_error = "error", "host is not on the project's security allowlist"
            page.last_checked_at = datetime.utcnow()
            session.add(page)
            session.commit()
            return {"status": "refused"}
        job = build_page_job(page, allowed, _load_data_policy(page.project_id, session))
    import redis as _r
    rc = _redis()
    rc.delete(job["result_key"])   # never fold in a stale result from an older job
    try:
        rc.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except _r.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    rc.xadd(JOBS_STREAM, {"job_id": job["job_id"], "run_id": "0", "payload": json.dumps(job)})
    collect_page_check.apply_async((page_id, datetime.utcnow().isoformat()), countdown=POLL_COUNTDOWN)
    return {"status": "dispatched", "job_id": job["job_id"]}


def _event(session, page_id: int, kind: str, summary: str, detail: Optional[dict], now: datetime):
    from app.models import PageChangeEvent
    session.add(PageChangeEvent(page_id=page_id, kind=kind, summary=summary[:500],
                                detail=detail, created_at=now))


def apply_page_observation(session, page, obs: Dict[str, Any],
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Fold one worker observation into the inventory / baselines / history.
    Caller commits. Returns {"status", "lines"} for alerting."""
    from app.models import PageScript
    from app.services import script_inventory as si
    from app.services.redaction import redact_headers, redact_text
    from app.services.estate_findings import apply_findings
    from app.services.passive_security import finding_fingerprint

    now = now or datetime.utcnow()
    page.last_checked_at = now
    if obs.get("error"):
        page.last_status, page.last_error = "error", redact_text(str(obs["error"]))[:500]
        session.add(page)
        return {"status": "error", "lines": []}
    page.last_error = None
    lines: List[str] = []
    alert = changed = False

    if page.track_scripts:
        raw = [{**s, "src": redact_text(s.get("src")) if s.get("src") else None}
               for s in (obs.get("scripts") or []) if isinstance(s, dict)]
        current = si.build_entries(raw, obs.get("final_url") or page.url,
                                   obs.get("external_hashes") or {})
        rows = session.exec(select(PageScript).where(PageScript.page_id == page.id)).all()
        by_key = {r.script_key: r for r in rows}
        first = not rows
        known = [{"script_key": r.script_key, "sha384": r.sha384, "src": r.src}
                 for r in rows if r.present]
        diff = si.diff_inventory(known, current)
        for c in diff["added"]:
            row = by_key.get(c["script_key"])
            if row is None:
                row = PageScript(page_id=page.id, first_seen_at=now, **c)
            else:   # back after a removal: re-approval needed if it changed
                if row.sha384 != c.get("sha384"):
                    row.authorized, row.authorized_at = False, None
                for k, v in c.items():
                    setattr(row, k, v)
                row.removed_at = None
            row.present, row.last_seen_at = True, now
            session.add(row)
            if not first:
                alert = True
                label = c.get("src") or f"inline script {c['sha384'][:20]}…"
                lines.append(f"New script: {label}")
                _event(session, page.id, "script_added", f"New script: {label}", {
                    "script_key": c["script_key"], "src": c.get("src"), "sha384": c.get("sha384"),
                    "third_party": c.get("third_party")}, now)
        for c in diff["changed"]:
            row = by_key[c["script_key"]]
            was_authorized = row.authorized
            row.sha384, row.src, row.integrity = c.get("sha384"), c.get("src"), c.get("integrity")
            # PCI DSS 6.4.3: the approval was for the content that was reviewed.
            row.authorized, row.authorized_at, row.last_seen_at = False, None, now
            session.add(row)
            alert = True
            lines.append(f"Script changed: {c.get('src') or c['script_key']}")
            _event(session, page.id, "script_changed", f"Script changed: {c.get('src') or c['script_key']}", {
                "script_key": c["script_key"], "previous_sha384": c.get("previous_sha384"),
                "sha384": c.get("sha384"), "previous_src": c.get("previous_src"), "src": c.get("src"),
                "approval_revoked": was_authorized}, now)
        for k in diff["removed"]:
            row = by_key[k["script_key"]]
            row.present, row.removed_at = False, now
            session.add(row)
            changed = True
            _event(session, page.id, "script_removed", f"Script removed: {row.src or row.script_key}",
                   {"script_key": row.script_key}, now)
        seen = {c["script_key"] for c in diff["added"]} | {c["script_key"] for c in diff["changed"]}
        for c in current:
            if c["script_key"] not in seen and c["script_key"] in by_key:
                by_key[c["script_key"]].last_seen_at = now
                session.add(by_key[c["script_key"]])
        if first:
            _event(session, page.id, "baseline", f"Inventory started with {len(current)} script(s)",
                   {"scripts": len(current)}, now)

    headers = si.security_headers(redact_headers(obs.get("headers") or {}))
    hdiff = si.diff_headers(page.headers_baseline, headers)
    if hdiff:
        changed = True
        names = ", ".join(f"{d['header']} {d['change']}" for d in hdiff)
        lines.append(f"Security headers: {names}")
        _event(session, page.id, "headers_changed", f"Security headers: {names}", {"changes": hdiff}, now)
    page.headers_baseline = headers

    produced: List[Dict[str, Any]] = []
    if page.track_content:
        text = redact_text(obs.get("text") or "") or ""
        elements = {k: (redact_text(v) if isinstance(v, str) else None)
                    for k, v in (obs.get("elements") or {}).items()}
        sim, hits, failures = si.defacement_signals(page, text, elements)
        if page.content_baseline is None:
            page.content_baseline, page.content_baseline_at = si.normalise_text(text), now
            _event(session, page.id, "content_baseline", "Content baseline recorded", None, now)
        page.last_similarity = sim
        problems = []
        if sim is not None and sim < page.similarity_threshold:
            problems.append(f"content similarity {sim:.0%} is below the {page.similarity_threshold:.0%} threshold")
        if hits:
            problems.append(f"forbidden keyword(s) present: {', '.join(hits)}")
        problems += [f"{f['selector']}: {f['reason']}" for f in failures]
        if problems:
            alert = True
            lines += [f"Defacement check: {p}" for p in problems]
            _event(session, page.id, "defacement", "; ".join(problems), {
                "similarity": sim, "keywords": hits, "elements": failures}, now)
            produced.append({
                "scan_type": "page-defacement", "category": "defacement", "severity": "high",
                "title": "Possible defacement", "description": "; ".join(problems)[:2000],
                "evidence": None, "target_url": page.url,
                "fingerprint": finding_fingerprint("defacement", "Possible defacement", page.url)})

    # Unapproved scripts are findings until someone approves them with a
    # justification (or they go away).
    if page.track_scripts:
        for r in session.exec(select(PageScript).where(
                PageScript.page_id == page.id, PageScript.present == True,  # noqa: E712
                PageScript.authorized == False)).all():  # noqa: E712
            title = f"Unapproved script: {(r.src or r.script_key)[:200]}"
            produced.append({
                "scan_type": "page-scripts", "category": "script-inventory",
                "severity": "medium" if r.third_party else "low", "title": title[:300],
                "description": ("A script on a monitored page has no recorded approval and "
                                "justification (PCI DSS 4.0 6.4.3)."),
                "evidence": r.sha384, "target_url": page.url,
                "fingerprint": finding_fingerprint("script-inventory", r.script_key, page.url)})
    scopes = set()
    if page.track_scripts:
        scopes.add(("page-scripts", page.url))
    if page.track_content:
        scopes.add(("page-defacement", page.url))
    session.flush()
    apply_findings(session, page.project_id, produced, scopes, now=now)

    page.last_status = "alert" if alert else "changed" if changed else "ok"
    session.add(page)
    return {"status": page.last_status, "lines": lines}


def collect_page_once(session, rc, page_id: int, started_at: datetime,
                      now: Optional[datetime] = None) -> bool:
    """Fold the worker result in if it is there. True when done (result or deadline)."""
    from app.models import MonitoredPage, Project
    from app.services.estate_findings import send_alert

    now = now or datetime.utcnow()
    page = session.get(MonitoredPage, page_id)
    if not page:
        return True
    key = PAGE_RESULT_KEY.format(page_id)
    raw = rc.get(key)
    if not raw:
        if now - started_at <= PAGE_DEADLINE:
            return False
        obs = {"error": "no worker result before the deadline — is an execution worker running?"}
    else:
        rc.delete(key)
        try:
            obs = json.loads(raw)
        except ValueError:
            obs = {"error": "unreadable worker result"}
    out = apply_page_observation(session, page, obs, now)
    session.commit()
    if out["status"] == "alert" and out["lines"]:
        project = session.get(Project, page.project_id)
        send_alert(session, project, f"Monitored page changed — {page.name}",
                   [page.url] + out["lines"], page.alert_emails)
    return True


@celery_app.task(name="app.tasks.estate_tasks.collect_page_check")
def collect_page_check(page_id: int, started_at: str) -> Dict[str, Any]:
    with Session(_engine) as session:
        done = collect_page_once(session, _redis(), page_id, datetime.fromisoformat(started_at))
    if not done:
        collect_page_check.apply_async((page_id, started_at), countdown=POLL_COUNTDOWN)
    return {"page_id": page_id, "done": done}


def due_pages(session, now: datetime) -> List[int]:
    from app.models import MonitoredPage
    rows = session.exec(select(MonitoredPage).where(
        MonitoredPage.schedule_enabled == True,  # noqa: E712
        (MonitoredPage.next_check_at == None) | (MonitoredPage.next_check_at <= now),  # noqa: E711
    )).all()
    out = []
    for page in rows:
        first = page.next_check_at is None
        page.next_check_at = next_run(page.cron_expression, now)
        session.add(page)
        if not first and page.next_check_at is not None:
            out.append(page.id)
    session.commit()
    return out


@celery_app.task(name="app.tasks.estate_tasks.dispatch_due_page_checks")
def dispatch_due_page_checks() -> Dict[str, Any]:
    with Session(_engine) as session:
        ids = due_pages(session, datetime.utcnow())
    for pid in ids:
        check_monitored_page.delay(pid)
    return {"dispatched": len(ids)}
