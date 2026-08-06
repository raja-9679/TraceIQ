"""HTTP/Streamable-HTTP transport for the TraceIQ MCP server.

Same typed tools as stdio, served over a single endpoint any MCP client
(Claude Code, Cursor, Copilot, Windsurf …) can reach with a URL + API key:

  POST /mcp    — MCP Streamable-HTTP endpoint (FastMCP-managed, stateless)
  GET  /health — liveness probe (no auth)

Auth: every /mcp request must carry the caller's TraceIQ workspace API key,
either as X-API-Key or Authorization: Bearer <key>. The key is stashed in a
request-scoped ContextVar and forwarded to the TraceIQ backend on every tool
call — no shared service account, no key storage here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import uvicorn

from traceiq_mcp._context import _api_key_ctx
from traceiq_mcp.app import mcp
from traceiq_mcp.client import TraceIQClient
from traceiq_mcp.tools import register_all

logger = logging.getLogger(__name__)

register_all()

# Positive validation cache: a supplied key is checked against the backend once
# (a cheap authenticated GET /api/workspaces) and the result is cached so every
# subsequent tool call in the session doesn't re-validate. Presence alone is not
# authentication — the backend is the authority on whether a key is real.
_VALIDATION_TTL_S = 300.0
_validated_keys: dict[str, float] = {}
_validation_lock = asyncio.Lock()


async def _key_is_valid(api_key: str) -> bool:
    now = time.monotonic()
    expiry = _validated_keys.get(api_key)
    if expiry is not None and expiry > now:
        return True
    async with _validation_lock:
        expiry = _validated_keys.get(api_key)
        if expiry is not None and expiry > now:
            return True
        try:
            await TraceIQClient(api_key=api_key).list_workspaces()
        except Exception as exc:  # noqa: BLE001 — any failure = reject
            logger.info("Rejecting MCP request: API key failed validation (%s)",
                        type(exc).__name__)
            _validated_keys.pop(api_key, None)
            return False
        _validated_keys[api_key] = now + _VALIDATION_TTL_S
        return True

# FastMCP builds the Starlette app (lifespan + session manager included);
# `stateless_http=True` on the instance makes each POST independent.
_starlette_app = mcp.streamable_http_app()


async def _send_json(send: Any, status: int, body: bytes) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class ApiKeyMiddleware:
    """Pure-ASGI wrapper: /health short-circuits, /mcp requires an API key and
    runs the inner app inside the request-scoped ContextVar."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._inner(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await _send_json(send, 200, b'{"status":"ok","service":"traceiq-mcp"}')
            return

        # Only the MCP endpoint is served; positively match it rather than
        # treating "anything that isn't /health" as authenticated surface.
        if not (path == "/mcp" or path.startswith("/mcp/")):
            await _send_json(send, 404, b'{"detail":"Not found"}')
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        api_key = headers.get(b"x-api-key", b"").decode()
        if not api_key:
            auth = headers.get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer "):
                api_key = auth[7:].strip()
        if not api_key:
            await _send_json(
                send, 401,
                b'{"detail":"X-API-Key header or Authorization: Bearer <api-key> required"}')
            return

        # Validate the credential against the backend (cached per key) so a
        # syntactically-present-but-bogus key can't reach any tool.
        if not await _key_is_valid(api_key):
            await _send_json(
                send, 401,
                b'{"detail":"API key rejected by TraceIQ backend"}')
            return

        token = _api_key_ctx.set(api_key)
        try:
            await self._inner(scope, receive, send)
        finally:
            _api_key_ctx.reset(token)


http_app = ApiKeyMiddleware(_starlette_app)


def main_http() -> None:
    """Entrypoint registered as the `traceiq-mcp-http` console script."""
    base_url = os.environ.get("TRACEIQ_BASE_URL", "")
    if not base_url:
        raise SystemExit("TRACEIQ_BASE_URL must be set (e.g. http://backend:8000)")

    port = int(os.environ.get("MCP_HTTP_PORT", "8088"))
    log_level = os.environ.get("LOG_LEVEL", "info").lower()

    logger.info("TraceIQ MCP HTTP server starting on port %d → %s", port, base_url)
    uvicorn.run(
        "traceiq_mcp.server_http:http_app",
        host="0.0.0.0",
        port=port,
        log_level=log_level,
    )
