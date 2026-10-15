"""Worker → backend authentication for `/api/internal/*` (WP8).

Two layers, both required when the second is switched on:

1. **The shared worker secret** (`X-Worker-Secret`, = WEBHOOK_SECRET, falling
   back to SECRET_KEY exactly as the older internal endpoints always did).
2. **Optional mTLS** (`REQUIRE_WORKER_MTLS`). TLS terminates at the reverse
   proxy, so the backend cannot see the certificate itself; nginx verifies it
   (`ssl_verify_client`) and reports the result in `X-Client-Verify` (and the
   subject in `X-Client-Cert-Subject`). Those headers are believed **only from
   a trusted proxy** (`TRUSTED_PROXY_CIDRS`) — from anyone else they are
   attacker-supplied text, and a worker that can reach the backend directly
   could simply claim `SUCCESS`.

The secret is kept even with mTLS on: a certificate proves which machine is
calling, the secret proves it was configured by this deployment — losing one
should not be enough.
"""
from __future__ import annotations

import hmac
import json
from typing import List

from fastapi import Header, HTTPException

from app.core.config import settings

INTERNAL_PREFIXES = ("/api/internal/", "/internal/")


def expected_worker_secret() -> str:
    return settings.WEBHOOK_SECRET or settings.SECRET_KEY


def worker_secret_ok(presented: str) -> bool:
    expected = expected_worker_secret()
    return bool(presented) and bool(expected) and hmac.compare_digest(presented, expected)


def require_worker(x_worker_secret: str = Header(default="")) -> None:
    """FastAPI dependency for internal endpoints."""
    if not worker_secret_ok(x_worker_secret):
        raise HTTPException(status_code=403, detail="Invalid worker secret")


def mtls_verdict(peer: str, headers: dict, *, required: bool,
                 trusted: list, allowed_subjects: List[str]) -> str:
    """'' when the request may proceed, else the refusal reason. Pure, for tests."""
    if not required:
        return ""
    from app.core.client_ip import ip_in
    if not trusted or not ip_in(peer, trusted):
        return ("worker mTLS is required but this request did not come through a "
                "trusted proxy (TRUSTED_PROXY_CIDRS)")
    if (headers.get("x-client-verify") or "").strip().upper() != "SUCCESS":
        return "worker mTLS is required: no verified client certificate"
    if allowed_subjects:
        subject = (headers.get("x-client-cert-subject") or "").strip()
        if subject not in allowed_subjects:
            return "worker client certificate subject is not allowed"
    return ""


class WorkerMTLSMiddleware:
    """Pure ASGI: refuses `/api/internal/*` without proxy-verified mTLS when
    REQUIRE_WORKER_MTLS is on. A no-op for every other path and when off."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(INTERNAL_PREFIXES):
            return await self.app(scope, receive, send)
        from app.core.client_ip import trusted_proxies
        from app.services.instance_settings import effective
        required = bool(effective("REQUIRE_WORKER_MTLS"))
        if not required:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        peer = (scope.get("client") or ("", 0))[0]
        reason = mtls_verdict(
            peer, headers, required=True, trusted=trusted_proxies(),
            allowed_subjects=as_list_lines(effective("WORKER_MTLS_ALLOWED_SUBJECTS")))
        if not reason:
            return await self.app(scope, receive, send)
        body = json.dumps({"detail": reason}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def as_list_lines(value) -> List[str]:
    """Subject DNs contain commas, so they cannot use the comma-split list
    parser: a JSON list from the DB, or one DN per line from the env."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            return [str(v).strip() for v in json.loads(text) if str(v).strip()]
        except ValueError:
            pass
    return [line.strip() for line in text.splitlines() if line.strip()]
