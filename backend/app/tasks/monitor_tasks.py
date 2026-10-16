"""Synthetic-monitoring evaluation (PLATFORM_VISION.md §5).

A `TestSchedule` with `is_monitor=true` turns its scheduled runs into
production health checks. After each monitor-triggered run finalises, we:

1. record a `MonitorCheck` (the source of truth for uptime/SLA),
2. compute the consecutive-failure streak,
3. transition the monitor's state (up ⇄ down), and
4. alert on the *transition* — a DOWN alert once the streak crosses
   `alert_after_failures`, and a RECOVERY alert when it passes again — using
   `last_alert_state` so we don't re-alert on every failing run.

Alerts reuse the project/global notification channel config
(`get_notification_settings`) and post to the configured Slack / Teams
webhooks. Email alerts are intentionally deferred (see PLATFORM_VISION.md).

MVP note: one check == one run. A suite that fans out to several runs produces
several checks per cycle, so `alert_after_failures` counts run-checks, not cron
cycles. Single-case / continuous-suite monitors (the common synthetic case)
map one cycle to one check exactly.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import MonitorCheck, TestRun, TestSchedule, TestStatus, Project
from app.tasks.notification_tasks import get_notification_settings, post_webhook

logger = logging.getLogger(__name__)

# Sync engine for the Celery worker (mirror notification_tasks).
sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
sync_engine = create_engine(sync_db_url, echo=False)

# Terminal statuses that count as a completed check.
_TERMINAL = {TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR}


def _schedule_id_from_run(run: TestRun) -> Optional[int]:
    """Scheduled runs are tagged `agent_id="schedule:<id>"` at dispatch."""
    agent_id = getattr(run, "agent_id", None) or ""
    if not agent_id.startswith("schedule:"):
        return None
    try:
        return int(agent_id.split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _consecutive_failures(session: Session, schedule_id: int,
                          region: Optional[str] = None) -> int:
    """Leading (most-recent-first) run of failing checks for this monitor —
    for one region when `region` is given, else across all checks (the
    unlabelled, pre-region behaviour)."""
    query = select(MonitorCheck).where(MonitorCheck.schedule_id == schedule_id)
    if region is not None:
        query = query.where(MonitorCheck.region == region)
    rows = session.exec(
        query.order_by(MonitorCheck.checked_at.desc(), MonitorCheck.id.desc())
        .limit(200)
    ).all()
    streak = 0
    for check in rows:
        if check.is_up:
            break
        streak += 1
    return streak


def transition(*, is_up: bool, streak: int, threshold: int,
               prev_state: Optional[str], prev_alert: Optional[str],
               alert_on_recovery: bool):
    """(new_state, alert_to_send or None, new_last_alert_state). Pure, so the
    single-region and per-region paths cannot drift apart."""
    if is_up:
        new_state = "up"
    elif streak >= threshold:
        new_state = "down"
    else:
        # Failing but below the alert threshold: hold prior state.
        new_state = prev_state or "up"
    alert, last_alert = None, prev_alert
    if new_state == "down" and prev_alert != "down":
        alert, last_alert = "down", "down"
    elif new_state == "up" and prev_alert == "down":
        alert = "up" if alert_on_recovery else None
        last_alert = "up"
    elif new_state == "up" and prev_alert is None:
        # First-ever check is healthy: set baseline silently.
        last_alert = "up"
    return new_state, alert, last_alert


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def evaluate_monitor_for_run(self, run_id: int):
    """Evaluate a finalised run against its monitor (no-op for non-monitors)."""
    try:
        with Session(sync_engine) as session:
            run = session.get(TestRun, run_id)
            if not run:
                return

            schedule_id = _schedule_id_from_run(run)
            if schedule_id is None:
                return  # not a scheduled run

            schedule = session.get(TestSchedule, schedule_id)
            if not schedule or not schedule.is_monitor:
                return  # schedule gone or not a monitor

            if run.status not in _TERMINAL:
                # Finalize fired before a terminal status was written; skip.
                return

            is_up = run.status == TestStatus.PASSED
            now = datetime.utcnow()
            region = getattr(run, "region", None)

            session.add(MonitorCheck(
                schedule_id=schedule_id,
                run_id=run.id,
                status=run.status,
                is_up=is_up,
                checked_at=now,
                region=region,
            ))
            session.commit()

            threshold = max(1, schedule.alert_after_failures or 1)

            if region and region in (schedule.regions or []):
                # Multi-region monitor (WP8): each region has its own streak,
                # state and alert history, so eu-west being down does not
                # suppress (or fake) us-east's alerts.
                streak = _consecutive_failures(session, schedule_id, region)
                states = dict(schedule.region_states or {})
                info = dict(states.get(region) or {})
                new_state, alert, last_alert = transition(
                    is_up=is_up, streak=streak, threshold=threshold,
                    prev_state=info.get("state"), prev_alert=info.get("last_alert_state"),
                    alert_on_recovery=schedule.alert_on_recovery)
                if alert:
                    _send_monitor_alert(session, schedule, run, alert, streak, region=region)
                _maybe_page(session, schedule, run, streak, region,
                            prev_alert=info.get("last_alert_state"), new_state=new_state)
                info.update(state=new_state, last_alert_state=last_alert,
                            last_checked_at=now.isoformat())
                states[region] = info
                schedule.region_states = states          # reassign: JSON column
                monitored = set(schedule.regions or [])
                schedule.monitor_state = "down" if any(
                    (v or {}).get("state") == "down"
                    for k, v in states.items() if k in monitored) else "up"
            else:
                streak = _consecutive_failures(session, schedule_id)
                new_state, alert, last_alert = transition(
                    is_up=is_up, streak=streak, threshold=threshold,
                    prev_state=schedule.monitor_state, prev_alert=schedule.last_alert_state,
                    alert_on_recovery=schedule.alert_on_recovery)
                if alert:
                    _send_monitor_alert(session, schedule, run, alert, streak)
                _maybe_page(session, schedule, run, streak, None,
                            prev_alert=schedule.last_alert_state, new_state=new_state)
                schedule.last_alert_state = last_alert
                schedule.monitor_state = new_state

            schedule.last_checked_at = now
            session.add(schedule)
            session.commit()

            logger.info(
                "[Monitor] schedule=%s run=%s region=%s up=%s streak=%s state=%s",
                schedule_id, run_id, region, is_up, streak, new_state)

    except Exception as e:
        logger.error("[Monitor] Error evaluating run %s: %s", run_id, e)
        raise self.retry(exc=e)


def _send_monitor_alert(session: Session, schedule: TestSchedule, run: TestRun,
                        state: str, streak: int, region: Optional[str] = None):
    """Post a DOWN/RECOVERY alert to the configured Slack/Teams webhooks.
    Paging (PagerDuty / Opsgenie) is decided separately in the evaluator."""
    project = session.get(Project, schedule.project_id) if schedule.project_id else None
    cfg = get_notification_settings(project, session)
    # Per-monitor email recipients are explicit opt-in, so they fire even when
    # the project's Slack/Teams notification settings are disabled.
    if not cfg.get("enabled", False):
        cfg = {}

    down = state == "down"
    where = f" [{region}]" if region else ""
    title = (f"🔴 Monitor DOWN: {schedule.name}{where}" if down
             else f"🟢 Monitor recovered: {schedule.name}{where}")
    detail = (f"{streak} consecutive failing check(s)." if down
              else "Monitor is passing again.")
    color = "#ef4444" if down else "#22c55e"

    if cfg.get("slack_enabled") and cfg.get("slack_webhook_url"):
        try:
            post_webhook(cfg["slack_webhook_url"], {
                "attachments": [{
                    "color": color,
                    "blocks": [
                        {"type": "header",
                         "text": {"type": "plain_text", "text": title, "emoji": True}},
                        {"type": "section", "fields": [
                            {"type": "mrkdwn", "text": f"*Monitor:*\n{schedule.name}"},
                            {"type": "mrkdwn", "text": f"*State:*\n{state.upper()}"},
                            {"type": "mrkdwn", "text": f"*Detail:*\n{detail}"},
                            {"type": "mrkdwn", "text": f"*Run:*\n#{run.id}"},
                        ]},
                    ],
                }],
            }).raise_for_status()
            logger.info("[Monitor] Slack alert sent for schedule %s (%s)", schedule.id, state)
        except Exception as e:
            logger.error("[Monitor] Slack alert failed: %s", e)

    if cfg.get("teams_enabled") and cfg.get("teams_webhook_url"):
        try:
            post_webhook(cfg["teams_webhook_url"], {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "themeColor": color.lstrip("#"),
                "summary": title,
                "sections": [{
                    "activityTitle": title,
                    "facts": [
                        {"name": "Monitor", "value": schedule.name},
                        {"name": "State", "value": state.upper()},
                        {"name": "Detail", "value": detail},
                        {"name": "Run", "value": f"#{run.id}"},
                    ],
                    "markdown": True,
                }],
            }).raise_for_status()
            logger.info("[Monitor] Teams alert sent for schedule %s (%s)", schedule.id, state)
        except Exception as e:
            logger.error("[Monitor] Teams alert failed: %s", e)

    recipients = [r for r in (schedule.alert_emails or []) if r and "@" in r]
    if recipients:
        _send_alert_email(recipients, title, schedule, state, detail, run)


def _maybe_page(session: Session, schedule: TestSchedule, run: TestRun, streak: int,
                region: Optional[str], *, prev_alert: Optional[str], new_state: str) -> None:
    if new_state == "down" and prev_alert != "down":
        _page_monitor(session, schedule, run, "down", streak, region)
    elif new_state == "up" and prev_alert == "down":
        _page_monitor(session, schedule, run, "up", 0, region)


def _page_monitor(session: Session, schedule: TestSchedule, run: TestRun,
                  state: str, streak: int, region: Optional[str]) -> None:
    """DOWN triggers, RECOVERY resolves — always, regardless of
    alert_on_recovery (that flag is about chat noise; leaving a page open
    after the monitor recovered is just wrong)."""
    if not schedule.project_id:
        return
    from app.services import paging
    where = f" [{region}]" if region else ""
    paging.page(
        session, project_id=schedule.project_id, source="monitor",
        action="trigger" if state == "down" else "resolve",
        dedup_key=paging.monitor_dedup_key(schedule.id, region),
        summary=(f"TraceIQ monitor DOWN: {schedule.name}{where} — "
                 f"{streak} consecutive failing check(s)" if state == "down"
                 else f"TraceIQ monitor recovered: {schedule.name}{where}"),
        details={"monitor": schedule.name, "schedule_id": schedule.id,
                 "region": region or "", "run_id": run.id, "streak": streak},
        link=_run_link(run.id), run_id=run.id, schedule_id=schedule.id)


def _run_link(run_id: int) -> Optional[str]:
    base = (getattr(settings, "FRONTEND_BASE_URL", None) or "").rstrip("/")
    return f"{base}/runs/{run_id}" if base else None


def _send_alert_email(recipients, title: str, schedule: TestSchedule,
                      state: str, detail: str, run: TestRun):
    """Plain SMTP alert to the monitor's explicit recipient list. Uses the
    same SMTP_* settings as account emails; silently skips when unset."""
    import smtplib
    from email.mime.text import MIMEText

    from app.core.config import settings as app_settings

    smtp_host = getattr(app_settings, "SMTP_HOST", None)
    if not smtp_host:
        logger.warning("[Monitor] alert_emails set but SMTP not configured; skipping email")
        return
    try:
        body = (
            f"Monitor: {schedule.name}\n"
            f"State: {state.upper()}\n"
            f"Detail: {detail}\n"
            f"Run: #{run.id}\n"
        )
        msg = MIMEText(body, "plain")
        msg["Subject"] = title
        msg["From"] = getattr(app_settings, "SMTP_FROM", "noreply@traceiq.io")
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP(smtp_host, getattr(app_settings, "SMTP_PORT", 587)) as server:
            smtp_user = getattr(app_settings, "SMTP_USER", None)
            smtp_password = getattr(app_settings, "SMTP_PASSWORD", None)
            if smtp_user and smtp_password:
                server.starttls()
                server.login(smtp_user, smtp_password)
            server.sendmail(msg["From"], recipients, msg.as_string())
        logger.info("[Monitor] Email alert sent to %d recipient(s) for schedule %s (%s)",
                    len(recipients), schedule.id, state)
    except Exception as e:  # noqa: BLE001 — alerting must not break evaluation
        logger.error("[Monitor] Email alert failed: %s", e)
