"""Drain the security-event outbox: evaluate alert rules, stream to the SIEM (WP7).

Runs on beat every few seconds. Two independent jobs per outbox row, tracked by
two flags so one failing never blocks the other:

1. `needs_alert_eval` — run app.services.security_alerts over the audit row.
   Each alert is sent to the instance's security channels and (when streaming
   is on) queued back into the outbox as a `security_alert` event.
2. `needs_delivery` — batch to the configured sink. A failed batch is retried
   with exponential backoff; rows stay until delivered or capped away.

Rows are claimed with `FOR UPDATE SKIP LOCKED`, so overlapping beat ticks or
several workers never send the same batch twice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import AuditLog, SecurityEventOutbox
from app.services import security_alerts, siem

logger = logging.getLogger(__name__)

_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)


def _event_for(session: Session, row: SecurityEventOutbox) -> Optional[Dict[str, Any]]:
    if row.kind == "audit":
        audit = session.exec(select(AuditLog).where(
            AuditLog.row_hash == row.audit_row_hash)).first()
        return siem.audit_event(audit) if audit else None
    return row.payload


def enforce_cap(session: Session, cap: int) -> int:
    """Drop the oldest undelivered events beyond `cap`. Returns how many."""
    if not cap or cap <= 0:
        return 0
    total = session.exec(select(func.count()).select_from(SecurityEventOutbox)).one()
    excess = int(total) - cap
    if excess <= 0:
        return 0
    doomed = session.exec(select(SecurityEventOutbox.id)
                          .order_by(SecurityEventOutbox.id).limit(excess)).all()
    session.exec(delete(SecurityEventOutbox).where(SecurityEventOutbox.id.in_(doomed)))
    logger.warning("[SIEM] outbox over its cap of %d — dropped the %d oldest event(s). "
                   "They remain in the audit table (export them from /audit/export).",
                   cap, excess)
    return excess


def evaluate_pending(session: Session, cfg: security_alerts.AlertConfig,
                     deliver: bool, batch: int = 500) -> List[Dict[str, Any]]:
    rows = session.exec(select(SecurityEventOutbox)
                        .where(SecurityEventOutbox.needs_alert_eval == True)  # noqa: E712
                        .order_by(SecurityEventOutbox.id).limit(batch)
                        .with_for_update(skip_locked=True)).all()
    raised: List[Dict[str, Any]] = []
    for row in rows:
        row.needs_alert_eval = False
        session.add(row)
        if not cfg.enabled or row.kind != "audit":
            continue
        audit = session.exec(select(AuditLog).where(
            AuditLog.row_hash == row.audit_row_hash)).first()
        if not audit:
            continue
        for alert in security_alerts.evaluate(session, audit, cfg):
            raised.append(alert)
            if deliver:
                siem.enqueue_event(session, "security_alert", siem.alert_event(alert))
    return raised


def deliver_pending(session: Session, cfg: siem.SinkConfig, *,
                    now: Optional[datetime] = None, batch: int = siem.MAX_BATCH,
                    sender=None) -> Dict[str, Any]:
    """Send one batch. `sender` is injectable for tests."""
    now = now or datetime.utcnow()
    sender = sender or siem.send
    rows = session.exec(select(SecurityEventOutbox)
                        .where(SecurityEventOutbox.needs_delivery == True,  # noqa: E712
                               SecurityEventOutbox.next_attempt_at <= now)
                        .order_by(SecurityEventOutbox.id).limit(batch)
                        .with_for_update(skip_locked=True)).all()
    if not rows:
        return {"sent": 0}
    events, carriers = [], []
    for row in rows:
        event = _event_for(session, row)
        if event is None:          # audit row aged out under retention
            row.needs_delivery = False
            session.add(row)
            continue
        events.append(event)
        carriers.append(row)
    if not carriers:
        return {"sent": 0}
    try:
        sender(cfg, events)
    except Exception as exc:  # noqa: BLE001
        for row in carriers:
            row.attempts += 1
            row.next_attempt_at = now + timedelta(seconds=siem.backoff_seconds(row.attempts))
            row.last_error = str(exc)[:500]
            session.add(row)
        logger.warning("[SIEM] delivery of %d event(s) failed (attempt %d): %s",
                       len(carriers), carriers[0].attempts if carriers else 0, exc)
        return {"sent": 0, "failed": len(carriers), "error": str(exc)[:500]}
    for row in carriers:
        row.needs_delivery = False
        row.last_error = None
        session.add(row)
    return {"sent": len(carriers)}


def drain_once(session: Session, *, sender=None, notify=None) -> Dict[str, Any]:
    alert_cfg = security_alerts.load_config()
    try:
        sink = siem.load_config()
    except siem.SiemConfigError as exc:
        logger.warning("[SIEM] streaming is on but misconfigured: %s", exc)
        sink = None
    from app.services.instance_settings import effective
    dropped = enforce_cap(session, int(effective("SIEM_OUTBOX_MAX") or 0))
    alerts = evaluate_pending(session, alert_cfg, deliver=sink is not None)
    session.commit()
    for alert in alerts:
        (notify or _queue_notification)(alert)
    out: Dict[str, Any] = {"alerts": len(alerts), "dropped": dropped}
    due = sink is not None and session.exec(select(SecurityEventOutbox.id).where(
        SecurityEventOutbox.needs_delivery == True,  # noqa: E712
        SecurityEventOutbox.next_attempt_at <= datetime.utcnow()).limit(1)).first()
    if due:
        # net_guard before every delivery, not just at save time: DNS can change.
        import asyncio
        try:
            asyncio.run(siem.check_destination(sink))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SIEM] destination refused by the outbound guard: %s", exc)
            session.commit()
            return {**out, "error": f"destination refused: {exc}"}
        out.update(deliver_pending(session, sink, sender=sender))
    session.exec(delete(SecurityEventOutbox).where(
        SecurityEventOutbox.needs_delivery == False,  # noqa: E712
        SecurityEventOutbox.needs_alert_eval == False))  # noqa: E712
    session.commit()
    return out


def _queue_notification(alert: Dict[str, Any]) -> None:
    try:
        notify_security_alert.delay(alert)
    except Exception as exc:  # noqa: BLE001
        logger.error("[SecurityAlert] could not queue notification: %s", exc)


@celery_app.task
def drain_security_events() -> Dict[str, Any]:
    with Session(_engine) as session:
        return drain_once(session)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def notify_security_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
    """Email / Slack / Teams for one alert. Each channel is independent: one
    failing does not stop the others, and only a total failure retries."""
    import smtplib
    from email.mime.text import MIMEText

    import requests

    from app.services.instance_settings import effective

    text = security_alerts.render_text(alert)
    subject = f"[TraceIQ security] {alert['title']}"
    sent, errors = [], []

    recipients = [r for r in (effective("SECURITY_ALERT_EMAILS") or []) if r]
    host = effective("SMTP_HOST")
    if recipients and host:
        try:
            msg = MIMEText(text, "plain")
            msg["Subject"] = subject
            msg["From"] = effective("SMTP_FROM") or "noreply@traceiq.io"
            msg["To"] = ", ".join(recipients)
            with smtplib.SMTP(host, effective("SMTP_PORT") or 587, timeout=15) as server:
                user, password = effective("SMTP_USER"), effective("SMTP_PASSWORD")
                if user and password:
                    server.starttls()
                    server.login(user, password)
                server.sendmail(msg["From"], recipients, msg.as_string())
            sent.append("email")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"email: {exc}")

    slack = effective("SECURITY_ALERT_SLACK_WEBHOOK_URL")
    if slack:
        try:
            r = requests.post(slack, json={"text": f"*{subject}*\n```{text}```"}, timeout=10,
                              allow_redirects=False)
            r.raise_for_status()
            sent.append("slack")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"slack: {exc}")

    teams = effective("SECURITY_ALERT_TEAMS_WEBHOOK_URL")
    if teams:
        try:
            color = "b91c1c" if alert.get("severity") in ("critical", "high") else "d97706"
            r = requests.post(teams, json={
                "@type": "MessageCard", "@context": "http://schema.org/extensions",
                "themeColor": color, "summary": subject,
                "sections": [{"activityTitle": subject, "text": text.replace("\n", "<br>")}],
            }, timeout=10, allow_redirects=False)
            r.raise_for_status()
            sent.append("teams")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"teams: {exc}")

    if errors:
        logger.error("[SecurityAlert] %s — channel errors: %s", alert.get("rule"), errors)
        if not sent:
            raise self.retry(exc=RuntimeError("; ".join(errors)))
    if not sent and not errors:
        logger.warning("[SecurityAlert] %s raised but no alert channel is configured: %s",
                       alert.get("rule"), alert.get("title"))
    return {"sent": sent, "errors": errors}


def outbox_status(session: Session) -> Dict[str, Any]:
    pending = session.exec(select(func.count()).select_from(SecurityEventOutbox).where(
        SecurityEventOutbox.needs_delivery == True)).one()  # noqa: E712
    oldest = session.exec(select(func.min(SecurityEventOutbox.created_at)).where(
        SecurityEventOutbox.needs_delivery == True)).one()  # noqa: E712
    last_error_row = session.exec(select(SecurityEventOutbox).where(
        SecurityEventOutbox.last_error != None)  # noqa: E711
        .order_by(SecurityEventOutbox.id.desc()).limit(1)).first()
    return {
        "pending": int(pending or 0),
        "oldest_pending_at": oldest.isoformat() + "Z" if oldest else None,
        "last_error": last_error_row.last_error if last_error_row else None,
        "retrying": bool(last_error_row),
    }
