"""HTTP/Streamable-HTTP transport for the TraceIQ MCP server.

Exposes the same tools as the stdio server over a single endpoint that any
MCP client (Claude Code, Cursor, Copilot, Windsurf …) can reach with just
a URL and an X-API-Key header.

  POST /mcp    — MCP Streamable-HTTP endpoint
  GET  /health — liveness probe (no auth required)

Auth: every /mcp request must carry the caller's TraceIQ workspace API key
as X-API-Key.  The key is forwarded to the TraceIQ backend for every tool
call — no shared service account, no key storage here.
"""
from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import anyio
import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from traceiq_mcp._context import _api_key_ctx
from traceiq_mcp.server import server  # Server instance with all 28 tools registered

logger = logging.getLogger(__name__)

# ── Session manager (stateless — each POST is independent) ────────────────────

session_manager = StreamableHTTPSessionManager(
    app=server,
    json_response=False,
    stateless=True,
)

# ── Static response helpers ───────────────────────────────────────────────────

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


# ── Core ASGI app ─────────────────────────────────────────────────────────────

class TraceIQMCPApp:
    """Minimal ASGI app: handles lifespan, /health, and /mcp."""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
        elif scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/health":
                await _send_json(send, 200, b'{"status":"ok","service":"traceiq-mcp"}')
            elif path.rstrip("/") == "/mcp":
                await self._handle_mcp(scope, receive, send)
            else:
                await _send_json(send, 404, b'{"detail":"not found"}')
        else:
            pass  # ignore websocket / other scopes

    async def _handle_lifespan(self, receive: Any, send: Any) -> None:
        event = await receive()
        if event["type"] == "lifespan.startup":
            try:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._run_session_manager)
                    await send({"type": "lifespan.startup.complete"})
                    event2 = await receive()
                    if event2["type"] == "lifespan.shutdown":
                        tg.cancel_scope.cancel()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
        await send({"type": "lifespan.shutdown.complete"})

    async def _run_session_manager(self) -> None:
        async with session_manager.run():
            # Yield control back; session manager stays alive until task group cancelled
            await anyio.sleep_forever()

    async def _handle_mcp(self, scope: Any, receive: Any, send: Any) -> None:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        api_key = headers.get(b"x-api-key", b"").decode()
        if not api_key:
            await _send_json(send, 401, b'{"detail":"X-API-Key header required"}')
            return

        token = _api_key_ctx.set(api_key)
        try:
            await session_manager.handle_request(scope, receive, send)
        finally:
            _api_key_ctx.reset(token)


http_app = TraceIQMCPApp()


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main_http() -> None:
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
