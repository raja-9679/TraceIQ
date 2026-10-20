"""WP15b H3: the dispatcher stamps a W3C traceparent on worker jobs so the
Node worker continues the run's trace."""
import re

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app.core import telemetry

TP = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")  # flags: sampled, +random-id bit in OTel 1.4x


@pytest.fixture
def tracing_on(monkeypatch):
    monkeypatch.setattr(telemetry, "_tracing_enabled", True)
    return TracerProvider().get_tracer("test")


def test_no_traceparent_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(telemetry, "_tracing_enabled", False)
    job = {"job_id": "j"}
    assert telemetry.current_traceparent() is None
    assert telemetry.stamp_trace(job) == {"job_id": "j"}


def test_no_traceparent_without_an_active_span(tracing_on):
    assert telemetry.current_traceparent() is None


def test_stamp_carries_the_active_span(tracing_on):
    with tracing_on.start_as_current_span("run_test_suite") as span:
        job = telemetry.stamp_trace({"job_id": "j"})
    m = TP.match(job["traceparent"])
    assert m, job
    ctx = span.get_span_context()
    assert m.group(1) == format(ctx.trace_id, "032x")
    assert m.group(2) == format(ctx.span_id, "016x")
    assert set(job) == {"job_id", "traceparent"}   # no baggage, nothing else


def test_a_replayed_job_keeps_its_original_trace(tracing_on):
    original = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with tracing_on.start_as_current_span("replay"):
        job = telemetry.stamp_trace({"job_id": "j", "traceparent": original})
    assert job["traceparent"] == original


def test_enqueue_job_stamps_every_run_job(tracing_on):
    import json
    from app import worker

    class Pipe:
        def __init__(self):
            self.calls = []

        def xadd(self, stream, fields):
            self.calls.append(("xadd", stream, fields))

        def rpush(self, key, value):
            self.calls.append(("rpush", key, value))

        def expire(self, *a):
            pass

        def sadd(self, *a):
            pass

    pipe = Pipe()
    with tracing_on.start_as_current_span("dispatch") as span:
        worker._enqueue_job(pipe, "jobs:pending", None, 7, {"job_id": "a", "test_case": {}})
        worker._enqueue_job(pipe, "jobs:pending", "local:1:w", 7, {"job_id": "b", "test_case": {}})
    want = format(span.get_span_context().trace_id, "032x")
    payloads = [json.loads(c[2]["payload"]) if c[0] == "xadd" else json.loads(c[2]) for c in pipe.calls]
    assert [TP.match(p["traceparent"]).group(1) for p in payloads] == [want, want]
