"""Outbound webhook delivery attempts, retries and redelivery (WP15f L3)."""
import hashlib
import hmac
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.net_guard import UnsafeUrlError
from app.models import WebhookDelivery, WorkspaceWebhook
from app.services import webhook_delivery as wd

URL = "https://hooks.example.com/services/T000/B000/SECRETTOKEN?sig=abc"


def _hook(**kw):
    return WorkspaceWebhook(id=3, workspace_id=9, name="ci", url=URL, secret="s3cret",
                            created_by_id=1, failure_count=0, **kw)


def _row(hook, **kw):
    row = wd.new_delivery(hook, event="run.failed", event_id="evt-1",
                          payload={"event": "run.failed", "run": {"id": 5}, "results": []}, run_id=5)
    for k, v in kw.items():
        setattr(row, k, v)
    return row


class Poster:
    def __init__(self, status=200, exc=None):
        self.status, self.exc, self.calls = status, exc, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return SimpleNamespace(status_code=self.status)


def _ok_guard(url):
    return url


def test_success_records_and_signs_with_delivery_header():
    hook, post = _hook(), Poster(204)
    row = _row(hook)
    assert wd.attempt(row, hook, post=post, guard=_ok_guard) is True
    assert (row.status, row.status_code, row.error) == ("ok", 204, None)
    url, kw = post.calls[0]
    assert kw["allow_redirects"] is False and kw["timeout"] == wd.TIMEOUT_SECONDS
    headers = kw["headers"]
    assert headers["X-TraceIQ-Delivery"] == row.delivery_id
    assert headers["X-TraceIQ-Event-Id"] == "evt-1"
    body = kw["data"]
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert headers["X-TraceIQ-Signature"] == expected
    sent = json.loads(body)
    assert sent["delivery_id"] == row.delivery_id and sent["attempt"] == 1 and "delivered_at" in sent
    assert hook.failure_count == 0 and hook.last_delivery_status == 204
    assert wd.retry_delay(row) is None


def test_server_error_is_retried_with_backoff_up_to_the_cap():
    hook = _hook()
    row = _row(hook)
    assert wd.attempt(row, hook, post=Poster(503), guard=_ok_guard) is False
    assert row.status == "failed" and row.error == "HTTP 503" and hook.failure_count == 1
    delays = []
    for _ in range(wd.MAX_ATTEMPTS):
        delays.append(wd.retry_delay(row))
        row = wd.follow_up(row)
        row.status, row.status_code = "failed", 503
    assert delays[:3] == list(wd.BACKOFF_SECONDS)
    assert delays[3] is None                     # attempt 4 is the last


def test_follow_up_keeps_delivery_id_and_payload():
    hook = _hook()
    first = _row(hook)
    nxt = wd.follow_up(first)
    assert (nxt.delivery_id, nxt.attempt, nxt.payload, nxt.manual) == (first.delivery_id, 2, first.payload, False)
    manual = wd.follow_up(nxt, manual=True)
    assert manual.delivery_id == first.delivery_id and manual.manual and manual.attempt == 1


def test_redirect_is_not_followed_and_reported():
    hook = _hook()
    row = _row(hook)
    wd.attempt(row, hook, post=Poster(302), guard=_ok_guard)
    assert row.status == "failed" and row.error == "redirect not followed (302)"


def test_network_errors_never_store_the_url():
    hook = _hook()
    row = _row(hook)
    exc = ConnectionError(f"HTTPSConnectionPool(host='hooks.example.com', port=443): Max retries exceeded with url: {URL}")
    wd.attempt(row, hook, post=Poster(exc=exc), guard=_ok_guard)
    assert row.error == "ConnectionError"
    assert "SECRETTOKEN" not in (row.error or "") and "sig=abc" not in (row.error or "")


def test_send_time_guard_refuses_and_is_not_retried():
    hook, post = _hook(), Poster(200)

    def guard(url):
        raise UnsafeUrlError("resolves to a private address (10.0.0.5)")
    row = _row(hook)
    assert wd.attempt(row, hook, post=post, guard=guard) is False
    assert post.calls == []                       # nothing left the building
    assert row.error.startswith("refused by network policy")
    assert wd.retry_delay(row) is None


def test_gone_and_manual_are_not_retried():
    hook = _hook()
    row = _row(hook)
    wd.attempt(row, hook, post=Poster(410), guard=_ok_guard)
    assert wd.retry_delay(row) is None
    manual = wd.follow_up(row, manual=True)
    wd.attempt(manual, hook, post=Poster(500), guard=_ok_guard)
    assert wd.retry_delay(manual) is None


def test_oversized_payload_keeps_everything_but_results():
    hook = _hook()
    big = {"event": "run.failed", "run": {"id": 1},
           "results": [{"error_message": "x" * 10_000} for _ in range(40)]}
    row = wd.new_delivery(hook, event="run.failed", event_id="e", payload=big, run_id=1)
    assert "results" not in row.payload and row.payload["results_truncated"] == 40
    assert row.payload["run"] == {"id": 1}


def test_api_shape_has_no_payload_or_url():
    hook = _hook()
    row = _row(hook, id=11, created_at=datetime(2026, 10, 20))
    out = wd.to_read(row)
    assert "payload" not in out and "url" not in out
    assert out["delivery_id"] == row.delivery_id
