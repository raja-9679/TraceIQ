"""Scheduled quality reports (PLATFORM_VISION.md §5, item 4).

A Celery-beat task fires due ReportSchedules on their cron, builds a compact
quality summary (run health + open failure clusters + top failing tests) for the
window, and pushes it to the configured channels (Slack/Teams webhooks, email).
`send_report_now` powers the on-demand "send now" API.
"""
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import requests
from croniter import croniter
from sqlmodel import Session, create_engine, select, func

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import (
    ReportSchedule, TestRun, TestStatus, TestCaseResult, FailureCluster, Project,
)

logger = logging.getLogger(__name__)

sync_engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", ""), echo=False)
_FINISHED = [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]
_FAIL = [TestStatus.FAILED, TestStatus.ERROR]


def _build_summary(session: Session, project_id: int, days: int) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    project = session.get(Project, project_id)

    rows = session.exec(
        select(TestRun.status, func.count(TestRun.id))
        .where(TestRun.project_id == project_id, TestRun.created_at >= since,
               TestRun.status.in_(_FINISHED)).group_by(TestRun.status)).all()
    counts = {str(s.value if hasattr(s, "value") else s): int(c) for s, c in rows}
    total = sum(counts.values())
    pass_rate = round(100.0 * counts.get("passed", 0) / total, 1) if total else 0.0

    open_clusters = session.exec(
        select(func.count()).select_from(FailureCluster)
        .where(FailureCluster.project_id == project_id, FailureCluster.status == "open")).one()

    top = session.exec(
        select(TestCaseResult.test_name, func.count(TestCaseResult.id))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= since,
               TestCaseResult.status.in_(_FAIL))
        .group_by(TestCaseResult.test_name)
        .order_by(func.count(TestCaseResult.id).desc()).limit(5)).all()

    return {
        "project_name": project.name if project else f"project {project_id}",
        "days": days, "total_runs": total, "pass_rate": pass_rate,
        "open_clusters": int(open_clusters or 0),
        "top_failing": [(n, int(c)) for n, c in top],
    }


def _report_lines(s: dict) -> list:
    lines = [
        f"TraceIQ quality report — {s['project_name']} (last {s['days']}d)",
        f"Pass rate: {s['pass_rate']}%  |  Runs: {s['total_runs']}  |  Open failure clusters: {s['open_clusters']}",
    ]
    if s["top_failing"]:
        lines.append("Top failing tests:")
        lines += [f"  • {n} — {c} failures" for n, c in s["top_failing"]]
    else:
        lines.append("No failing tests in the window. 🎉")
    return lines


def _send_slack(text: str):
    url = getattr(settings, "SLACK_WEBHOOK_URL", None)
    if not url:
        return
    requests.post(url, json={"text": text}, timeout=10).raise_for_status()


def _send_teams(text: str):
    url = getattr(settings, "TEAMS_WEBHOOK_URL", None)
    if not url:
        return
    requests.post(url, json={"@type": "MessageCard", "@context": "http://schema.org/extensions",
                             "summary": "TraceIQ quality report", "text": text.replace("\n", "  \n")},
                  timeout=10).raise_for_status()


def _send_email(subject: str, body: str, recipients: list):
    host = getattr(settings, "SMTP_HOST", None)
    if not host or not recipients:
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = getattr(settings, "SMTP_FROM", "noreply@traceiq.io")
    msg["To"] = ", ".join(recipients)
    with smtplib.SMTP(host, getattr(settings, "SMTP_PORT", 587)) as srv:
        user = getattr(settings, "SMTP_USER", None)
        if user:
            srv.starttls()
            srv.login(user, getattr(settings, "SMTP_PASSWORD", ""))
        srv.send_message(msg)


def _deliver(schedule: ReportSchedule, session: Session) -> dict:
    summary = _build_summary(session, schedule.project_id, schedule.window_days)
    text = "\n".join(_report_lines(summary))
    channels = schedule.channels or ["slack"]
    sent = []
    for ch in channels:
        try:
            if ch == "slack":
                _send_slack(text); sent.append("slack")
            elif ch == "teams":
                _send_teams(text); sent.append("teams")
            elif ch == "email":
                _send_email(f"TraceIQ report — {summary['project_name']}", text, schedule.recipients or [])
                sent.append("email")
        except Exception as e:  # noqa: BLE001
            logger.warning("[Report] channel %s failed for schedule %s: %s", ch, schedule.id, e)
    return {"channels_sent": sent, "summary": summary, "text": text}


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
                _deliver(sch, s)
                sch.last_run_at = now
                sch.next_run_at = croniter(sch.cron_expression, now).get_next(datetime)
                s.add(sch)
            except Exception as e:  # noqa: BLE001
                logger.error("[Report] schedule %s failed: %s", sch.id, e)
        s.commit()


@celery_app.task(name="app.tasks.report_tasks.send_report_now")
def send_report_now(schedule_id: int):
    with Session(sync_engine) as s:
        sch = s.get(ReportSchedule, schedule_id)
        if not sch:
            return
        result = _deliver(sch, s)
        sch.last_run_at = datetime.utcnow()
        s.add(sch)
        s.commit()
        logger.info("[Report] sent schedule %s now: %s", schedule_id, result.get("channels_sent"))
