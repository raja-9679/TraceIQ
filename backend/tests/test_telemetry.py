"""H4: JSON logging, request-id propagation, print() capture, and the
opt-in switches for tracing and error tracking. No network, no collector."""
import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import telemetry
from app.core.telemetry import (ContextFilter, JsonFormatter, RequestIdMiddleware, StdoutToLog,
                                request_id_var, sanitize_request_id)


def _record(msg="hello", level=logging.INFO, **extra):
    rec = logging.LogRecord("traceiq.test", level, "f.py", 1, msg, (), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    ContextFilter().filter(rec)
    return rec


def test_json_formatter_emits_one_stable_object():
    line = JsonFormatter(service="svc").format(_record("hi there"))
    obj = json.loads(line)
    assert obj["level"] == "INFO" and obj["logger"] == "traceiq.test"
    assert obj["msg"] == "hi there" and obj["service"] == "svc"
    assert obj["ts"].endswith("Z")
    assert "request_id" not in obj  # none in this context


def test_json_formatter_includes_extra_fields_and_request_id():
    token = request_id_var.set("req-123")
    try:
        obj = json.loads(JsonFormatter().format(_record("x", run_id=42, weird=object())))
    finally:
        request_id_var.reset(token)
    assert obj["request_id"] == "req-123"
    assert obj["run_id"] == 42
    assert isinstance(obj["weird"], str)  # non-JSON values are stringified, not dropped


def test_json_formatter_renders_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = _record("failed")
        rec.exc_info = sys.exc_info()
    obj = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in obj["exc"]


def test_json_output_is_single_line_even_for_multiline_messages():
    line = JsonFormatter().format(_record("a\nb\nc"))
    assert "\n" not in line and json.loads(line)["msg"] == "a\nb\nc"


def test_stdout_wrapper_turns_prints_into_records(caplog):
    logger = logging.getLogger("stdout.test")
    logger.propagate = True
    wrapper = StdoutToLog(io.StringIO(), logger)
    with caplog.at_level(logging.INFO, logger="stdout.test"):
        wrapper.write("partial ")
        wrapper.write("line\nsecond line\n")
        wrapper.write("tail without newline")
        wrapper.flush()
    assert [r.getMessage() for r in caplog.records] == ["partial line", "second line", "tail without newline"]
    assert wrapper.isatty() is False


def test_sanitize_request_id_rejects_junk():
    assert sanitize_request_id("abc-123") == "abc-123"
    assert sanitize_request_id("  padded  ") == "padded"
    assert sanitize_request_id("has space") is None
    assert sanitize_request_id("x" * 129) is None
    assert sanitize_request_id("new\nline") is None
    assert sanitize_request_id("") is None and sanitize_request_id(None) is None


def _app():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    def echo():
        return {"request_id": request_id_var.get()}

    return app


def test_middleware_mints_an_id_and_echoes_it():
    client = TestClient(_app())
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.headers["x-request-id"] == r.json()["request_id"]
    assert len(r.headers["x-request-id"]) == 32


def test_middleware_honours_a_clean_incoming_id_and_replaces_a_dirty_one():
    client = TestClient(_app())
    r = client.get("/echo", headers={"X-Request-ID": "trace-from-lb-7"})
    assert r.headers["x-request-id"] == "trace-from-lb-7" == r.json()["request_id"]
    r2 = client.get("/echo", headers={"X-Request-ID": "bad id with spaces"})
    assert r2.headers["x-request-id"] != "bad id with spaces"


def test_request_id_does_not_leak_between_requests():
    assert request_id_var.get() is None
    TestClient(_app()).get("/echo", headers={"X-Request-ID": "one"})
    assert request_id_var.get() is None


def test_tracing_is_off_without_an_endpoint(monkeypatch):
    for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(telemetry, "_tracing_enabled", None)
    assert telemetry.tracing_enabled() is False
    assert telemetry.configure_tracing(FastAPI()) is False


def test_tracing_switches(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    assert telemetry.tracing_enabled() is True
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert telemetry.tracing_enabled() is False  # the standard kill switch wins


def test_error_tracking_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert telemetry.configure_error_tracking() is False


def test_sentry_denylist_covers_traceiq_secrets():
    names = set(telemetry.SENTRY_EXTRA_DENYLIST)
    for must in ("x-api-key", "x-worker-secret", "x-traceiq-secret", "authorization",
                 "cookie", "refresh_token", "saml_sp_private_key"):
        assert must in names


def test_before_send_strips_cookies_and_query_and_tags_request_id():
    token = request_id_var.set("rid-9")
    try:
        event = telemetry._sentry_before_send(
            {"request": {"cookies": {"session": "x"}, "query_string": "token=abc", "url": "/x"}}, {})
    finally:
        request_id_var.reset(token)
    assert "cookies" not in event["request"]
    assert event["request"]["query_string"] == "[Filtered]"
    assert event["tags"]["request_id"] == "rid-9"


def test_configure_logging_text_mode_leaves_stdout_alone(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "text")
    monkeypatch.setattr(telemetry, "_configured", False)
    import sys
    before = sys.stdout
    assert telemetry.configure_logging() == "text"
    assert sys.stdout is before
    # root handler carries the context filter either way
    root = logging.getLogger()
    assert any(isinstance(f, ContextFilter) for h in root.handlers for f in h.filters)
