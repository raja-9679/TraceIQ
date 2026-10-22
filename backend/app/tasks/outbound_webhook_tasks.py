"""Outbound webhook delivery — fan-out workspace-registered webhooks on run events.

Trigger: invoked from POST /api/runs/{id}/finalize (and any other point where
TraceIQ wants to notify external systems). Idempotency is the recipient's
responsibility — `X-TraceIQ-Delivery` is stable across retries.

Every attempt is logged (WebhookDelivery) and failures are retried with
backoff — see app/services/webhook_delivery.py.

Payload contract (sent as POST JSON):
    {
      "event":       "run.passed" | "run.failed" | "run.cancelled" | "run.timed_out"
                     | "run.completed" (anything else, e.g. an errored run),
      "event_id":    "<uuid4>",                       # one per event
      "delivery_id": "<uuid4>",   # per (event, webhook); same on retries/redelivery
      "attempt":     1,
      "delivered_at":"<ISO-8601>",
      "run":         { id, status, suite_name, ..., git_commit, triggered_by, agent_id, ... },
      "results":     [ { test_name, status, error_message, ... }, ... ]   # optional, omitted if large
    }

Signature:
    Header `X-TraceIQ-Signature: sha256=<hex>` — HMAC-SHA256 of the raw body
    using the webhook's stored `secret`. Recipients verify before trusting.
    `X-TraceIQ-Delivery` repeats delivery_id: drop a delivery id you have
    already processed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, create_engine, select

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import (
    TestRun,
    TestCaseResult,
    WorkspaceWebhook,
    Project,
)

_sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
_engine = create_engine(_sync_db_url, echo=False)


def _event_for_run(run: TestRun) -> str:
    if run.status == "failed":
        return "run.failed"
    if run.status == "passed":
        return "run.passed"
    if run.status == "cancelled":
        return "run.cancelled"
    if run.status == "timed_out":
        return "run.timed_out"
    return "run.completed"


# Before WP15f a timed-out run was ERROR and went out as "run.completed". A
# webhook filtered on "run.completed" keeps receiving those runs (and
# cancelled ones) under their new, more specific names.
_COMPLETED_ALIASES = {"run.completed", "run.cancelled", "run.timed_out"}


def _match_event_filter(event_filter: Optional[str], event: str) -> bool:
    if not event_filter:
        return True
    allowed = {e.strip() for e in event_filter.split(",") if e.strip()}
    if event in allowed:
        return True
    return "run.completed" in allowed and event in _COMPLETED_ALIASES


def _serialize_run(run: TestRun, results: List[TestCaseResult]) -> Dict[str, Any]:
    return {
        "id": run.id,
        "status": str(run.status.value) if hasattr(run.status, "value") else str(run.status),
        "suite_name": run.suite_name,
        "test_case_name": run.test_case_name,
        "project_id": run.project_id,
        "browser": run.browser,
        "device": run.device,
        "total_tests": run.total_tests,
        "passed_tests": run.passed_tests,
        "failed_tests": run.failed_tests,
        "duration_ms": run.duration_ms,
        "error_message": run.error_message,
        "trace_url": run.trace_url,
        "video_url": run.video_url,
        "git_commit": run.git_commit,
        "git_branch": run.git_branch,
        "git_pr_url": run.git_pr_url,
        "git_repo": run.git_repo,
        "triggered_by": str(run.triggered_by.value) if hasattr(run.triggered_by, "value") else str(run.triggered_by),
        "agent_id": run.agent_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _serialize_results(results: List[TestCaseResult], max_items: int = 100) -> List[Dict[str, Any]]:
    out = []
    for r in results[:max_items]:
        out.append({
            "test_name": r.test_name,
            "status": str(r.status.value) if hasattr(r.status, "value") else str(r.status),
            "duration_ms": r.duration_ms,
            "error_message": r.error_message,
            "trace_url": r.trace_url,
            "video_url": r.video_url,
        })
    return out


@celery_app.task(name="app.tasks.outbound_webhook_tasks.dispatch_run_webhooks", bind=True, max_retries=3)
def dispatch_run_webhooks(self, run_id: int) -> Dict[str, Any]:
    """Fan-out registered webhooks for a finalized run.

    Looks up the run's workspace via project → workspace, finds the active
    webhooks for it — workspace-wide ones plus those scoped to the run's own
    project (a project-scoped webhook used to receive every project's runs)
    — that match the event filter, and sends each a signed payload. Every
    attempt is logged as a WebhookDelivery; failures are retried with backoff
    (app/services/webhook_delivery.py). One target's failure never blocks
    another.
    """
    from sqlalchemy import or_
    from app.services import webhook_delivery as wd

    with Session(_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[OutboundWebhook] Run {run_id} not found")
            return {"dispatched": 0, "skipped": "run not found"}

        project = session.get(Project, run.project_id) if run.project_id else None
        workspace_id = project.workspace_id if project else None
        if not workspace_id:
            return {"dispatched": 0, "skipped": "no workspace"}
        from app.services.demo_workspace import is_demo_workspace_sync
        if is_demo_workspace_sync(session, workspace_id):
            return {"dispatched": 0, "skipped": "demo workspace"}

        webhooks = list(session.exec(
            select(WorkspaceWebhook)
            .where(WorkspaceWebhook.workspace_id == workspace_id)
            .where(WorkspaceWebhook.is_active == True)  # noqa: E712
            .where(or_(WorkspaceWebhook.project_id.is_(None),
                       WorkspaceWebhook.project_id == run.project_id))
        ))
        if not webhooks:
            return {"dispatched": 0, "skipped": "no webhooks registered"}

        results = list(session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)))
        event = _event_for_run(run)
        event_id = str(uuid.uuid4())
        payload = {
            "event": event,
            "event_id": event_id,
            "run": _serialize_run(run, results),
            "results": _serialize_results(results),
        }

        dispatched = 0
        retries = []
        for webhook in webhooks:
            if not _match_event_filter(webhook.event_filter, event):
                continue
            row = wd.new_delivery(webhook, event=event, event_id=event_id, payload=payload, run_id=run_id)
            if wd.attempt(row, webhook):
                dispatched += 1
            delay = wd.schedule_next(row)
            session.add(row)
            session.add(webhook)
            session.flush()
            if delay is not None:
                retries.append((row.id, delay))
        session.commit()

    for row_id, delay in retries:
        retry_webhook_delivery.apply_async((row_id,), countdown=delay)
    return {"dispatched": dispatched, "total_targets": len(webhooks), "retrying": len(retries)}


def _send_follow_up(row_id: int, *, manual: bool) -> Dict[str, Any]:
    from app.services import webhook_delivery as wd
    from app.models import WebhookDelivery

    with Session(_engine) as session:
        prev = session.get(WebhookDelivery, row_id)
        if prev is None:
            return {"skipped": "delivery not found"}
        webhook = session.get(WorkspaceWebhook, prev.webhook_id)
        if webhook is None or webhook.workspace_id != prev.workspace_id:
            return {"skipped": "webhook gone"}
        from app.services.demo_workspace import is_demo_workspace_sync
        if is_demo_workspace_sync(session, webhook.workspace_id):
            # Same rule as dispatch_run_webhooks: a demo never calls out, also
            # not on a retry or a redeliver of a seeded delivery.
            return {"skipped": "demo workspace"}
        if not webhook.is_active and not manual:
            return {"skipped": "webhook disabled"}
        if not manual:
            # A newer attempt of this delivery already exists (the retry was
            # queued twice, or someone redelivered meanwhile): do nothing.
            newer = session.exec(select(WebhookDelivery).where(
                WebhookDelivery.delivery_id == prev.delivery_id,
                WebhookDelivery.id > prev.id)).first()
            if newer is not None:
                return {"skipped": "superseded"}
        prev.next_retry_at = None
        row = wd.follow_up(prev, manual=manual)
        ok = wd.attempt(row, webhook)
        delay = wd.schedule_next(row)
        session.add_all([prev, row, webhook])
        session.commit()
        new_id = row.id
    if delay is not None:
        retry_webhook_delivery.apply_async((new_id,), countdown=delay)
    return {"delivery_row_id": new_id, "ok": ok, "retry_in": delay}


@celery_app.task(name="app.tasks.outbound_webhook_tasks.retry_webhook_delivery")
def retry_webhook_delivery(row_id: int) -> Dict[str, Any]:
    """Automatic retry of a failed attempt (scheduled with a countdown)."""
    return _send_follow_up(row_id, manual=False)


@celery_app.task(name="app.tasks.outbound_webhook_tasks.redeliver_webhook_delivery")
def redeliver_webhook_delivery(row_id: int) -> Dict[str, Any]:
    """A workspace admin asked to send a logged delivery again."""
    return _send_follow_up(row_id, manual=True)
