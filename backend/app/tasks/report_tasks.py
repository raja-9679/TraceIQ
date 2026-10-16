"""Scheduled quality digests (WP12a; first version PLATFORM_VISION.md §5, item 4).

A Celery-beat task fires due ReportSchedules on their cron, builds the digest
for the window (app/services/reporting.py: pass rate, runs and failures with
the change from the previous window, top failing tests, open failure
clusters, flaky count, release-readiness verdict, escaped defects) and sends
it to the schedule's channels. `send_report_now` powers "send now".

Who receives it (decision R1, fixing the first version, which posted to the
instance-wide Slack/Teams webhook - every tenant's digest in one channel -
and emailed whatever addresses were typed in):

* email: the picked project members, each re-checked for access to the
  project at send time (`has_project_access_sync`) and skipped if they set
  this project to "off"; plus the schedule's extra addresses, validated when
  saved (rows from before WP12a keep their addresses, as extras). One message
  per recipient, so nobody sees the rest of the list.
* Slack / Teams: only the project's own webhooks (Project settings ->
  Notifications), posted through `post_webhook` (net_guard on every send).
* The instance switches (NOTIFICATIONS_ENABLED, *_NOTIFICATIONS_ENABLED) stay
  the master control; SMTP comes from instance settings.
"""
import asyncio
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

from croniter import croniter
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import Project, ProjectNotificationPref, ReportSchedule, User
from app.services import instance_settings as insvc
from app.services import reporting

logger = logging.getLogger(__name__)

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)


def digest_recipients(session: Session, schedule: ReportSchedule,
                      project: Project) -> Tuple[List[str], Dict[str, int]]:
    """(addresses, skipped counts). Picked people must still have access to
    THIS project; extra addresses are re-validated (old rows were never)."""
    from app.services.project_notifications import has_project_access_sync
    from app.settings_models import UserSettings, normalize_notification_email
    out: List[str] = []
    seen = set()
    skipped = {"no_access": 0, "inactive": 0, "opted_out": 0, "invalid_address": 0}

    def add(addr: Optional[str]):
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)

    for uid in dict.fromkeys(schedule.recipient_user_ids or []):
        user = session.get(User, uid)
        if not user or not user.email or not getattr(user, "is_active", True):
            skipped["inactive"] += 1
            continue
        if not has_project_access_sync(session, uid, project.id):
            skipped["no_access"] += 1
            continue
        pref = session.exec(select(ProjectNotificationPref).where(
            ProjectNotificationPref.user_id == uid,
            ProjectNotificationPref.project_id == project.id)).first()
        if pref and pref.mode == "off":
            skipped["opted_out"] += 1
            continue
        us = session.exec(select(UserSettings).where(UserSettings.user_id == uid)).first()
        add((getattr(us, "notification_email", None) or "").strip() or user.email)
    for raw in schedule.recipients or []:
        try:
            addr = normalize_notification_email(raw) if isinstance(raw, str) else None
        except ValueError:
            addr = None
        if not addr:
            skipped["invalid_address"] += 1
            continue
        add(addr)
    return out, {k: v for k, v in skipped.items() if v}


async def _snapshot_async(project_id: int, days: int) -> Optional[Dict[str, Any]]:
    # A private engine per call: the app's pooled async engine is bound to the
    # event loop it was first used on, and every asyncio.run is a new loop.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel.ext.asyncio.session import AsyncSession
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            project = await s.get(Project, project_id)
            if not project:
                return None
            return await reporting.project_snapshot(s, project, days)
    finally:
        await engine.dispose()


def build_snapshot(project_id: int, days: int) -> Optional[Dict[str, Any]]:
    return asyncio.run(_snapshot_async(project_id, days))


def _send_emails(subject: str, text: str, html_body: str, recipients: List[str]) -> int:
    host = insvc.effective('SMTP_HOST')
    if not host:
        logger.warning("[Digest] SMTP not configured; %d recipient(s) not emailed", len(recipients))
        return 0
    sender = insvc.effective('SMTP_FROM') or 'noreply@traceiq.io'
    user, password = insvc.effective('SMTP_USER'), insvc.effective('SMTP_PASSWORD')
    sent = 0
    with smtplib.SMTP(host, insvc.effective('SMTP_PORT') or 587) as srv:
        if user and password:
            srv.starttls()
            srv.login(user, password)
        for to in recipients:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = sender
            msg['To'] = to
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            try:
                srv.sendmail(sender, [to], msg.as_string())
                sent += 1
            except smtplib.SMTPException as e:
                logger.warning("[Digest] email to one recipient failed: %s", e)
    return sent


def _post(url: str, payload: Dict[str, Any]):
    from app.tasks.notification_tasks import post_webhook
    resp = post_webhook(url, payload)
    resp.raise_for_status()


def _deliver(schedule: ReportSchedule, session: Session,
             snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.services import project_notifications as pn
    result: Dict[str, Any] = {"at": datetime.utcnow().isoformat(), "channels_sent": [],
                              "emailed": 0, "skipped": {}, "errors": []}
    project = session.get(Project, schedule.project_id)
    if not project:
        result["errors"].append("project not found")
        return result
    if not insvc.effective('NOTIFICATIONS_ENABLED'):
        result["errors"].append("notifications are switched off for this instance")
        return result
    snap = snapshot if snapshot is not None else build_snapshot(project.id, schedule.window_days or 7)
    if snap is None:
        result["errors"].append("could not build the digest")
        return result
    base = (settings.FRONTEND_BASE_URL or "").rstrip("/")
    link = f"{base}/reports/summary" if base else None
    text = reporting.render_digest_text(snap, link)
    subject = f"TraceIQ digest - {project.name}: " + (
        f"{snap['current']['pass_rate']:g}% pass rate" if snap["current"]["pass_rate"] is not None
        else "no finished runs")

    for ch in schedule.channels or ["email"]:
        try:
            if ch == "email":
                if not insvc.effective('EMAIL_NOTIFICATIONS_ENABLED'):
                    result["errors"].append("email is switched off for this instance")
                    continue
                to, skipped = digest_recipients(session, schedule, project)
                result["skipped"] = skipped
                if to:
                    result["emailed"] = _send_emails(
                        subject, text, reporting.render_digest_html(snap, link), to)
                    if result["emailed"]:
                        result["channels_sent"].append("email")
            elif ch in ("slack", "teams"):
                if not insvc.effective(f'{ch.upper()}_NOTIFICATIONS_ENABLED'):
                    result["errors"].append(f"{ch} is switched off for this instance")
                    continue
                url = pn.webhook_url(project, ch)
                if not url:
                    result["errors"].append(f"no {ch} webhook set for this project")
                    continue
                if ch == "slack":
                    _post(url, {"text": text})
                else:
                    _post(url, {"@type": "MessageCard", "@context": "http://schema.org/extensions",
                                "summary": "TraceIQ quality digest",
                                "text": text.replace("\n", "  \n")})
                result["channels_sent"].append(ch)
        except Exception as e:  # noqa: BLE001
            # Never the webhook URL: it is a secret. The channel name is enough.
            logger.warning("[Digest] channel %s failed for schedule %s: %s",
                           ch, schedule.id, type(e).__name__)
            result["errors"].append(f"{ch} delivery failed")
    return result


@celery_app.task(name="app.tasks.report_tasks.send_scheduled_reports")
def send_scheduled_reports():
    """Beat task: deliver any report schedules that are due."""
    now = datetime.utcnow()
    with Session(sync_engine) as s:
        due = s.exec(select(ReportSchedule).where(
            ReportSchedule.is_active == True,  # noqa: E712
            ReportSchedule.next_run_at != None,  # noqa: E711
            ReportSchedule.next_run_at <= now)).all()
        for sch in due:
            try:
                sch.last_result = _deliver(sch, s)
            except Exception as e:  # noqa: BLE001
                logger.error("[Digest] schedule %s failed: %s", sch.id, e)
                sch.last_result = {"at": now.isoformat(), "errors": ["delivery failed"]}
            # Advance even after a failure: retrying every 5 minutes would
            # spam whichever channels did work.
            sch.last_run_at = now
            sch.next_run_at = croniter(sch.cron_expression, now).get_next(datetime)
            s.add(sch)
        s.commit()


@celery_app.task(name="app.tasks.report_tasks.send_report_now")
def send_report_now(schedule_id: int):
    with Session(sync_engine) as s:
        sch = s.get(ReportSchedule, schedule_id)
        if not sch:
            return
        result = _deliver(sch, s)
        sch.last_run_at = datetime.utcnow()
        sch.last_result = result
        s.add(sch)
        s.commit()
        logger.info("[Digest] sent schedule %s now: %s", schedule_id, result.get("channels_sent"))
