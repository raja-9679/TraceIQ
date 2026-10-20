"""Backend spans for a result's steps, read from Jaeger or Grafana Tempo (WP16a).

Workers record, per step, the trace id of the case attempt and the span id
they put in the `traceparent` of that step's requests to the project's own
hosts (`step_evidence[].trace`, see app/services/trace_correlation.py). Here
the trace is fetched from the instance's trace backend and each step gets the
spans that descend from ITS span id — the application's server spans name it
as their parent. Nothing is inferred: a step whose span has no descendants in
the trace is reported as `no_telemetry` with the reason, never matched up by
time or name.

Backends (instance settings group `observability`):
  TRACE_BACKEND        jaeger | tempo
  TRACE_BACKEND_URL    base URL the BACKEND can reach (Jaeger query / Tempo)
  TRACE_BACKEND_TOKEN  optional; sent as `Authorization: Bearer <token>`, or
                       verbatim when it already starts with `Bearer `/`Basic `
                       (Grafana Cloud Tempo uses basic auth)
  TRACE_BACKEND_TENANT optional Tempo `X-Scope-OrgID`

Fetched like PROMETHEUS_URL: through net_guard with the configured host as
its own private-network exemption (an in-cluster Jaeger is the normal case;
metadata/link-local stay refused), redirects not followed, a timeout and a
response-size cap. A trace backend has no tenancy of its own, which is why
only trace ids taken from the result's own stored evidence are ever queried —
128-bit random ids the worker generated — and why local-worker results (whose
evidence any API-key holder can write) are never looked up.

Span attributes are an allow-list (method, route, status code, rpc/db/
messaging system and operation, error type) and every value is redacted with
the project's policy: URLs, statements and messages carry data.

Everything against a real Jaeger/Tempo is mock-verified only
(`_TRANSPORT` takes an httpx.MockTransport in tests).
"""
from __future__ import annotations

import base64
import binascii
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlsplit

from app.services.redaction import DEFAULT_POLICY, RedactionPolicy, redact_text

TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_SPANS_PER_TRACE = 1000      # parsed from one trace
MAX_SPANS_PER_STEP = 50         # returned for one step
MAX_TRACES_PER_RESULT = 5       # distinct trace ids looked up for one result
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 256

BACKENDS = ("jaeger", "tempo")

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# OpenTelemetry semantic-convention keys (old and new names) that describe
# WHAT ran without carrying payload. `http.url`, `http.target`, `url.full`,
# `db.statement`, `db.query.text` and friends are deliberately absent.
ALLOWED_ATTRIBUTES = (
    "http.method", "http.request.method", "http.status_code", "http.response.status_code",
    "http.route", "rpc.system", "rpc.service", "rpc.method", "rpc.grpc.status_code",
    "db.system", "db.operation", "db.operation.name", "db.name", "db.namespace",
    "messaging.system", "messaging.operation", "error.type", "exception.type",
    "span.kind", "otel.status_code",
)
MAX_ATTRIBUTE_CHARS = 200
MAX_STATUS_MESSAGE_CHARS = 300


class TraceBackendError(RuntimeError):
    """The backend could not be queried (unreachable, refused, bad answer)."""


@dataclass
class Span:
    span_id: str
    parent_span_id: Optional[str]
    service: str
    operation: str
    start_us: int
    duration_us: int
    error: bool
    status_message: Optional[str]
    attributes: Dict[str, str]


def valid_trace_id(value: Any) -> bool:
    return isinstance(value, str) and bool(TRACE_ID_RE.match(value)) and value != "0" * 32


def valid_span_id(value: Any) -> bool:
    return isinstance(value, str) and bool(SPAN_ID_RE.match(value)) and value != "0" * 16


def _hex_id(value: Any, length: int) -> Optional[str]:
    """Jaeger ids are hex (sometimes without leading zeros); OTLP JSON from
    Tempo encodes bytes fields as base64. Normalised to lowercase hex."""
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]+", v) and len(v) <= length:
        return v.lower().rjust(length, "0")
    try:
        raw = base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) * 2 != length:
        return None
    return raw.hex()


def _clip(value: Any, limit: int, policy: RedactionPolicy) -> str:
    return (redact_text(str(value), policy) or "")[:limit]


def _attrs(pairs: Dict[str, Any], policy: RedactionPolicy) -> Dict[str, str]:
    return {k: _clip(pairs[k], MAX_ATTRIBUTE_CHARS, policy)
            for k in ALLOWED_ATTRIBUTES if k in pairs and pairs[k] not in (None, "")}


# ── Parsers ─────────────────────────────────────────────────────────────────

def parse_jaeger(body: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> List[Span]:
    """Jaeger query API `GET /api/traces/{id}` → spans.

    {"data": [{"traceID", "spans": [{spanID, operationName, references:
    [{refType: CHILD_OF, spanID}], startTime (µs), duration (µs), tags:
    [{key, value}], processID}], "processes": {pid: {serviceName}}}]}"""
    if not isinstance(body, dict):
        raise TraceBackendError("unexpected Jaeger response")
    out: List[Span] = []
    for trace in body.get("data") or []:
        if not isinstance(trace, dict):
            continue
        processes = trace.get("processes") if isinstance(trace.get("processes"), dict) else {}
        for s in trace.get("spans") or []:
            if len(out) >= MAX_SPANS_PER_TRACE:
                return out
            if not isinstance(s, dict):
                continue
            sid = _hex_id(s.get("spanID"), 16)
            if not sid:
                continue
            parent = None
            for ref in s.get("references") or []:
                if isinstance(ref, dict) and ref.get("refType", "CHILD_OF") == "CHILD_OF":
                    parent = _hex_id(ref.get("spanID"), 16)
                    break
            tags = {t.get("key"): t.get("value") for t in s.get("tags") or []
                    if isinstance(t, dict) and isinstance(t.get("key"), str)}
            proc = processes.get(s.get("processID")) or {}
            err = tags.get("error") in (True, "true") or str(tags.get("otel.status_code", "")).upper() == "ERROR"
            msg = tags.get("otel.status_description") or tags.get("error.message")
            out.append(Span(
                span_id=sid, parent_span_id=parent,
                service=_clip(proc.get("serviceName") or "unknown", 120, policy),
                operation=_clip(s.get("operationName") or "", 200, policy),
                start_us=_int(s.get("startTime")), duration_us=_int(s.get("duration")),
                error=err,
                status_message=_clip(msg, MAX_STATUS_MESSAGE_CHARS, policy) if (err and msg) else None,
                attributes=_attrs(tags, policy)))
    return out


def _otlp_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    return None


def _otlp_attrs(items: Any) -> Dict[str, Any]:
    return {a.get("key"): _otlp_value(a.get("value")) for a in items or []
            if isinstance(a, dict) and isinstance(a.get("key"), str)}


_OTLP_KINDS = {1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}


def parse_tempo(body: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> List[Span]:
    """Tempo `GET /api/traces/{id}` (OTLP JSON: `batches` or `resourceSpans`,
    v2 wraps it in `trace`) → spans. Status code 2 / STATUS_CODE_ERROR = error."""
    if not isinstance(body, dict):
        raise TraceBackendError("unexpected Tempo response")
    if isinstance(body.get("trace"), dict):
        body = body["trace"]
    batches = body.get("batches") or body.get("resourceSpans") or []
    out: List[Span] = []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        resource = _otlp_attrs((batch.get("resource") or {}).get("attributes"))
        service = resource.get("service.name") or "unknown"
        scopes = batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []
        for scope in scopes:
            for s in (scope or {}).get("spans") or []:
                if len(out) >= MAX_SPANS_PER_TRACE:
                    return out
                if not isinstance(s, dict):
                    continue
                sid = _hex_id(s.get("spanId"), 16)
                if not sid:
                    continue
                attrs = _otlp_attrs(s.get("attributes"))
                kind = s.get("kind")
                if isinstance(kind, int) and kind in _OTLP_KINDS:
                    attrs.setdefault("span.kind", _OTLP_KINDS[kind])
                elif isinstance(kind, str) and kind.startswith("SPAN_KIND_"):
                    attrs.setdefault("span.kind", kind[len("SPAN_KIND_"):].lower())
                status = s.get("status") if isinstance(s.get("status"), dict) else {}
                code = status.get("code")
                err = code in (2, "2", "STATUS_CODE_ERROR")
                start_ns, end_ns = _int(s.get("startTimeUnixNano")), _int(s.get("endTimeUnixNano"))
                msg = status.get("message")
                out.append(Span(
                    span_id=sid, parent_span_id=_hex_id(s.get("parentSpanId"), 16),
                    service=_clip(service, 120, policy),
                    operation=_clip(s.get("name") or "", 200, policy),
                    start_us=start_ns // 1000, duration_us=max(0, end_ns - start_ns) // 1000,
                    error=err,
                    status_message=_clip(msg, MAX_STATUS_MESSAGE_CHARS, policy) if (err and msg) else None,
                    attributes=_attrs(attrs, policy)))
    return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── Backends ────────────────────────────────────────────────────────────────

class TraceBackend(Protocol):
    name: str

    def trace_url(self, base: str, trace_id: str) -> str: ...

    def parse(self, body: Any, policy: RedactionPolicy) -> List[Span]: ...


class JaegerBackend:
    name = "jaeger"

    def trace_url(self, base: str, trace_id: str) -> str:
        return f"{base.rstrip('/')}/api/traces/{trace_id}"

    def parse(self, body: Any, policy: RedactionPolicy) -> List[Span]:
        return parse_jaeger(body, policy)


class TempoBackend:
    name = "tempo"

    def trace_url(self, base: str, trace_id: str) -> str:
        return f"{base.rstrip('/')}/api/traces/{trace_id}"

    def parse(self, body: Any, policy: RedactionPolicy) -> List[Span]:
        return parse_tempo(body, policy)


_BACKENDS: Dict[str, TraceBackend] = {"jaeger": JaegerBackend(), "tempo": TempoBackend()}


@dataclass
class BackendConfig:
    kind: str
    base_url: str
    token: Optional[str] = None
    tenant: Optional[str] = None


def configured() -> Optional[BackendConfig]:
    from app.services.instance_settings import effective
    kind = str(effective("TRACE_BACKEND") or "").strip().lower()
    base = str(effective("TRACE_BACKEND_URL") or "").strip()
    if kind not in _BACKENDS or not base:
        return None
    return BackendConfig(kind=kind, base_url=base,
                         token=(effective("TRACE_BACKEND_TOKEN") or None),
                         tenant=(str(effective("TRACE_BACKEND_TENANT") or "").strip() or None))


def auth_header(token: Optional[str]) -> Dict[str, str]:
    if not token:
        return {}
    t = str(token).strip()
    if t.lower().startswith(("bearer ", "basic ")):
        return {"Authorization": t}
    return {"Authorization": f"Bearer {t}"}


# Tests install an httpx.MockTransport here.
_TRANSPORT = None


def _fetch_json(cfg: BackendConfig, trace_id: str) -> Optional[Any]:
    """One trace from the backend. None = the backend has no such trace."""
    import json

    import httpx

    from app.core.net_guard import UnsafeUrlError, validate_outbound_url_sync
    url = _BACKENDS[cfg.kind].trace_url(cfg.base_url, trace_id)
    try:
        validate_outbound_url_sync(url, extra_allowed_hosts=[urlsplit(cfg.base_url).hostname or ""])
    except UnsafeUrlError as exc:
        raise TraceBackendError(f"trace backend URL refused: {exc}")
    headers = {"Accept": "application/json", **auth_header(cfg.token)}
    if cfg.tenant and cfg.kind == "tempo":
        headers["X-Scope-OrgID"] = cfg.tenant
    kwargs: Dict[str, Any] = {"timeout": TIMEOUT_SECONDS, "follow_redirects": False}
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        with httpx.Client(**kwargs) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 300:
                    raise TraceBackendError(f"trace backend returned HTTP {resp.status_code}")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise TraceBackendError("trace is larger than the response cap")
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise TraceBackendError(f"trace backend unreachable: {type(exc).__name__}")
    try:
        return json.loads(b"".join(chunks) or b"null")
    except ValueError:
        raise TraceBackendError("trace backend returned invalid JSON")


def fetch_trace(cfg: BackendConfig, trace_id: str,
                policy: RedactionPolicy = DEFAULT_POLICY) -> Optional[List[Span]]:
    """Spans of one trace, or None when the backend has no such trace."""
    body = _fetch_json(cfg, trace_id)
    if body is None:
        return None
    spans = _BACKENDS[cfg.kind].parse(body, policy)
    return spans or None


# ── Mapping spans onto steps ────────────────────────────────────────────────

def descendants_of(spans: List[Span], root_span_id: str) -> List[Span]:
    """Spans whose ancestry reaches `root_span_id` (our step span, which the
    backend never sees itself: the worker only PROPAGATES it). Start order."""
    children: Dict[str, List[Span]] = {}
    for s in spans:
        if s.parent_span_id:
            children.setdefault(s.parent_span_id, []).append(s)
    out: List[Span] = []
    stack, seen = [root_span_id], {root_span_id}
    while stack:
        for child in children.get(stack.pop(), []):
            if child.span_id in seen:
                continue
            seen.add(child.span_id)
            out.append(child)
            stack.append(child.span_id)
    out.sort(key=lambda s: (s.start_us, s.span_id))
    return out


def _span_out(s: Span, origin_us: int) -> Dict[str, Any]:
    return {
        "span_id": s.span_id, "parent_span_id": s.parent_span_id,
        "service": s.service, "operation": s.operation,
        "offset_ms": round(max(0, s.start_us - origin_us) / 1000, 3),
        "duration_ms": round(s.duration_us / 1000, 3),
        "error": s.error, "status_message": s.status_message,
        "attributes": s.attributes,
    }


NO_IDS = ("This result carries no trace ids: trace correlation was off for the project "
          "when it ran, or the worker that ran it predates it.")
NOT_CONFIGURED = "No trace backend is configured on this instance (Settings → Instance → Observability)."
LOCAL_RUN = "Local-worker runs do not carry trace correlation."
NO_REQUESTS = ("No request to the project's own hosts carried a trace header during this step, "
               "so the application could not have recorded spans for it.")
TRACE_MISSING = ("The trace backend has no spans for this trace id — the service may not be "
                 "instrumented, the trace not sampled, or not ingested yet.")
NO_DESCENDANTS = ("The trace backend has spans for this case, but none whose parent is this "
                  "step's span.")


def step_trace_refs(step_evidence: Any) -> List[Dict[str, Any]]:
    """Steps whose stored evidence carries valid trace ids."""
    out = []
    for e in step_evidence or []:
        if not isinstance(e, dict) or not isinstance(e.get("index"), int):
            continue
        t = e.get("trace")
        if isinstance(t, dict) and valid_trace_id(t.get("trace_id")) and valid_span_id(t.get("span_id")):
            out.append({"index": e["index"], "id": f"s{e['index']}", "type": e.get("type"),
                        "trace_id": t["trace_id"], "span_id": t["span_id"],
                        "requests": t.get("requests") if isinstance(t.get("requests"), int) else None})
    return out


def build_result_traces(step_evidence: Any, *, is_local_run: bool = False,
                        cfg: Optional[BackendConfig] = None,
                        policy: RedactionPolicy = DEFAULT_POLICY,
                        fetch=None) -> Dict[str, Any]:
    """The `/traces` payload for one result. `fetch(cfg, trace_id, policy)`
    defaults to `fetch_trace` (tests pass a fake)."""
    fetch = fetch or fetch_trace
    refs = step_trace_refs(step_evidence)
    out: Dict[str, Any] = {"configured": cfg is not None, "backend": cfg.kind if cfg else None,
                           "status": "ok", "reason": None, "error": None, "steps": []}
    if is_local_run:
        out.update(status="no_telemetry", reason=LOCAL_RUN)
        return out
    if not refs:
        out.update(status="no_telemetry", reason=NO_IDS)
        return out
    if cfg is None:
        out.update(status="no_telemetry", reason=NOT_CONFIGURED)
        return out

    wanted = []
    for r in refs:
        if r["requests"] != 0 and r["trace_id"] not in wanted:
            wanted.append(r["trace_id"])
    traces: Dict[str, Optional[List[Span]]] = {}
    for tid in wanted[:MAX_TRACES_PER_RESULT]:
        try:
            traces[tid] = fetch(cfg, tid, policy)
        except TraceBackendError as exc:
            out["error"] = str(exc)[:300]
            break

    for r in refs:
        step: Dict[str, Any] = {"step": r["id"], "index": r["index"], "type": r["type"],
                                "trace_id": r["trace_id"], "span_id": r["span_id"],
                                "requests": r["requests"], "status": "no_telemetry",
                                "reason": None, "spans": [], "error_spans": 0, "truncated": False}
        if r["requests"] == 0:
            step["reason"] = NO_REQUESTS
        elif r["trace_id"] not in traces:
            step["reason"] = (f"The trace backend could not be queried: {out['error']}"
                              if out["error"] else "Not looked up: too many traces on one result.")
        elif traces[r["trace_id"]] is None:
            step["reason"] = TRACE_MISSING
        else:
            found = descendants_of(traces[r["trace_id"]], r["span_id"])
            if not found:
                step["reason"] = NO_DESCENDANTS
            else:
                origin = min(s.start_us for s in found)
                step.update(status="ok",
                            spans=[_span_out(s, origin) for s in found[:MAX_SPANS_PER_STEP]],
                            error_spans=sum(1 for s in found if s.error),
                            truncated=len(found) > MAX_SPANS_PER_STEP)
        out["steps"].append(step)
    return out


# ── Cache (per result) ──────────────────────────────────────────────────────
# In-process, keyed by result id: the endpoint checks access BEFORE reading it.
# Short-lived because spans arrive late (ingestion delay) and a "no spans yet"
# answer should not stick.

_cache: Dict[int, Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def cache_get(result_id: int) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        hit = _cache.get(result_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _cache.pop(result_id, None)
        return None


def cache_put(result_id: int, payload: Dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:CACHE_MAX_ENTRIES // 4]:
                _cache.pop(key, None)
        _cache[result_id] = (time.monotonic() + CACHE_TTL_SECONDS, payload)


def cache_clear() -> None:
    with _cache_lock:
        _cache.clear()


def error_spans_by_step(payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{step index: {count, first error span}} for failure investigation."""
    out: Dict[int, Dict[str, Any]] = {}
    for step in payload.get("steps") or []:
        errs = [s for s in step.get("spans") or [] if s.get("error")]
        if errs:
            out[step["index"]] = {"count": step.get("error_spans") or len(errs), "first": errs[0],
                                  "trace_id": step.get("trace_id")}
    return out
