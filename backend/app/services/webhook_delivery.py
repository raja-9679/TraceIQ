"""Outbound webhook deliveries: attempts, retries, redelivery (WP15f L3).

Before WP15f a delivery was fire-and-forget: one POST, the outcome folded
into three summary columns on the webhook (`last_delivery_*`,
`failure_count`), no retry although the task declared `max_retries`, no way
to see which event failed or to send it again. Now every attempt is a
`WebhookDelivery` row, a failed attempt is retried with backoff, and a
workspace admin can redeliver any logged delivery.

Safety, unchanged in spirit but now enforced at SEND time too:
* the URL goes through net_guard before every attempt (it was only checked
  when the webhook was registered — DNS can change after that);
* redirects are not followed (requests would re-POST the signed body to
  wherever a 307 pointed, including an internal address);
* the response body is never stored, and neither is the URL (it may carry a
  token) — errors are reduced to a short class/reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from app.models import WebhookDelivery, WorkspaceWebhook

MAX_ATTEMPTS = 4
# Delay before attempt 2, 3, 4.
BACKOFF_SECONDS = (60, 300, 1800)
TIMEOUT_SECONDS = 10
MAX_STORED_PAYLOAD_BYTES = 256 * 1024
USER_AGENT = "TraceIQ-Webhook/1.1"


def sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def new_delivery(webhook: WorkspaceWebhook, *, event: str, event_id: str,
                 payload: Dict[str, Any], run_id: Optional[int]) -> WebhookDelivery:
    """Attempt 1 of a fresh delivery (a new delivery id)."""
    body = json.dumps(payload, default=str)
    stored = payload if len(body.encode("utf-8")) <= MAX_STORED_PAYLOAD_BYTES else None
    return WebhookDelivery(
        webhook_id=webhook.id, workspace_id=webhook.workspace_id, run_id=run_id,
        event=event, event_id=event_id, delivery_id=str(uuid.uuid4()),
        attempt=1, payload=stored if stored is not None else _shrunk(payload),
    )


def _shrunk(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Oversized bodies keep everything but the per-result list."""
    out = dict(payload)
    results = out.pop("results", None)
    out["results_truncated"] = len(results or [])
    return out


def follow_up(prev: WebhookDelivery, *, manual: bool = False) -> WebhookDelivery:
    """The next attempt of the same delivery (same delivery id)."""
    return WebhookDelivery(
        webhook_id=prev.webhook_id, workspace_id=prev.workspace_id, run_id=prev.run_id,
        event=prev.event, event_id=prev.event_id, delivery_id=prev.delivery_id,
        attempt=1 if manual else prev.attempt + 1, manual=manual, payload=prev.payload,
    )


def _error_text(exc: BaseException) -> str:
    """A short reason with no URL in it (requests puts the full URL, query
    and all, into its connection errors)."""
    from app.core.net_guard import UnsafeUrlError
    if isinstance(exc, UnsafeUrlError):
        return f"refused by network policy: {str(exc)[:200]}"
    return type(exc).__name__


def attempt(row: WebhookDelivery, webhook: WorkspaceWebhook, *,
            post: Optional[Callable[..., Any]] = None,
            guard: Optional[Callable[[str], Any]] = None,
            now: Callable[[], datetime] = datetime.utcnow) -> bool:
    """Send one attempt; fill in `row` and the webhook's summary columns.

    Returns True on a 2xx. Never raises. The caller adds/commits and decides
    whether to schedule a retry (`retry_delay`).
    """
    if post is None:
        import requests
        post = requests.post
    if guard is None:
        from app.core.net_guard import validate_outbound_url_sync
        guard = validate_outbound_url_sync

    payload = dict(row.payload or {})
    payload["delivered_at"] = now().isoformat()
    payload["delivery_id"] = row.delivery_id
    payload["attempt"] = row.attempt
    body = json.dumps(payload, default=str).encode("utf-8")
    started = time.monotonic()
    ok = False
    try:
        guard(webhook.url)
        resp = post(
            webhook.url, data=body, timeout=TIMEOUT_SECONDS, allow_redirects=False,
            headers={
                "Content-Type": "application/json",
                "X-TraceIQ-Event": row.event,
                "X-TraceIQ-Event-Id": row.event_id,
                "X-TraceIQ-Delivery": row.delivery_id,
                "X-TraceIQ-Signature": sign(body, webhook.secret),
                "User-Agent": USER_AGENT,
            },
        )
        row.status_code = int(resp.status_code)
        ok = 200 <= row.status_code < 300
        if not ok:
            row.error = (f"redirect not followed ({row.status_code})" if 300 <= row.status_code < 400
                         else f"HTTP {row.status_code}")
    except Exception as exc:  # noqa: BLE001 — every failure is recorded
        row.status_code = None
        row.error = _error_text(exc)
    row.duration_ms = int((time.monotonic() - started) * 1000)
    row.status = "ok" if ok else "failed"
    row.completed_at = now()

    webhook.last_delivery_at = row.completed_at
    webhook.last_delivery_status = row.status_code or 0
    webhook.failure_count = 0 if ok else (webhook.failure_count or 0) + 1
    return ok


def retry_delay(row: WebhookDelivery) -> Optional[int]:
    """Seconds until the next automatic attempt, or None for no retry.

    Manual redeliveries are not retried (the person can press it again), and
    neither is a URL the network policy refused (it will refuse it again).
    4xx is retried except 410 Gone — a receiver mid-deploy often 404s/503s.
    """
    if row.status != "failed" or row.manual or row.attempt >= MAX_ATTEMPTS:
        return None
    if (row.error or "").startswith("refused by network policy"):
        return None
    if row.status_code == 410:
        return None
    return BACKOFF_SECONDS[min(row.attempt - 1, len(BACKOFF_SECONDS) - 1)]


def schedule_next(row: WebhookDelivery, now: Callable[[], datetime] = datetime.utcnow) -> Optional[int]:
    delay = retry_delay(row)
    row.next_retry_at = now() + timedelta(seconds=delay) if delay is not None else None
    return delay


def to_read(row: WebhookDelivery) -> Dict[str, Any]:
    """API shape — metadata only, never the payload."""
    return {
        "id": row.id, "webhook_id": row.webhook_id, "run_id": row.run_id,
        "event": row.event, "event_id": row.event_id, "delivery_id": row.delivery_id,
        "attempt": row.attempt, "manual": row.manual, "status": row.status,
        "status_code": row.status_code, "duration_ms": row.duration_ms, "error": row.error,
        "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
