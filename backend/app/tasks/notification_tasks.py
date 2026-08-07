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
    """
    Get notification settings from project or global defaults.

    Priority: Project settings override global env vars.

    Returns dict with:
        - enabled: Master switch (NOTIFICATIONS_ENABLED)
        - email_enabled: Email channel (EMAIL_NOTIFICATIONS_ENABLED)
        - slack_enabled: Slack channel (SLACK_NOTIFICATIONS_ENABLED)
        - teams_enabled: Teams channel (TEAMS_NOTIFICATIONS_ENABLED)
        - notify_on_failure_only: Skip passed runs (NOTIFY_ON_FAILURE_ONLY)
    """
    # Effective instance settings: admin UI (DB) override, else environment.
    defaults = {
        'enabled': insvc.effective('NOTIFICATIONS_ENABLED'),
        'email_enabled': insvc.effective('EMAIL_NOTIFICATIONS_ENABLED'),
        'slack_enabled': insvc.effective('SLACK_NOTIFICATIONS_ENABLED'),
        'teams_enabled': insvc.effective('TEAMS_NOTIFICATIONS_ENABLED'),
        'notify_on_failure_only': insvc.effective('NOTIFY_ON_FAILURE_ONLY'),
        'slack_webhook_url': insvc.effective('SLACK_WEBHOOK_URL'),
        'teams_webhook_url': insvc.effective('TEAMS_WEBHOOK_URL'),
    }

    logger.debug(f"[Notification] Global settings: enabled={defaults['enabled']}, "
                 f"email={defaults['email_enabled']}, slack={defaults['slack_enabled']}, "
                 f"teams={defaults['teams_enabled']}")

    if project and hasattr(project, 'notification_settings'):
        # Override with project-specific settings (allows per-project customization)
        project_settings = project.notification_settings or {}
        if project_settings:
            logger.debug(
                f"[Notification] Project overrides: {project_settings}")
        defaults.update(project_settings)

    return defaults


def build_notification_content(run: TestRun, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build notification content from run data and controller payload.
    """
    run_summary = payload.get('run_summary', {})
    ai_analysis = payload.get('ai_analysis')

    status_emoji = "✅" if run.status == TestStatus.PASSED else "❌"

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
    """
    Get email recipients based on run owner and project settings.
    """
    recipients = []

    # Run owner
    if run.user_id:
        user = session.get(User, run.user_id)
        if user and user.email:
            recipients.append(user.email)

    # Additional recipients from settings
    extra_recipients = settings_dict.get('email_recipients', [])
    recipients.extend(extra_recipients)

    return list(set(recipients))  # Deduplicate


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
        response = requests.post(webhook_url, json=payload, timeout=10)
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
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"[Teams] Sent notification for run {run.id}")
    except Exception as e:
        logger.error(f"[Teams] Failed to send notification: {e}")
        raise


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
