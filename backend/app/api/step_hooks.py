"""Test inbox + webhook capture endpoints (wait-for-email / wait-for-webhook).

Three audiences, three auth models:

* **Workers** poll `POST /internal/test-inbox/search` and
  `POST /internal/webhook-captures/search` with the shared worker secret
  (`X-Worker-Secret`, like /internal/llm-usage). They name a run, case, data
  row and label — never an address or token. The backend derives the address
  / token itself, and only for a live run whose project owns that case, so a
  worker cannot read any mailbox or capture outside its own run.
* **The app under test** POSTs to `/hooks/capture/{token}[/...]`. Public and
  unauthenticated by necessity (third-party webhook senders do not carry our
  credentials); the token is HMAC-verified, the run must be live, each token
  holds at most MAX_CAPTURES_PER_TOKEN rows, bodies are capped, everything is
  redacted before it is stored, and the route is rate limited.
* **Instance admins** test the inbox connection from the settings page.
"""
from __future__ import annotations

import hmac
from typing import Optional

from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.core.config import settings
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import Project, TestCase, TestRun, TestStatus, User, WebhookCapture
from app.services import inbox_providers, step_hooks
from app.services.data_policy import resolve_for_project
from app.services.redaction import (
    REDACTED, RedactionPolicy, _normalize_key, redact_body, redact_headers, redact_text,
)

router = APIRouter()

MAX_CAPTURE_BYTES = 256 * 1024
MAX_CAPTURES_PER_TOKEN = 50
MAX_HEADERS = 100
MAX_HEADER_VALUE = 2000
SEARCH_LIMIT = 50
CAPTURE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_LIVE = (TestStatus.PENDING, TestStatus.RUNNING)


def _require_worker(secret: str) -> None:
    expected = settings.WEBHOOK_SECRET or settings.SECRET_KEY
    if not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="Invalid worker secret")


def _is_live(run: Optional[TestRun]) -> bool:
    return bool(run) and run.finalized_at is None and run.status in _LIVE


async def _live_run_for_case(session: AsyncSession, run_id: int, case_id: int):
    """(run, project) when `case_id` may use hooks in `run_id`; 404 otherwise.

    The case must belong to the run's project: the address derivation is keyed
    on the case id, so without this a worker could mint another project's
    address just by naming that project's case alongside any live run.
    """
    run = await session.get(TestRun, run_id)
    if not _is_live(run):
        raise HTTPException(status_code=404, detail="run is not live")
    case = await session.get(TestCase, case_id)
    if not case or case.project_id != run.project_id:
        raise HTTPException(status_code=404, detail="test case is not part of this run's project")
    project = await session.get(Project, run.project_id) if run.project_id else None
    return run, project


def _policy(project) -> RedactionPolicy:
    return RedactionPolicy.from_data_policy(resolve_for_project(project) if project else None)


# --------------------------------------------------------------------------
# Worker: inbox
# --------------------------------------------------------------------------

class InboxSearch(BaseModel):
    run_id: int
    test_case_id: int
    row_index: Optional[int] = None
    label: str = step_hooks.DEFAULT_LABEL
    subject: str = Field(default="", max_length=300)
    subject_regex: str = Field(default="", max_length=300)
    from_contains: str = Field(default="", max_length=300)
    extract: str = "none"
    pattern: str = Field(default="", max_length=300)


@router.post("/internal/test-inbox/search")
async def search_test_inbox(
    body: InboxSearch,
    x_worker_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    _require_worker(x_worker_secret)
    _run, project = await _live_run_for_case(session, body.run_id, body.test_case_id)
    cfg = step_hooks.load_inbox_config()
    try:
        address = step_hooks.inbox_address(cfg, body.run_id, body.test_case_id,
                                           body.row_index, body.label)
        provider = inbox_providers.provider_for(cfg)
        result = await inbox_providers.search(
            provider, address, subject=body.subject, subject_regex=body.subject_regex,
            sender=body.from_contains, extract_mode=body.extract, pattern=body.pattern,
            policy=_policy(project))
    except step_hooks.StepHooksError as exc:
        # 422 is "your step is wrong / the inbox is not set up" — the worker
        # fails the step at once instead of polling out the timeout.
        raise HTTPException(status_code=422, detail=str(exc))
    except inbox_providers.InboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"address": address, **result}


# --------------------------------------------------------------------------
# Worker: webhook captures
# --------------------------------------------------------------------------

class CaptureSearch(BaseModel):
    run_id: int
    test_case_id: int
    row_index: Optional[int] = None
    label: str = step_hooks.DEFAULT_LABEL


def capture_dict(c: WebhookCapture) -> dict:
    return {
        "id": c.id, "method": c.method, "path": c.path, "query": c.query,
        "headers": c.headers or {}, "content_type": c.content_type, "body": c.body,
        "body_size": c.body_size, "truncated": c.truncated,
        "received_at": c.received_at.isoformat() + "Z",
    }


@router.post("/internal/webhook-captures/search")
async def search_webhook_captures(
    body: CaptureSearch,
    x_worker_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    _require_worker(x_worker_secret)
    await _live_run_for_case(session, body.run_id, body.test_case_id)
    try:
        token = step_hooks.webhook_token(body.run_id, body.test_case_id, body.row_index, body.label)
    except step_hooks.StepHooksError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    rows = (await session.exec(
        select(WebhookCapture).where(WebhookCapture.token == token)
        .order_by(WebhookCapture.id.desc()).limit(SEARCH_LIMIT))).all()
    return {"captures": [capture_dict(c) for c in rows]}


# --------------------------------------------------------------------------
# Public: the capture URL the app under test calls
# --------------------------------------------------------------------------

async def _read_capped(request: Request) -> tuple[bytes, int, bool]:
    """Body up to MAX_CAPTURE_BYTES. Stops reading past the cap instead of
    buffering an arbitrarily large upload just to throw most of it away."""
    buf = bytearray()
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        room = MAX_CAPTURE_BYTES - len(buf)
        if room > 0:
            buf.extend(chunk[:room])
        if received > MAX_CAPTURE_BYTES:
            break
    declared = request.headers.get("content-length", "")
    size = int(declared) if declared.isdigit() else received
    return bytes(buf), max(size, received), received > MAX_CAPTURE_BYTES


@limiter.limit("120/minute")
async def capture_webhook(
    request: Request,
    token: str,
    subpath: str = "",
    session: AsyncSession = Depends(get_session),
):
    ref = step_hooks.parse_webhook_token(token)
    if ref is None:
        # Same answer for malformed and forged: nothing to learn by probing.
        raise HTTPException(status_code=404, detail="unknown capture URL")
    run = await session.get(TestRun, ref.run_id)
    if not _is_live(run):
        raise HTTPException(status_code=410, detail="this capture URL's test run has finished")
    stored = (await session.exec(
        select(func.count()).select_from(WebhookCapture)
        .where(WebhookCapture.token == token))).one()
    if stored >= MAX_CAPTURES_PER_TOKEN:
        raise HTTPException(status_code=429, detail="capture limit reached for this URL")

    raw, size, truncated = await _read_capped(request)
    project = await session.get(Project, run.project_id) if run.project_id else None
    policy = _policy(project)
    content_type = request.headers.get("content-type", "")[:200]
    text = raw.decode("utf-8", errors="replace")
    headers = {k: v[:MAX_HEADER_VALUE] for k, v in list(request.headers.items())[:MAX_HEADERS]}

    capture = WebhookCapture(
        token=token, run_id=ref.run_id, test_case_id=ref.case_id, label=ref.label,
        method=request.method, path=("/" + subpath) if subpath else "",
        query=_redact_query(str(request.url.query)[:2000], policy),
        headers=redact_headers(headers, policy),
        content_type=content_type,
        # A truncated JSON body no longer parses, so redact_body falls back to
        # the value-pattern sweep for it — the denylist needs whole documents.
        body=redact_body(text, content_type, policy) or "",
        body_size=size, truncated=truncated,
    )
    session.add(capture)
    await session.commit()
    return {"ok": True, "id": capture.id}


# Query-string names that carry credentials in webhook/callback URLs but are
# too generic for the global denylist (which also applies to request bodies).
_QUERY_EXTRA_KEYS = frozenset({"signature", "sig", "token", "code"})


def _redact_query(query: str, policy: RedactionPolicy) -> str:
    """Webhook senders often put a token or signature in the query string;
    it is stored, so redact it like a body: sensitive keys by name, the rest by
    value pattern."""
    if not query:
        return ""
    # Query keys can be either kind of name (`access_token`, `x-api-key`).
    sensitive = (policy.body_key_set() | {_normalize_key(h) for h in policy.header_set()}
                 | _QUERY_EXTRA_KEYS)
    out = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if _normalize_key(key) in sensitive:
            value = REDACTED
        else:
            value = redact_text(value, policy)
        out.append((key, value))
    return urlencode(out, safe="[]*")


# One route per method: a single multi-method route gives every method the same
# OpenAPI operationId, which the generated frontend types reject as duplicates.
for _path in ("/hooks/capture/{token}", "/hooks/capture/{token}/{subpath:path}"):
    for _method in CAPTURE_METHODS:
        router.add_api_route(_path, capture_webhook, methods=[_method],
                             operation_id=f"capture_webhook_{_method.lower()}",
                             include_in_schema=_path.endswith("{token}"))


# --------------------------------------------------------------------------
# Admin: connection test
# --------------------------------------------------------------------------

@router.post("/admin/instance-settings/test-inbox")
async def check_inbox_connection(_: User = Depends(get_current_instance_admin)):
    """Check the EFFECTIVE inbox config (save first) and show what an address
    and a capture URL will look like."""
    cfg = step_hooks.load_inbox_config()
    try:
        provider = inbox_providers.provider_for(cfg)
        detail = await provider.check()
        example = step_hooks.inbox_address(cfg, 0, 0, None, step_hooks.DEFAULT_LABEL)
    except step_hooks.StepHooksError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except inbox_providers.InboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "ok": True, "detail": detail, "example_address": example,
        "webhook_base": step_hooks.webhook_url(step_hooks.public_base_url(), "<token>"),
    }
