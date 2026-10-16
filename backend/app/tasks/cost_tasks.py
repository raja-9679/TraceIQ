"""Project budget alerts (WP12d).

Hourly: for every project with a monthly budget, price this month's usage
(app/services/cost.py) and alert once per threshold (80%, 100%) per month.

Delivery is the project's own notification rules — never the instance-wide
webhook, never an address outside the project: emails go to the project's
picked people who still have access (re-checked now) plus its extra
addresses, chat goes to the project's own Slack/Teams webhooks through the
guarded post_webhook. The instance notification switches are the master
control, as for run notifications.

Dedupe is the CostBudgetAlert unique key (project, month, threshold): the row
is inserted and committed BEFORE anything is sent, so a second beat tick or a
second beat process loses the insert and stays quiet. The cost of that order
is that a delivery failing after the insert is not retried this month — a
missed budget email beats a budget email every hour.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.services import cost as cost_svc
from app.services import instance_settings as insvc

logger = logging.getLogger(__name__)

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)


def _send_email(recipients: List[str], subject: str, text: str) -> None:
    smtp_host = insvc.effective('SMTP_HOST')
    if not smtp_host or not recipients:
        return
    smtp_port = insvc.effective('SMTP_PORT') or 587
    smtp_user = insvc.effective('SMTP_USER')
    smtp_password = insvc.effective('SMTP_PASSWORD')
    smtp_from = insvc.effective('SMTP_FROM') or 'noreply@traceiq.io'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_from
    msg['To'] = ', '.join(recipients)
    msg.attach(MIMEText(text, 'plain'))
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if smtp_user and smtp_password:
            server.starttls()
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, recipients, msg.as_string())


def deliver_budget_alert(session: Session, project, threshold: int, spent: float,
                         budget: float, currency: str, period: str) -> List[str]:
    """Send one alert through the project's channels; returns channels used."""
    from app.services.project_notifications import email_recipients
    from app.tasks.notification_tasks import get_notification_settings, post_webhook

    cfg = get_notification_settings(project, session)
    if not cfg.get('enabled'):
        return []
    word = "reached" if threshold >= 100 else "passed"
    subject = f"[TraceIQ] {project.name}: {threshold}% of the {period} budget {word}"
    text = (f"Project {project.name} has spent {spent:,.2f} {currency} of its "
            f"{budget:,.2f} {currency} budget for {period} ({threshold}% threshold). "
            f"Browser-minutes are an approximation of worker cost. "
            f"See the Costs page for the breakdown.")
    used: List[str] = []
    if cfg.get('email_enabled'):
        # A budget alert is failure-class news: people who chose "failures
        # only" for this project still get it.
        to = email_recipients(session, project, None, run_failed=True)
        if to:
            try:
                _send_email(to, subject, text)
                used.append('email')
            except Exception as exc:  # noqa: BLE001
                logger.error("[Budget] email for project %s failed: %s", project.id, exc)
    for which, payload in (('slack', {"text": f"*{subject}*\n{text}"}),
                           ('teams', {"text": f"{subject}\n\n{text}"})):
        url = cfg.get(f'{which}_webhook_url')
        if cfg.get(f'{which}_enabled') and url:
            try:
                post_webhook(url, payload).raise_for_status()
                used.append(which)
            except Exception as exc:  # noqa: BLE001
                logger.error("[Budget] %s for project %s failed: %s", which, project.id, exc)
    return used


def check_budgets(session: Session, now: datetime = None, deliver=deliver_budget_alert) -> int:
    """One pass over every budgeted project. Returns the alerts raised."""
    from app.models import CostBudgetAlert, Project, ProjectCostBudget

    now = now or datetime.utcnow()
    prices = cost_svc.load_prices()
    if not prices.enabled:
        return 0
    period = now.strftime("%Y-%m")
    since = cost_svc.month_start(now)
    raised = 0
    for b in session.exec(select(ProjectCostBudget)).all():
        project = session.get(Project, b.project_id)
        if not project or not b.monthly_amount or b.monthly_amount <= 0:
            continue
        rows = cost_svc.collect(session, project_ids=[project.id], since=since, until=now,
                                with_runs=False)
        spent = cost_svc.project_spend(prices, rows["llm_rows"], rows["browser_rows"]).get(
            project.id, Decimal(0))
        budget = Decimal(str(b.monthly_amount))
        already = set(session.exec(select(CostBudgetAlert.threshold).where(
            CostBudgetAlert.project_id == project.id, CostBudgetAlert.period == period)).all())
        spent_f = cost_svc.money(spent, prices.currency)
        won = []
        for threshold in cost_svc.budget_crossed(spent, budget):
            if threshold in already:
                continue
            session.add(CostBudgetAlert(project_id=project.id, period=period, threshold=threshold,
                                        spent=spent_f, budget=float(budget), currency=prices.currency))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()   # another beat got there first
                continue
            won.append(threshold)
        if not won:
            continue
        raised += len(won)
        # Jumping from under 80% to over 100% in one hour records both
        # thresholds but sends one message, the one that matters.
        try:
            deliver(session, project, max(won), spent_f, float(budget), prices.currency, period)
        except Exception as exc:  # noqa: BLE001
            logger.error("[Budget] delivery for project %s failed: %s", project.id, exc)
    return raised


@celery_app.task(name="app.tasks.cost_tasks.check_project_budgets")
def check_project_budgets() -> int:
    with Session(sync_engine) as session:
        return check_budgets(session)
