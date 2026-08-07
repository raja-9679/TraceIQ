"""Outbound webhook delivery — fan-out workspace-registered webhooks on run events.

Trigger: invoked from POST /api/runs/{id}/finalize (and any other point where
TraceIQ wants to notify external systems). Idempotency is the recipient's
responsibility — we send a stable `event_id` per delivery attempt.

Payload contract (sent as POST JSON):
    {
      "event":       "run.completed" | "run.failed" | "run.passed",
      "event_id":    "<uuid4>",
      "delivered_at":"<ISO-8601>",
      "run":         { id, status, suite_name, ..., git_commit, triggered_by, agent_id, ... },
      "results":     [ { test_name, status, error_message, ... }, ... ]   # optional, omitted if large
    }

Signature:
    Header `X-TraceIQ-Signature: sha256=<hex>` — HMAC-SHA256 of the raw body
    using the webhook's stored `secret`. Recipients verify before trusting.
"""
from __future__ import annotations

import hashlib
import hmac
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
    return "run.completed"


def _match_event_filter(event_filter: Optional[str], event: str) -> bool:
    if not event_filter:
        return True
    allowed = {e.strip() for e in event_filter.split(",") if e.strip()}
    return event in allowed


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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

    Lookups the run's workspace via project → workspace, finds active webhooks
    that match the event filter, and POSTs a signed payload to each. Per-target
    failures are recorded but do not block other targets.
    """
    import requests  # local import keeps cold-start small

    with Session(_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[OutboundWebhook] Run {run_id} not found")
            return {"dispatched": 0, "skipped": "run not found"}

        project = session.get(Project, run.project_id) if run.project_id else None
        workspace_id = project.workspace_id if project else None
        if not workspace_id:
            return {"dispatched": 0, "skipped": "no workspace"}

        results_stmt = select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)
        results = list(session.exec(results_stmt))

        webhooks_stmt = (
            select(WorkspaceWebhook)
            .where(WorkspaceWebhook.workspace_id == workspace_id)
            .where(WorkspaceWebhook.is_active == True)  # noqa: E712
        )
        webhooks = list(session.exec(webhooks_stmt))
        if not webhooks:
            return {"dispatched": 0, "skipped": "no webhooks registered"}

        event = _event_for_run(run)
        payload = {
            "event": event,
            "event_id": str(uuid.uuid4()),
            "delivered_at": datetime.utcnow().isoformat(),
            "run": _serialize_run(run, results),
            "results": _serialize_results(results),
        }
        body_bytes = json.dumps(payload, default=str).encode("utf-8")

        dispatched = 0
        for webhook in webhooks:
            if not _match_event_filter(webhook.event_filter, event):
                continue
            try:
                resp = requests.post(
                    webhook.url,
                    data=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-TraceIQ-Event": event,
                        "X-TraceIQ-Event-Id": payload["event_id"],
                        "X-TraceIQ-Signature": _sign(body_bytes, webhook.secret),
                        "User-Agent": "TraceIQ-Webhook/1.0",
                    },
                    timeout=10,
                )
                webhook.last_delivery_at = datetime.utcnow()
                webhook.last_delivery_status = resp.status_code
                if resp.status_code >= 300:
                    webhook.failure_count += 1
                else:
                    webhook.failure_count = 0
                    dispatched += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[OutboundWebhook] Delivery to {webhook.url} failed: {exc}")
                webhook.last_delivery_at = datetime.utcnow()
                webhook.last_delivery_status = 0
                webhook.failure_count += 1
            session.add(webhook)

        session.commit()
        return {"dispatched": dispatched, "total_targets": len(webhooks)}
