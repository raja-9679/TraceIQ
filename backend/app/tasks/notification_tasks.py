"""
Celery tasks for sending notifications after test run completion.
Supports email, Slack, and Teams notifications based on user/project preferences.

Environment Variables:
----------------------
Master Switch:
    NOTIFICATIONS_ENABLED=false         # If false, NO notifications sent (master off switch)

Individual Channel Controls (only checked if NOTIFICATIONS_ENABLED=true):
    EMAIL_NOTIFICATIONS_ENABLED=true    # Enable/disable email notifications
    SLACK_NOTIFICATIONS_ENABLED=true    # Enable/disable Slack notifications  
    TEAMS_NOTIFICATIONS_ENABLED=false   # Enable/disable Teams notifications

Examples:
    - Email only: NOTIFICATIONS_ENABLED=true, EMAIL_NOTIFICATIONS_ENABLED=true, others=false
    - Email+Slack: NOTIFICATIONS_ENABLED=true, EMAIL_NOTIFICATIONS_ENABLED=true, SLACK_NOTIFICATIONS_ENABLED=true
    - Disabled: NOTIFICATIONS_ENABLED=false (nothing else matters)

Additional Settings:
    NOTIFY_ON_FAILURE_ONLY=true         # Only notify on failed runs (skip passed)
    SLACK_WEBHOOK_URL=https://...       # Slack incoming webhook URL
    TEAMS_WEBHOOK_URL=https://...       # Teams incoming webhook URL
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM  # Email config
"""
from typing import Dict, Any, Optional, List
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from sqlmodel import Session, create_engine, select
from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.services import instance_settings as insvc
from app.models import TestRun, TestSuite, Project, User, TestStatus
import logging

logger = logging.getLogger(__name__)

# Use sync engine for Celery worker
sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
sync_engine = create_engine(sync_db_url, echo=False)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_run_notifications(self, run_id: int, payload: Dict[str, Any]):
    """
    Send notifications for a completed test run.

    Notification Logic:
    1. If NOTIFICATIONS_ENABLED=false → exit immediately, nothing sent
    2. If enabled, check each channel independently (email_enabled, slack_enabled, teams_enabled)
    3. Each enabled channel sends its notification

    Args:
        run_id: The test run ID
        payload: Contains ai_analysis, curated_results, run_summary from controller
    """
    try:
        with Session(sync_engine) as session:
            run = session.get(TestRun, run_id)
            if not run:
                logger.error(f"[Notification] Run {run_id} not found")
                return
            # WP15f: whoever cancelled a run knows; nobody else is told. (A
            # TIMED_OUT run is still notified, like the ERROR it used to be.)
            if run.status == TestStatus.CANCELLED:
                logger.info(f"[Notification] Run {run_id} was cancelled; not notifying")
                return

            # Get project notification settings
            project = session.get(
                Project, run.project_id) if run.project_id else None
            notification_settings = get_notification_settings(project, session)

            if not notification_settings.get('enabled', False):
                logger.info(
                    f"[Notification] Notifications disabled for run {run_id}")
                return

            # Build notification content
            content = build_notification_content(run, payload)

            # Send based on configured channels
            if notification_settings.get('email_enabled'):
                send_email_notification(
                    run, content, notification_settings, session
                )

            if notification_settings.get('slack_enabled'):
                send_slack_notification(
                    run, content, notification_settings
                )

            if notification_settings.get('teams_enabled'):
                send_teams_notification(
                    run, content, notification_settings
                )

            # Log which channels were attempted
            channels = []
            if notification_settings.get('email_enabled'):
                channels.append('email')
            if notification_settings.get('slack_enabled'):
                channels.append('slack')
            if notification_settings.get('teams_enabled'):
                channels.append('teams')
            logger.info(
                f"[Notification] Processed run {run_id}, channels: {channels or 'none enabled'}")

    except Exception as e:
        logger.error(
            f"[Notification] Error sending notifications for run {run_id}: {e}")
        raise self.retry(exc=e)


def get_notification_settings(project: Optional[Project], session: Session) -> Dict[str, Any]:
    """Effective notification settings for one project's events.

    The instance switches are the master control; the project decides who
    is emailed and which chat webhooks get its runs (see
    app/services/project_notifications.py). Chat goes ONLY to the project's
    own webhooks: the instance-wide SLACK/TEAMS_WEBHOOK_URL used to receive
    every project's runs, so it is no longer used here.

    Also used by monitor and estate alerts (same keys).
    """
    from app.services import project_notifications as pn
    from app.services.demo_workspace import is_demo_project_sync
    if project is not None and is_demo_project_sync(session, getattr(project, 'id', None)):
        # A demo workspace (WP13c) never notifies anyone, whatever its
        # project settings say.
        return {'enabled': False, 'email_enabled': False, 'slack_enabled': False,
                'teams_enabled': False, 'notify_on_failure_only': False,
                'slack_webhook_url': None, 'teams_webhook_url': None, 'demo': True}
    inst_on = bool(insvc.effective('NOTIFICATIONS_ENABLED'))
    cfg = pn.merged(project) if project else dict(pn.DEFAULTS)
    slack_url = pn.webhook_url(project, 'slack') if project else None
    teams_url = pn.webhook_url(project, 'teams') if project else None
    out = {
        'enabled': inst_on,
        'email_enabled': bool(insvc.effective('EMAIL_NOTIFICATIONS_ENABLED')) and bool(cfg.get('email_enabled')),
        'slack_enabled': bool(insvc.effective('SLACK_NOTIFICATIONS_ENABLED'))
                         and bool(cfg.get('slack_enabled')) and bool(slack_url),
        'teams_enabled': bool(insvc.effective('TEAMS_NOTIFICATIONS_ENABLED'))
                         and bool(cfg.get('teams_enabled')) and bool(teams_url),
        'notify_on_failure_only': bool(insvc.effective('NOTIFY_ON_FAILURE_ONLY')) or bool(cfg.get('failures_only')),
        'slack_webhook_url': slack_url,
        'teams_webhook_url': teams_url,
    }
    logger.debug("[Notification] project %s: email=%s slack=%s teams=%s failures_only=%s",
                 getattr(project, 'id', None), out['email_enabled'], out['slack_enabled'],
                 out['teams_enabled'], out['notify_on_failure_only'])
    return out


def post_webhook(url: str, payload: Dict[str, Any], timeout: int = 10):
    """POST to a chat webhook a project admin entered: vetted by net_guard on
    every delivery (DNS can change after saving; no metadata/link-local
    targets; private ranges only when the instance allows them) and with
    redirects off, so an allowed host cannot bounce the request elsewhere."""
    from app.core.net_guard import validate_outbound_url_sync
    validate_outbound_url_sync(url)
    return requests.post(url, json=payload, timeout=timeout, allow_redirects=False)


def build_notification_content(run: TestRun, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build notification content from run data and controller payload.
    """
    run_summary = payload.get('run_summary', {})
    ai_analysis = payload.get('ai_analysis')

    status_emoji = ("✅" if run.status == TestStatus.PASSED
                    else "⏱️" if run.status == TestStatus.TIMED_OUT else "❌")

    content = {
        'title': f"{status_emoji} Test Run {run.status.value.upper()}: {run.suite_name or 'Unknown Suite'}",
        'run_id': run.id,
        'suite_name': run.suite_name,
        'status': run.status.value,
        'passed': run.passed_tests,
        'failed': run.failed_tests,
        'total': run.total_tests,
        'duration_ms': run.duration_ms,
        'pass_rate': run_summary.get('pass_rate', 0),
        'error_message': run.error_message,
    }

    # Add AI analysis if available
    if ai_analysis:
        content['ai_analysis'] = {
            'summary': ai_analysis.get('summary'),
            'suggestions': ai_analysis.get('suggestions', []),
            'patterns': ai_analysis.get('failure_patterns', []),
        }

    return content


def send_email_notification(
    run: TestRun,
    content: Dict[str, Any],
    settings_dict: Dict[str, Any],
    session: Session
):
    """
    Send email notification to project owners/subscribers.
    """
    try:
        # Get recipients
        recipients = get_email_recipients(run, settings_dict, session)
        if not recipients:
            logger.info(f"[Email] No recipients for run {run.id}")
            return

        # Skip if notify_on_failure_only and test passed
        if settings_dict.get('notify_on_failure_only') and run.status == TestStatus.PASSED:
            logger.info(
                f"[Email] Skipping notification for passed run {run.id}")
            return

        # Build email
        subject = content['title']
        body = build_email_body(content)

        # Send via SMTP (effective settings: admin UI override, else env)
        smtp_host = insvc.effective('SMTP_HOST')
        smtp_port = insvc.effective('SMTP_PORT') or 587
        smtp_user = insvc.effective('SMTP_USER')
        smtp_password = insvc.effective('SMTP_PASSWORD')
        smtp_from = insvc.effective('SMTP_FROM') or 'noreply@traceiq.io'

        if not smtp_host:
            logger.warning("[Email] SMTP not configured, skipping email")
            return

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = ', '.join(recipients)

        # Plain text and HTML versions
        text_part = MIMEText(body['text'], 'plain')
        html_part = MIMEText(body['html'], 'html')
        msg.attach(text_part)
        msg.attach(html_part)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_password:
                server.starttls()
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, recipients, msg.as_string())

        logger.info(
            f"[Email] Sent notification to {len(recipients)} recipients for run {run.id}")

    except Exception as e:
        logger.error(f"[Email] Failed to send notification: {e}")
        raise


def get_email_recipients(run: TestRun, settings_dict: Dict[str, Any], session: Session) -> List[str]:
    """Who is emailed about this run: the starter and the people the project
    picked (each still a project member, each on their own terms), plus the
    project's extra addresses. Never anyone outside the project."""
    from app.services.project_notifications import email_recipients
    project = session.get(Project, run.project_id) if run.project_id else None
    if project is None:
        return []
    return email_recipients(session, project, run.user_id, run_failed=run.status != TestStatus.PASSED)


def build_email_body(content: Dict[str, Any]) -> Dict[str, str]:
    """
    Build plain text and HTML email body.
    User-controlled strings are html.escape()'d before being embedded in HTML.
    """
    import html as html_lib

    # Escape all user-controlled strings used in HTML
    safe_title = html_lib.escape(str(content.get('title', '')))
    safe_suite_name = html_lib.escape(str(content.get('suite_name', '')))
    safe_error = html_lib.escape(str(content['error_message'])) if content.get('error_message') else None

    ai = content.get('ai_analysis')
    safe_ai_summary = html_lib.escape(str(ai.get('summary', 'No analysis available'))) if ai else None
    safe_ai_suggestions = [html_lib.escape(str(s)) for s in (ai.get('suggestions', [])[:3] if ai else [])]

    text = f"""
Test Run Report
===============

Suite: {content.get('suite_name', '')}
Status: {content['status'].upper()}
Results: {content['passed']}/{content['total']} passed ({content['pass_rate']:.1f}%)
Duration: {content['duration_ms']/1000:.1f}s

"""

    if content['failed'] > 0 and content.get('error_message'):
        text += f"Error: {content['error_message']}\n\n"

    if ai:
        text += f"AI Analysis\n-----------\n{ai.get('summary', 'No analysis available')}\n\n"
        if ai.get('suggestions'):
            text += "Suggestions:\n"
            for suggestion in ai['suggestions'][:3]:
                text += f"  • {suggestion}\n"

    header_color = '#22c55e' if content['status'] == 'passed' else '#ef4444'
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background: {header_color}; color: white; padding: 15px; border-radius: 8px; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat {{ background: #f3f4f6; padding: 15px; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; }}
        .ai-section {{ background: #fef3c7; padding: 15px; border-radius: 8px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{safe_title}</h1>
    </div>

    <div class="stats">
        <div class="stat">
            <div class="stat-value">{content['passed']}</div>
            <div>Passed</div>
        </div>
        <div class="stat">
            <div class="stat-value">{content['failed']}</div>
            <div>Failed</div>
        </div>
        <div class="stat">
            <div class="stat-value">{content['pass_rate']:.1f}%</div>
            <div>Pass Rate</div>
        </div>
        <div class="stat">
            <div class="stat-value">{content['duration_ms']/1000:.1f}s</div>
            <div>Duration</div>
        </div>
    </div>
"""

    if safe_error:
        html += f'<div style="background: #fee2e2; padding: 15px; border-radius: 8px;"><strong>Error:</strong> {safe_error}</div>'

    if ai and safe_ai_summary:
        html += f"""
    <div class="ai-section">
        <h3>AI Analysis</h3>
        <p>{safe_ai_summary}</p>
"""
        if safe_ai_suggestions:
            html += "<h4>Suggestions:</h4><ul>"
            for suggestion in safe_ai_suggestions:
                html += f"<li>{suggestion}</li>"
            html += "</ul>"
        html += "</div>"

    html += "</body></html>"

    return {'text': text, 'html': html}


def send_slack_notification(run: TestRun, content: Dict[str, Any], settings_dict: Dict[str, Any]):
    """
    Send Slack notification via webhook.
    """
    webhook_url = settings_dict.get('slack_webhook_url')
    if not webhook_url:
        logger.warning("[Slack] No webhook URL configured")
        return

    # Skip if notify_on_failure_only and test passed
    if settings_dict.get('notify_on_failure_only') and run.status == TestStatus.PASSED:
        return

    color = "#22c55e" if run.status == TestStatus.PASSED else "#ef4444"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": content['title'],
                "emoji": True
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn",
                    "text": f"*Suite:*\n{content['suite_name']}"},
                {"type": "mrkdwn",
                    "text": f"*Status:*\n{content['status'].upper()}"},
                {"type": "mrkdwn",
                    "text": f"*Passed:*\n{content['passed']}/{content['total']}"},
                {"type": "mrkdwn",
                    "text": f"*Duration:*\n{content['duration_ms']/1000:.1f}s"}
            ]
        }
    ]

    if content.get('ai_analysis') and content['ai_analysis'].get('summary'):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🤖 AI Analysis:*\n{content['ai_analysis']['summary']}"
            }
        })

    payload = {
        "attachments": [{
            "color": color,
            "blocks": blocks
        }]
    }

    try:
        response = post_webhook(webhook_url, payload)
        response.raise_for_status()
        logger.info(f"[Slack] Sent notification for run {run.id}")
    except Exception as e:
        logger.error(f"[Slack] Failed to send notification: {e}")
        raise


def send_teams_notification(run: TestRun, content: Dict[str, Any], settings_dict: Dict[str, Any]):
    """
    Send Microsoft Teams notification via webhook.
    """
    webhook_url = settings_dict.get('teams_webhook_url')
    if not webhook_url:
        logger.warning("[Teams] No webhook URL configured")
        return

    # Skip if notify_on_failure_only and test passed
    if settings_dict.get('notify_on_failure_only') and run.status == TestStatus.PASSED:
        return

    theme_color = "22c55e" if run.status == TestStatus.PASSED else "ef4444"

    # Teams Adaptive Card format
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme_color,
        "summary": content['title'],
        "sections": [{
            "activityTitle": content['title'],
            "facts": [
                {"name": "Suite", "value": content['suite_name']},
                {"name": "Status", "value": content['status'].upper()},
                {"name": "Results",
                    "value": f"{content['passed']}/{content['total']} passed"},
                {"name": "Pass Rate", "value": f"{content['pass_rate']:.1f}%"},
                {"name": "Duration",
                    "value": f"{content['duration_ms']/1000:.1f}s"}
            ],
            "markdown": True
        }]
    }

    if content.get('ai_analysis') and content['ai_analysis'].get('summary'):
        payload["sections"].append({
            "activityTitle": "🤖 AI Analysis",
            "text": content['ai_analysis']['summary']
        })

    try:
        response = post_webhook(webhook_url, payload)
        response.raise_for_status()
        logger.info(f"[Teams] Sent notification for run {run.id}")
    except Exception as e:
        logger.error(f"[Teams] Failed to send notification: {e}")
        raise


@celery_app.task(name="app.tasks.notification_tasks.notify_case_owners_for_run")
def notify_case_owners_for_run(run_id: int):
    """Email each failing case's owner when this run is the case's first
    failure after a pass (WP16b, V5). Queued once per finished run
    (run_completion.SIDE_EFFECT_TASKS); see owner_notices_for_run."""
    try:
        with Session(sync_engine) as session:
            sent = owner_notices_for_run(session, run_id)
        if sent:
            logger.info("[OwnerNotice] run %s: notified owners of %s case(s)", run_id, sent)
    except Exception as e:  # noqa: BLE001 — best effort, never retried into duplicates
        logger.error("[OwnerNotice] run %s: %s", run_id, e)


def owner_notices_for_run(session: Session, run_id: int, send=None) -> int:
    """Returns how many cases produced a notice.

    Rules: project opted in (`notify_case_owners`) and notifications/email
    on at instance level (demo projects never notify); the run is not
    cancelled; a case result failed for a test reason (not infrastructure /
    cancelled / timeout / not executed; manual results excluded); the case's
    newest verdict BEFORE this run was a pass. The recipient is the owner,
    or the backup when the owner can't be reached — each must be active,
    still on the project and not have muted the project ("off").

    Dedupe: a CaseOwnerNotice row keyed (case, last passing result) is
    committed BEFORE sending, so a retried task, a second finalizer or two
    concurrent failing runs produce one email per pass->fail transition."""
    from sqlalchemy.exc import IntegrityError
    from app.models import CaseOwnerNotice, ProjectNotificationPref, TestCase, TestCaseResult
    from app.services import project_notifications as pn, run_scope
    from app.services.ownership_review import is_test_failure
    from app.settings_models import UserSettings
    send = send or send_account_email

    run = session.get(TestRun, run_id)
    if not run or not run.project_id or run.status == TestStatus.CANCELLED:
        return 0
    project = session.get(Project, run.project_id)
    if project is None or not pn.merged(project).get("notify_case_owners"):
        return 0
    ns = get_notification_settings(project, session)
    if not ns.get("enabled") or not ns.get("email_enabled"):
        return 0

    failing: Dict[int, TestCaseResult] = {}
    for r in session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id,
                                                       TestCaseResult.test_case_id.is_not(None))).all():
        if r.result_kind == "manual" or not is_test_failure(r.status, r.error_kind):
            continue
        failing.setdefault(r.test_case_id, r)
    verdicts = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
    notified = 0
    for case_id, result in failing.items():
        case = session.get(TestCase, case_id)
        if case is None or case.project_id != project.id or not (case.owner_user_id or case.backup_owner_user_id):
            continue
        prev = session.exec(
            select(TestCaseResult).join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(TestCaseResult.test_case_id == case_id, TestRun.project_id == project.id,
                   TestRun.id != run.id, TestRun.created_at <= run.created_at,
                   # Validation runs (WP16c) ran an overlay, not this test.
                   run_scope.counted(TestRun),
                   TestCaseResult.status.in_(verdicts),
                   (TestCaseResult.result_kind.is_(None)) | (TestCaseResult.result_kind != "manual"))
            .order_by(TestRun.created_at.desc(), TestCaseResult.id.desc()).limit(1)).first()
        if prev is None or prev.status != TestStatus.PASSED:
            continue  # never ran before, or already failing: not a NEW failure

        def reachable(uid):
            if not uid:
                return None
            user = session.get(User, uid)
            if not user or not user.email or not getattr(user, "is_active", True):
                return None
            if not pn.has_project_access_sync(session, uid, project.id):
                return None
            pref = session.exec(select(ProjectNotificationPref).where(
                ProjectNotificationPref.user_id == uid,
                ProjectNotificationPref.project_id == project.id)).first()
            if pref and pref.mode == "off":
                return None
            us = session.exec(select(UserSettings).where(UserSettings.user_id == uid)).first()
            return (getattr(us, "notification_email", None) or "").strip() or user.email
        to = reachable(case.owner_user_id) or reachable(case.backup_owner_user_id)
        if not to:
            continue
        notice = CaseOwnerNotice(project_id=project.id, test_case_id=case_id, test_run_id=run.id,
                                 after_result_id=prev.id, recipients=1)
        session.add(notice)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            continue
        subject, html, text = _owner_notice_body(project, case, run, result)
        send(to, subject, html, text)
        notified += 1
    return notified


def _owner_notice_body(project, case, run, result):
    import html as _html
    base = (getattr(settings, 'FRONTEND_BASE_URL', '') or '').rstrip('/')
    link = f"{base}/runs/{run.id}" if base else f"run #{run.id}"
    # Case names are user text: no control characters in a mail header.
    clean_name = "".join(ch for ch in (case.name or "") if ch.isprintable())
    subject = f"[TraceIQ] Your test started failing: {clean_name}"[:200]
    text = (f"The test \"{case.name}\" in project \"{project.name}\" failed in run #{run.id} "
            f"after passing previously.\n\nOpen the run: {link}\n\n"
            "You get this because you own (or back up) this test. Project admins can turn these "
            "emails off in the project's notification settings.")
    html_body = (f"<p>The test <strong>{_html.escape(case.name)}</strong> in project "
                 f"<strong>{_html.escape(project.name or '')}</strong> failed in run #{run.id} after "
                 f"passing previously.</p><p><a href=\"{_html.escape(link)}\">Open the run</a></p>"
                 "<p style=\"color:#666\">You get this because you own (or back up) this test.</p>")
    return subject, html_body, text


@celery_app.task(name="app.tasks.notification_tasks.send_account_email")
def send_account_email(to_email: str, subject: str, html_body: str, text_body: str = None):
    """Send a transactional account email (password reset / verification).

    Reuses the SMTP configuration. Best-effort: logs and returns instead of
    raising so token creation is never rolled back by a mail failure. When SMTP
    is unconfigured (dev), the message (including any link) is logged so flows
    remain testable.
    """
    smtp_host = insvc.effective('SMTP_HOST')
    smtp_from = insvc.effective('SMTP_FROM') or 'noreply@traceiq.io'
    if not smtp_host:
        logger.warning("[Account] SMTP not configured; would send to %s: %s\n%s",
                       to_email, subject, text_body or html_body)
        return

    try:
        smtp_port = insvc.effective('SMTP_PORT') or 587
        smtp_user = insvc.effective('SMTP_USER')
        smtp_password = insvc.effective('SMTP_PASSWORD')

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = to_email
        msg.attach(MIMEText(text_body or '', 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_password:
                server.starttls()
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        logger.info("[Account] Sent '%s' to %s", subject, to_email)
    except Exception as e:
        logger.error("[Account] Failed to send account email to %s: %s", to_email, e)
