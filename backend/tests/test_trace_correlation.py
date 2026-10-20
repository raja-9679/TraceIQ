"""Trace correlation (WP16a): Jaeger/Tempo parsing, step mapping, attribute
redaction, the dispatch host set, evidence ingestion and `sN.trace` citations.

Everything against Jaeger/Tempo is mock-verified (httpx.MockTransport)."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import failure_analysis, trace_backends, trace_correlation
from app.services.failure_investigation import build_evidence_index
from app.services.redaction import RedactionPolicy
from app.services.trace_backends import (
    BackendConfig, Span, TraceBackendError, build_result_traces, descendants_of,
    error_spans_by_step, parse_jaeger, parse_tempo,
)
from app.tasks.result_aggregator import normalise_step_evidence

TID = "4bf92f3577b34da6a3ce929d0e0e4736"
STEP0 = "00f067aa0ba902b7"
STEP1 = "1111111111111111"


def _jaeger_body():
    return {"data": [{
        "traceID": TID,
        "processes": {"p1": {"serviceName": "checkout-api"}, "p2": {"serviceName": "postgres"}},
        "spans": [
            {"traceID": TID, "spanID": "a000000000000001", "operationName": "POST /cart",
             "references": [{"refType": "CHILD_OF", "traceID": TID, "spanID": STEP0}],
             "startTime": 1_000_000, "duration": 250_000, "processID": "p1",
             "tags": [{"key": "http.method", "value": "POST"},
                      {"key": "http.status_code", "value": 500},
                      {"key": "http.url", "value": "https://shop.test/cart?token=abc"},
                      {"key": "error", "value": True},
                      {"key": "otel.status_description", "value": "card 4111111111111111 declined"}]},
            {"traceID": TID, "spanID": "a000000000000002", "operationName": "INSERT cart",
             "references": [{"refType": "CHILD_OF", "spanID": "a000000000000001"}],
             "startTime": 1_050_000, "duration": 20_000, "processID": "p2",
             "tags": [{"key": "db.system", "value": "postgresql"},
                      {"key": "db.statement", "value": "INSERT INTO cart VALUES ('secret')"}]},
            # Under another step's span; must not be attributed to step 0.
            {"traceID": TID, "spanID": "b000000000000001", "operationName": "GET /",
             "references": [{"refType": "CHILD_OF", "spanID": STEP1}],
             "startTime": 3_000_000, "duration": 5_000, "processID": "p1", "tags": []},
            # No parent link to any step: never guessed by time.
            {"traceID": TID, "spanID": "c000000000000001", "operationName": "cron",
             "references": [], "startTime": 1_000_100, "duration": 5, "processID": "p1", "tags": []},
        ],
    }]}


def _b64(hexid):
    return base64.b64encode(bytes.fromhex(hexid)).decode()


def _tempo_body():
    return {"batches": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout-api"}}]},
        "scopeSpans": [{"spans": [
            {"traceId": _b64(TID), "spanId": _b64("a000000000000001"), "parentSpanId": _b64(STEP0),
             "name": "POST /cart", "kind": 2, "startTimeUnixNano": "1000000000",
             "endTimeUnixNano": "1250000000", "status": {"code": 2, "message": "boom"},
             "attributes": [{"key": "http.route", "value": {"stringValue": "/cart"}},
                            {"key": "url.full", "value": {"stringValue": "https://x/?t=1"}}]},
            {"traceId": TID, "spanId": "a000000000000002", "parentSpanId": "a000000000000001",
             "name": "SELECT", "kind": "SPAN_KIND_CLIENT", "startTimeUnixNano": 1_010_000_000,
             "endTimeUnixNano": 1_020_000_000, "status": {"code": "STATUS_CODE_OK"}, "attributes": []},
        ]}],
    }]}


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_parse_jaeger_allow_lists_and_redacts_attributes():
    spans = {s.span_id: s for s in parse_jaeger(_jaeger_body())}
    api = spans["a000000000000001"]
    assert api.service == "checkout-api" and api.operation == "POST /cart"
    assert api.parent_span_id == STEP0 and api.error is True
    assert api.duration_us == 250_000
    assert api.attributes == {"http.method": "POST", "http.status_code": "500"}  # no http.url
    assert "4111111111111111" not in api.status_message                           # PAN redacted
    db = spans["a000000000000002"]
    assert db.attributes == {"db.system": "postgresql"}                           # no statement
    assert db.error is False and db.status_message is None


def test_parse_tempo_handles_base64_ids_kinds_and_v2_wrapper():
    for body in (_tempo_body(), {"trace": {"resourceSpans": _tempo_body()["batches"]}}):
        spans = parse_tempo(body)
        assert [s.span_id for s in spans] == ["a000000000000001", "a000000000000002"]
        top = spans[0]
        assert top.parent_span_id == STEP0 and top.error and top.status_message == "boom"
        assert top.duration_us == 250_000 and top.service == "checkout-api"
        assert top.attributes == {"http.route": "/cart", "span.kind": "server"}
        assert spans[1].attributes == {"span.kind": "client"} and not spans[1].error


def test_attribute_values_use_the_project_policy():
    body = _jaeger_body()
    body["data"][0]["spans"][0]["tags"].append({"key": "http.route", "value": "/u/jane@example.com"})
    default = {s.span_id: s for s in parse_jaeger(body)}["a000000000000001"]
    assert "jane@example.com" in default.attributes["http.route"]       # email off by default
    strict = {s.span_id: s for s in parse_jaeger(body, RedactionPolicy(patterns=("email",)))}
    assert "jane@example.com" not in strict["a000000000000001"].attributes["http.route"]


def test_parsers_refuse_non_objects_and_cap_spans(monkeypatch):
    with pytest.raises(TraceBackendError):
        parse_jaeger([])
    with pytest.raises(TraceBackendError):
        parse_tempo("x")
    monkeypatch.setattr(trace_backends, "MAX_SPANS_PER_TRACE", 2)
    assert len(parse_jaeger(_jaeger_body())) == 2


def test_descendants_follow_parent_links_only():
    spans = parse_jaeger(_jaeger_body())
    assert [s.span_id for s in descendants_of(spans, STEP0)] == ["a000000000000001", "a000000000000002"]
    assert [s.span_id for s in descendants_of(spans, STEP1)] == ["b000000000000001"]
    assert descendants_of(spans, "2222222222222222") == []
    # A cycle in hostile data cannot loop forever (and a span claiming the
    # step's own id is not one of its descendants).
    loop = [Span("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "s", "o", 0, 0, False, None, {}),
            Span("bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa", "s", "o", 0, 0, False, None, {})]
    assert [s.span_id for s in descendants_of(loop, "aaaaaaaaaaaaaaaa")] == ["bbbbbbbbbbbbbbbb"]


# ── Result payload ──────────────────────────────────────────────────────────

def _evidence(requests0=2, requests2=1):
    return [
        {"id": "s0", "index": 0, "type": "click", "status": "failed",
         "trace": {"trace_id": TID, "span_id": STEP0, "requests": requests0}},
        {"id": "s1", "index": 1, "type": "goto", "status": "passed",
         "trace": {"trace_id": TID, "span_id": STEP1, "requests": 1}},
        {"id": "s2", "index": 2, "type": "fill", "status": "passed",
         "trace": {"trace_id": TID, "span_id": "3333333333333333", "requests": requests2}},
        {"id": "s3", "index": 3, "type": "expect", "status": "skipped"},
    ]


CFG = BackendConfig(kind="jaeger", base_url="http://10.1.2.3:16686")


def test_result_traces_per_step_with_reasons():
    calls = []

    def fake(cfg, tid, policy):
        calls.append(tid)
        return parse_jaeger(_jaeger_body(), policy)

    out = build_result_traces(_evidence(requests2=0), cfg=CFG, fetch=fake)
    assert calls == [TID]                                   # one fetch per trace id
    by = {s["step"]: s for s in out["steps"]}
    assert set(by) == {"s0", "s1", "s2"}                    # s3 has no trace id
    assert by["s0"]["status"] == "ok" and by["s0"]["error_spans"] == 1
    assert [s["operation"] for s in by["s0"]["spans"]] == ["POST /cart", "INSERT cart"]
    assert by["s0"]["spans"][1]["offset_ms"] == 50.0
    assert by["s1"]["status"] == "ok" and len(by["s1"]["spans"]) == 1
    assert by["s2"]["status"] == "no_telemetry" and by["s2"]["reason"] == trace_backends.NO_REQUESTS
    errs = error_spans_by_step(out)
    assert list(errs) == [0] and errs[0]["first"]["service"] == "checkout-api"


def test_result_traces_never_infers():
    out = build_result_traces(_evidence(), cfg=CFG, fetch=lambda c, t, p: None)
    assert all(s["status"] == "no_telemetry" and s["reason"] == trace_backends.TRACE_MISSING
               for s in out["steps"])
    only_other = lambda c, t, p: parse_jaeger(_jaeger_body(), p)[2:]   # step-1 span + orphan
    by = {s["step"]: s for s in build_result_traces(_evidence(), cfg=CFG, fetch=only_other)["steps"]}
    assert by["s0"]["reason"] == trace_backends.NO_DESCENDANTS


def test_result_traces_top_level_states():
    assert build_result_traces([], cfg=CFG)["reason"] == trace_backends.NO_IDS
    assert build_result_traces(_evidence(), cfg=None)["reason"] == trace_backends.NOT_CONFIGURED
    local = build_result_traces(_evidence(), cfg=CFG, is_local_run=True,
                                fetch=lambda *a: pytest.fail("local runs are never looked up"))
    assert local["reason"] == trace_backends.LOCAL_RUN

    def broken(c, t, p):
        raise TraceBackendError("trace backend unreachable: ConnectError")
    out = build_result_traces(_evidence(), cfg=CFG, fetch=broken)
    assert out["error"].startswith("trace backend unreachable")
    assert all(s["status"] == "no_telemetry" for s in out["steps"])


def test_spans_per_step_are_capped(monkeypatch):
    monkeypatch.setattr(trace_backends, "MAX_SPANS_PER_STEP", 1)
    out = build_result_traces(_evidence(), cfg=CFG, fetch=lambda c, t, p: parse_jaeger(_jaeger_body(), p))
    s0 = out["steps"][0]
    assert len(s0["spans"]) == 1 and s0["truncated"] is True


def test_invalid_ids_in_evidence_are_ignored():
    ev = [{"index": 0, "trace": {"trace_id": "../../admin", "span_id": STEP0}},
          {"index": 1, "trace": {"trace_id": "0" * 32, "span_id": STEP0}},
          {"index": 2, "trace": {"trace_id": TID.upper(), "span_id": STEP0}}]
    assert trace_backends.step_trace_refs(ev) == []


# ── HTTP (mock-verified) ────────────────────────────────────────────────────

@pytest.fixture
def transport(monkeypatch):
    seen = []

    def install(handler):
        def wrapped(request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(trace_backends, "_TRANSPORT", httpx.MockTransport(wrapped))
        return seen
    yield install
    monkeypatch.setattr(trace_backends, "_TRANSPORT", None)


def test_fetch_jaeger_and_tempo_over_http(transport, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: None)
    seen = transport(lambda r: httpx.Response(
        200, json=_jaeger_body() if "16686" in str(r.url) else _tempo_body()))
    spans = trace_backends.fetch_trace(BackendConfig("jaeger", "http://10.1.2.3:16686/", token="t0k"), TID)
    assert len(spans) == 4
    assert str(seen[0].url) == f"http://10.1.2.3:16686/api/traces/{TID}"
    assert seen[0].headers["authorization"] == "Bearer t0k"
    spans = trace_backends.fetch_trace(
        BackendConfig("tempo", "http://10.1.2.4:3200", token="Basic dXNlcjp0b2s=", tenant="acme"), TID)
    assert len(spans) == 2
    assert seen[1].headers["authorization"] == "Basic dXNlcjp0b2s="
    assert seen[1].headers["x-scope-orgid"] == "acme"


def test_fetch_404_redirect_size_and_metadata(transport, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: None)
    transport(lambda r: httpx.Response(404, json={"errors": [{"code": 404}]}))
    assert trace_backends.fetch_trace(CFG, TID) is None
    transport(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(TraceBackendError, match="HTTP 302"):
        trace_backends.fetch_trace(CFG, TID)
    monkeypatch.setattr(trace_backends, "MAX_RESPONSE_BYTES", 10)
    transport(lambda r: httpx.Response(200, content=json.dumps(_jaeger_body()).encode()))
    with pytest.raises(TraceBackendError, match="cap"):
        trace_backends.fetch_trace(CFG, TID)
    # The configured host is a PRIVATE-network exemption only, never metadata.
    with pytest.raises(TraceBackendError, match="refused"):
        trace_backends.fetch_trace(BackendConfig("jaeger", "http://169.254.169.254"), TID)


def test_configured_and_auth_header(monkeypatch):
    from app.services import instance_settings
    values = {"TRACE_BACKEND": "Tempo", "TRACE_BACKEND_URL": "http://tempo:3200"}
    monkeypatch.setattr(instance_settings, "effective", lambda k: values.get(k))
    cfg = trace_backends.configured()
    assert cfg.kind == "tempo" and cfg.token is None
    values["TRACE_BACKEND"] = "zipkin"
    assert trace_backends.configured() is None
    assert trace_backends.auth_header(None) == {}
    assert trace_backends.auth_header("Bearer x") == {"Authorization": "Bearer x"}


def test_cache_is_per_result_and_expires(monkeypatch):
    trace_backends.cache_clear()
    trace_backends.cache_put(1, {"a": 1})
    assert trace_backends.cache_get(1) == {"a": 1} and trace_backends.cache_get(2) is None
    monkeypatch.setattr(trace_backends.time, "monotonic", lambda: 10 ** 9)
    assert trace_backends.cache_get(1) is None


# ── Dispatch host set ───────────────────────────────────────────────────────

class _Session:
    def __init__(self, envs, suites):
        self._answers = [envs, suites]

    def exec(self, _stmt):
        return SimpleNamespace(all=lambda: self._answers.pop(0))


def _project(trace=None, sec=None):
    return SimpleNamespace(id=7, trace_settings=trace, security_settings=sec)


def test_for_dispatch_is_off_by_default_and_at_capture_none():
    assert trace_correlation.for_dispatch(_project(), _Session([], []), "standard") is None
    on = _project({"enabled": True})
    assert trace_correlation.for_dispatch(on, _Session(["https://app.test"], []), "none") is None
    assert trace_correlation.for_dispatch(None, None, "standard") is None
    assert trace_correlation.for_dispatch(_project({"enabled": "yes please"}), None, "standard") is None


def test_for_dispatch_host_set_is_the_projects_own_hosts():
    project = _project({"enabled": True, "cross_origin": True},
                       {"enabled": False, "allowed_domains": ["*.api.test"]})
    session = _Session(["https://App.test:8443/base", None],
                       [{"allowed_domains": ["cdn.app.test", {"domain": "auth.app.test"}]}, None])
    out = trace_correlation.for_dispatch(project, session, "minimal")
    assert out == {"enabled": True, "cross_origin": True,
                   "hosts": ["*.api.test", "app.test", "auth.app.test", "cdn.app.test"]}


def test_for_dispatch_with_no_hosts_injects_nothing():
    assert trace_correlation.for_dispatch(_project({"enabled": True}), _Session([], []), "full") is None


def test_settings_payload_carries_trace_correlation():
    from app.worker import _settings_payload
    cfg = {"enabled": True, "hosts": ["app.test"], "cross_origin": False}
    assert _settings_payload({"trace_correlation": cfg})["trace_correlation"] == cfg
    assert "trace_correlation" not in _settings_payload({})


# ── Ingestion + failure investigation ───────────────────────────────────────

def test_ingestion_keeps_only_valid_trace_ids():
    out = normalise_step_evidence([
        {"index": 0, "type": "goto", "trace": {"trace_id": TID, "span_id": STEP0, "requests": 3, "x": 1}},
        {"index": 1, "type": "goto", "trace": {"trace_id": "nothex" * 6, "span_id": STEP0}},
        {"index": 2, "type": "goto", "trace": "junk"},
        {"index": 3, "type": "goto", "trace": {"trace_id": TID, "span_id": STEP1, "requests": True}},
    ])
    assert out[0]["trace"] == {"trace_id": TID, "span_id": STEP0, "requests": 3}
    assert "trace" not in out[1] and "trace" not in out[2]
    assert out[3]["trace"] == {"trace_id": TID, "span_id": STEP1}


def _traces_for_step0():
    payload = build_result_traces(_evidence(), cfg=CFG, fetch=lambda c, t, p: parse_jaeger(_jaeger_body(), p))
    return error_spans_by_step(payload)


def test_trace_evidence_item_only_for_steps_with_error_spans():
    idx = build_evidence_index(step_evidence=_evidence(), error_message="boom",
                               step_traces=_traces_for_step0())
    assert "s0.trace" in idx.items and idx.items["s0.trace"].kind == "trace"
    assert "s1.trace" not in idx.items
    assert "checkout-api POST /cart" in idx.items["s0.trace"].label
    # A trace summary for a step the evidence does not list adds nothing.
    idx = build_evidence_index(step_evidence=_evidence(), error_message="",
                               step_traces={9: {"count": 1, "first": {}}})
    assert not any(i.endswith(".trace") for i in idx.items)


def test_error_spans_drive_an_app_bug_hypothesis(monkeypatch):
    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: SimpleNamespace(name="null"))
    r = failure_analysis.analyze_case_failure(
        test_name="buy", error_message="Timeout 30000ms exceeded waiting for locator('#qty')",
        steps=[{"type": "click"}], step_evidence=_evidence(), step_traces=_traces_for_step0())
    top = r.hypotheses[0]
    assert top.category == "app_bug" and "s0.trace" in top.evidence_refs
    assert any("s0.trace" in o.evidence_refs for o in r.observed)
    assert "s0.trace" in {i.id for i in r.evidence_items}


class _FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, prompt, system=None, max_tokens=None):
        return self.reply


def test_llm_trace_citations_survive_only_when_the_item_exists(monkeypatch):
    reply = json.dumps({"root_cause_category": "app_bug", "summary": "s", "details": "d",
                        "fix_target": "app", "confidence": 0.8, "hypotheses": [
                            {"category": "app_bug", "fix_target": "app", "summary": "API failed",
                             "confidence": 0.9, "evidence_refs": ["s0.trace", "s1.trace"]}]})
    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: _FakeLLM(reply))
    r = failure_analysis.analyze_case_failure(
        test_name="buy", error_message="x", steps=[{"type": "click"}],
        step_evidence=_evidence(), step_traces=_traces_for_step0())
    llm = next(h for h in r.hypotheses if h.summary == "API failed")
    assert llm.evidence_refs == ["s0.trace"] and r.dropped_citations == 1
    # Without traces, the same citation is dropped too.
    r = failure_analysis.analyze_case_failure(
        test_name="buy", error_message="x", steps=[{"type": "click"}], step_evidence=_evidence())
    assert r.dropped_citations == 2
