"""Structured logs, request ids, OpenTelemetry traces, error tracking (H4).

Three independent, all-optional layers, each switched by environment:

  LOG_FORMAT=json          one JSON object per line on stdout, with
                           request_id / trace_id / span_id when present.
                           `text` (default) keeps the human-readable format.
  OTEL_EXPORTER_OTLP_ENDPOINT
                           turns on tracing: FastAPI, SQLAlchemy, httpx, redis
                           and Celery spans, exported over OTLP/HTTP to any
                           collector (Jaeger, Tempo, Honeycomb, Datadog agent…).
                           All other OTEL_* knobs (service name, sampler,
                           headers, protocol) are read by the SDK itself.
  SENTRY_DSN               error tracking. PII off, secrets scrubbed, tracing
                           left to OpenTelemetry.

Why not just "use logging everywhere": 150 `print()` calls across 30 modules
predate any logging discipline, and converting them by hand is a large,
low-value diff that would touch every Celery task. In JSON mode stdout itself
is wrapped, so a legacy print becomes a proper JSON record (logger "stdout",
level INFO) and a log shipper never sees a bare line. New code uses `logging`.

Everything here degrades to a no-op when unconfigured; importing this module
never requires the optional packages to be importable (they are pinned in
requirements.txt, but a stripped image or a test venv without them must still
start).
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import os
import sys
import traceback
import uuid
from typing import Any, Optional

# --------------------------------------------------------------------------
# request-id propagation
# --------------------------------------------------------------------------

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "traceiq_request_id", default=None)

REQUEST_ID_HEADER = "x-request-id"
_MAX_REQUEST_ID = 128


def sanitize_request_id(raw: Optional[str]) -> Optional[str]:
    """Accept a caller's id only if it is printable, short, and has no
    whitespace — it ends up in log lines and response headers."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw or len(raw) > _MAX_REQUEST_ID:
        return None
    if any(c.isspace() or not c.isprintable() for c in raw):
        return None
    return raw


class RequestIdMiddleware:
    """Pure ASGI middleware: read or mint X-Request-ID, expose it to logs via a
    contextvar, echo it on the response. Pure ASGI (not BaseHTTPMiddleware) so
    WebSockets and streaming responses are untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        incoming = None
        for name, value in scope.get("headers") or []:
            if name == REQUEST_ID_HEADER.encode():
                incoming = sanitize_request_id(value.decode("latin-1"))
                break
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


# --------------------------------------------------------------------------
# JSON logging
# --------------------------------------------------------------------------

_STD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message", "asctime", "request_id", "trace_id", "span_id",
    "color_message"}  # uvicorn's ANSI duplicate of msg — noise in JSON


def _trace_context() -> tuple[Optional[str], Optional[str]]:
    try:
        from opentelemetry import trace
    except Exception:
        return None, None
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


class ContextFilter(logging.Filter):
    """Stamp request_id / trace_id / span_id on every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.trace_id, record.span_id = _trace_context()
        return True


class JsonFormatter(logging.Formatter):
    """One object per line. Keys are stable so a shipper can index them:
    ts, level, logger, msg, then request_id/trace_id/span_id when present,
    then any `extra=` fields, then `exc` for exceptions."""

    def __init__(self, service: str = ""):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
                  .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if self.service:
            out["service"] = self.service
        for key in ("request_id", "trace_id", "span_id"):
            value = getattr(record, key, None)
            if value:
                out[key] = value
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                out[key] = _jsonable(value)
        if record.exc_info:
            out["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        elif record.exc_text:
            out["exc"] = record.exc_text
        if record.stack_info:
            out["stack"] = record.stack_info
        return json.dumps(out, ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


class StdoutToLog:
    """File-like wrapper that turns each written line into a log record, so
    legacy print() calls become structured lines in JSON mode. Partial lines
    are buffered until their newline. Keeps a handle to the real stream so
    `flush`/`isatty`/`fileno` keep working for anything that asks."""

    def __init__(self, real, logger: logging.Logger):
        self._real = real
        self._logger = logger
        self._buf = ""

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            s = str(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.info(line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.info(self._buf.rstrip())
        self._buf = ""
        try:
            self._real.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._real.fileno()

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")

    def __getattr__(self, name):
        return getattr(self._real, name)


_configured = False
_real_stdout = None


def log_format() -> str:
    return (os.environ.get("LOG_FORMAT") or "text").strip().lower()


def configure_logging(service: str = "") -> str:
    """Install the root handler once per process. Returns the format in use.

    In json mode also (a) re-points uvicorn's and celery's own loggers at our
    handler so *every* line is JSON, and (b) wraps sys.stdout so legacy
    print() output is too."""
    global _configured, _real_stdout
    fmt = log_format()
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if _configured:
        logging.getLogger().setLevel(level)
        return fmt
    _configured = True

    _real_stdout = sys.stdout if _real_stdout is None else _real_stdout
    handler = logging.StreamHandler(_real_stdout)
    handler.addFilter(ContextFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service=service))
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers with propagate=False; celery does the
    # same via its signals (handled in celery_app.py). Fold uvicorn into ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # SQLAlchemy echo and third-party chatter stay at WARNING unless LOG_LEVEL
    # is DEBUG; the interesting lines are ours.
    if level > logging.DEBUG:
        for name in ("sqlalchemy.engine", "httpx", "httpcore", "botocore", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)

    if fmt == "json" and not isinstance(sys.stdout, StdoutToLog):
        sys.stdout = StdoutToLog(_real_stdout, logging.getLogger("stdout"))
    return fmt


def attach_json_formatter(logger: logging.Logger, service: str = "") -> None:
    """For frameworks that hand us their logger (Celery's after_setup_logger):
    make it emit through our root handler instead of its own."""
    if log_format() != "json":
        return
    logger.handlers[:] = []
    logger.propagate = True


# --------------------------------------------------------------------------
# OpenTelemetry
# --------------------------------------------------------------------------

_tracing_enabled: Optional[bool] = None


def tracing_enabled() -> bool:
    """On when an OTLP endpoint is configured (the standard variable), or when
    OTEL_ENABLED is set explicitly. OTEL_SDK_DISABLED=true wins."""
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    if os.environ.get("OTEL_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
                or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def configure_tracing(app=None, *, service_name: str = "traceiq-backend",
                      sqlalchemy_engine=None) -> bool:
    """Set up the tracer provider and instrument the libraries we use.
    Returns False (and does nothing) when tracing is not configured or the
    OpenTelemetry packages are missing."""
    global _tracing_enabled
    if not tracing_enabled():
        _tracing_enabled = False
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # pragma: no cover - packages are pinned
        logging.getLogger(__name__).warning("OpenTelemetry requested but unavailable: %s", exc)
        _tracing_enabled = False
        return False

    if _tracing_enabled is None:
        # OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES from the environment win
        # over our default, per the spec's precedence.
        resource = Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name)})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _instrument_libraries(sqlalchemy_engine)
        _tracing_enabled = True

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(
                app, excluded_urls=os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS",
                                                  "health,health/.*,metrics"))
        except Exception as exc:  # pragma: no cover
            logging.getLogger(__name__).warning("FastAPI instrumentation failed: %s", exc)
    return True


def _instrument_libraries(sqlalchemy_engine=None) -> None:
    log = logging.getLogger(__name__)
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        # One call does both: it patches create_engine for engines built later
        # AND instruments the one that already exists (the app's async engine,
        # via its sync core). Calling instrument() twice logs a warning.
        kwargs = {}
        if sqlalchemy_engine is not None:
            kwargs["engine"] = getattr(sqlalchemy_engine, "sync_engine", sqlalchemy_engine)
        SQLAlchemyInstrumentor().instrument(**kwargs)
    except Exception as exc:  # pragma: no cover
        log.warning("SQLAlchemy instrumentation failed: %s", exc)
    for modpath, cls in (("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
                         ("opentelemetry.instrumentation.redis", "RedisInstrumentor")):
        try:
            module = __import__(modpath, fromlist=[cls])
            getattr(module, cls)().instrument()
        except Exception as exc:  # pragma: no cover
            log.warning("%s instrumentation failed: %s", cls, exc)


def instrument_celery() -> None:
    """Called from Celery's worker_process_init: the instrumentor must run in
    the worker process, after the fork, or spans never leave the child."""
    if not configure_tracing(service_name=os.environ.get("OTEL_SERVICE_NAME", "traceiq-celery")):
        return
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).warning("Celery instrumentation failed: %s", exc)


# --------------------------------------------------------------------------
# error tracking (Sentry, optional)
# --------------------------------------------------------------------------

# Header / field names whose VALUES must never reach an error tracker. Sentry
# has its own denylist; these are TraceIQ's additions (API keys, the worker
# shared secret, SCIM bearer, cookies).
SENTRY_EXTRA_DENYLIST = (
    "x-api-key", "x-worker-secret", "x-traceiq-secret", "x-traceiq-signature",
    "authorization", "cookie", "set-cookie", "refresh_token", "access_token",
    "mfa_token", "client_secret", "private_key", "saml_sp_private_key",
    "smtp_password", "secret_key", "secrets_key", "webhook_secret",
)


def configure_error_tracking(*, service_name: str = "traceiq-backend") -> bool:
    """Initialise Sentry when SENTRY_DSN is set. PII off; request bodies off;
    our secret names added to the scrubber; tracing left to OpenTelemetry."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).warning("SENTRY_DSN set but sentry-sdk unavailable: %s", exc)
        return False
    from app.core.config import settings
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT") or settings.ENVIRONMENT,
        release=os.environ.get("SENTRY_RELEASE") or os.environ.get("TRACEIQ_VERSION") or None,
        server_name=service_name,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        event_scrubber=EventScrubber(denylist=list(DEFAULT_DENYLIST) + list(SENTRY_EXTRA_DENYLIST),
                                     recursive=True),
        before_send=_sentry_before_send,
    )
    return True


def _sentry_before_send(event: dict, hint: dict) -> Optional[dict]:
    # Tag with the request id so a Sentry issue can be matched to the JSON
    # log lines and the trace for the same request.
    rid = request_id_var.get()
    if rid:
        event.setdefault("tags", {})["request_id"] = rid
    trace_id, _ = _trace_context()
    if trace_id:
        event.setdefault("tags", {})["trace_id"] = trace_id
    # Cookies and the raw query string are the two things the scrubber's
    # key-based pass can miss (session ids live in values, not keys).
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("cookies", None)
        if "query_string" in req:
            req["query_string"] = "[Filtered]"
    return event
