"""Per-request context variables for the HTTP transport mode."""
from __future__ import annotations

import contextvars

# Set by ApiKeyMiddleware in server_http.py before each tool call.
# Falls back to empty string so stdio mode (which never sets it) still works —
# TraceIQClient falls back to TRACEIQ_API_KEY env var when this is empty.
_api_key_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_api_key_ctx", default=""
)


def get_request_api_key() -> str:
    return _api_key_ctx.get()
