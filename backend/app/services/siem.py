"""Audit and security-event streaming to a SIEM (WP7).

Four sinks, chosen by the `SIEM_SINK_TYPE` instance setting:

  syslog      RFC 5424 over TCP (octet-counted framing, RFC 6587), TLS or UDP
  splunk_hec  Splunk HTTP Event Collector, `Authorization: Splunk <token>`
  elastic     Elasticsearch/OpenSearch bulk API, `Authorization: ApiKey <key>`
  webhook     generic HTTPS JSON, HMAC-SHA256 in `X-TraceIQ-Signature`

**Delivery is never on the request path.** Audit rows are written inside the
request's transaction; `enqueue_audit_rows` (called from the audit chain's
before_flush hook) adds a `SecurityEventOutbox` row in the same flush, so the
event exists exactly when the audit row commits. A beat task drains the outbox
after commit, retries failures with exponential backoff, and the
`SIEM_OUTBOX_MAX` cap drops the oldest events during a long outage rather than
growing the database without bound. The audit table itself is the system of
record; the SIEM stream is a copy, so a dropped event is recoverable from
`/workspaces/{id}/audit/export`.

**Events carry the hash chain** (`prev_hash`, `row_hash` and the exact
timestamp string the hash was computed over), so a SIEM can verify the chain
independently of TraceIQ — the point of shipping audit logs off-box is that
someone who controls this database cannot quietly rewrite them.

Formatters are pure functions (unit-tested); `send()` does the I/O.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SINK_TYPES = ("syslog", "splunk_hec", "elastic", "webhook")
EVENT_SCHEMA = "traceiq.security-event/1"

#: RFC 5424 facility 13 = "log audit".
SYSLOG_FACILITY = 13
#: Structured-data id. 32473 is the IANA enterprise number reserved for
#: documentation (RFC 5612); collectors match on the `traceiq` name part.
SYSLOG_SD_ID = "traceiq@32473"
SYSLOG_APP = "traceiq"
_SYSLOG_SEVERITY = {"critical": 2, "high": 3, "medium": 4, "low": 5, "info": 6}

MAX_BATCH = 200
MAX_BACKOFF_SECONDS = 3600


class SiemConfigError(ValueError):
    """The configured sink cannot be used as configured."""


class SiemDeliveryError(RuntimeError):
    """The sink was reachable in principle but this delivery failed."""


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _hostname() -> str:
    try:
        return socket.gethostname() or "traceiq"
    except OSError:
        return "traceiq"


def audit_event(row: Any) -> Dict[str, Any]:
    """The SIEM representation of one AuditLog row."""
    ts = row.timestamp
    return {
        "schema": EVENT_SCHEMA,
        "event_type": "audit",
        "severity": "info",
        "id": row.id,
        "timestamp": _iso_z(ts),
        "workspace_id": row.workspace_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "user_id": row.user_id,
        "actor_type": row.actor_type,
        "actor_label": row.actor_label,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "changes": row.changes or {},
        # Everything needed to recompute row_hash with
        # app.services.audit.chain_hash — notably the timestamp string exactly
        # as hashed (naive UTC isoformat, no zone suffix).
        "chain": {
            "algorithm": "sha256",
            "prev_hash": row.prev_hash,
            "row_hash": row.row_hash,
            "hashed_timestamp": ts.isoformat() if isinstance(ts, datetime) else str(ts),
            "fields": ["entity_type", "entity_id", "action", "user_id", "workspace_id",
                       "hashed_timestamp", "changes(sorted-key compact JSON)", "prev_hash"],
        },
        "host": _hostname(),
    }


def alert_event(alert: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema": EVENT_SCHEMA, "event_type": "security_alert",
            "timestamp": alert.get("timestamp") or _iso_z(datetime.utcnow()),
            "severity": alert.get("severity", "high"), "host": _hostname(), **alert}


def test_event(requested_by: Optional[str] = None) -> Dict[str, Any]:
    return {"schema": EVENT_SCHEMA, "event_type": "test", "severity": "info",
            "timestamp": _iso_z(datetime.utcnow()), "host": _hostname(),
            "message": "TraceIQ SIEM connectivity test", "requested_by": requested_by}


def _iso_z(ts: Any) -> str:
    if isinstance(ts, datetime):
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        return ts.isoformat(timespec="microseconds") + "Z"
    return str(ts)


def _epoch(event: Dict[str, Any]) -> float:
    raw = str(event.get("timestamp") or "").rstrip("Z")
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return datetime.now(timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Formatters (pure)
# ---------------------------------------------------------------------------

def _sd_escape(value: Any) -> str:
    # RFC 5424 §6.3.3: '"', '\' and ']' must be escaped inside PARAM-VALUE.
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _printable(value: str, limit: int) -> str:
    """HOSTNAME/APP-NAME/MSGID are PRINTUSASCII (33-126) with a length cap; NILVALUE '-' if empty."""
    cleaned = "".join(ch for ch in (value or "") if 33 <= ord(ch) <= 126)[:limit]
    return cleaned or "-"


def format_syslog(event: Dict[str, Any], hostname: Optional[str] = None) -> str:
    """One RFC 5424 message (without transport framing).

    <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG
    """
    severity = _SYSLOG_SEVERITY.get(str(event.get("severity", "info")).lower(), 6)
    pri = SYSLOG_FACILITY * 8 + severity
    params = {k: event.get(k) for k in ("event_type", "id", "action", "entity_type",
                                        "entity_id", "workspace_id", "user_id", "rule")
              if event.get(k) is not None}
    chain = event.get("chain") or {}
    if chain.get("row_hash"):
        params["row_hash"] = chain["row_hash"]
        params["prev_hash"] = chain.get("prev_hash") or ""
    sd = "[" + SYSLOG_SD_ID + "".join(
        f' {k}="{_sd_escape(v)}"' for k, v in params.items()) + "]"
    msg = json.dumps(event, separators=(",", ":"), sort_keys=True, default=str)
    return " ".join([
        f"<{pri}>1",
        str(event.get("timestamp") or _iso_z(datetime.utcnow())),
        _printable(hostname or event.get("host") or _hostname(), 255),
        SYSLOG_APP,
        "-",
        _printable(str(event.get("event_type") or "audit"), 32),
        sd,
        msg,
    ])


def frame_octet_counting(message: str) -> bytes:
    """RFC 6587 octet-counting: `MSG-LEN SP SYSLOG-MSG`. Unlike newline framing
    it survives a newline inside the message (audit changes can contain one)."""
    data = message.encode("utf-8")
    return f"{len(data)} ".encode("ascii") + data


def hec_payload(events: Sequence[Dict[str, Any]]) -> str:
    """Splunk HEC batch: concatenated JSON objects (not a JSON array)."""
    return "".join(json.dumps({
        "time": round(_epoch(e), 6),
        "host": e.get("host") or _hostname(),
        "source": "traceiq",
        "sourcetype": f"traceiq:{e.get('event_type', 'audit')}",
        "event": e,
    }, separators=(",", ":"), default=str) for e in events)


def elastic_bulk(events: Sequence[Dict[str, Any]], index: str) -> str:
    """NDJSON for the _bulk API. `create` with `_id` = row hash makes delivery
    idempotent: a retried batch the cluster half-applied answers 409 for the
    rows it already has instead of duplicating them."""
    lines: List[str] = []
    for e in events:
        meta: Dict[str, Any] = {"_index": index}
        doc_id = (e.get("chain") or {}).get("row_hash") or e.get("alert_id")
        if doc_id:
            meta["_id"] = doc_id
        lines.append(json.dumps({"create": meta}, separators=(",", ":")))
        lines.append(json.dumps({"@timestamp": e.get("timestamp"), **e},
                                separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n"


def webhook_body(events: Sequence[Dict[str, Any]]) -> bytes:
    return json.dumps({"schema": EVENT_SCHEMA, "events": list(events)},
                      separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def backoff_seconds(attempts: int) -> int:
    """5s, 10s, 20s … capped at an hour."""
    return min(5 * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SinkConfig:
    sink_type: str
    url: str
    token: Optional[str] = None
    index: str = "traceiq-audit"
    tls_verify: bool = True
    ca_cert: Optional[str] = None


def load_config(get: Any = None) -> Optional[SinkConfig]:
    """The effective sink, or None when streaming is off."""
    if get is None:
        from app.services.instance_settings import effective as get
    if not get("SIEM_ENABLED"):
        return None
    sink_type = (get("SIEM_SINK_TYPE") or "").strip().lower()
    url = (get("SIEM_URL") or "").strip()
    if sink_type not in SINK_TYPES:
        raise SiemConfigError(f"SIEM_SINK_TYPE must be one of {', '.join(SINK_TYPES)}")
    if not url:
        raise SiemConfigError("SIEM_URL is required")
    cfg = SinkConfig(sink_type=sink_type, url=url, token=get("SIEM_TOKEN") or None,
                     index=(get("SIEM_ELASTIC_INDEX") or "traceiq-audit").strip(),
                     tls_verify=get("SIEM_TLS_VERIFY") is not False,
                     ca_cert=(get("SIEM_CA_CERT") or None))
    validate_config(cfg)
    return cfg


def validate_config(cfg: SinkConfig) -> None:
    parts = urlsplit(cfg.url)
    if cfg.sink_type == "syslog":
        if parts.scheme not in ("tcp", "tls", "udp") or not parts.hostname or not parts.port:
            raise SiemConfigError("syslog SIEM_URL must be tcp://host:port, tls://host:port or udp://host:port")
    else:
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise SiemConfigError(f"{cfg.sink_type} SIEM_URL must be an http(s) URL")
        if cfg.sink_type in ("splunk_hec", "elastic", "webhook") and not cfg.token:
            raise SiemConfigError(f"{cfg.sink_type} needs SIEM_TOKEN")


def guard_url(cfg: SinkConfig) -> str:
    """The URL net_guard should vet. Syslog targets have no http scheme, so the
    host is checked through an equivalent http URL — the address rules (no
    metadata/link-local, private only when allowed) are what matter."""
    if cfg.sink_type == "syslog":
        parts = urlsplit(cfg.url)
        return f"http://{parts.hostname}:{parts.port}/"
    return cfg.url


async def check_destination(cfg: SinkConfig) -> None:
    from app.core.net_guard import validate_outbound_url
    await validate_outbound_url(guard_url(cfg))


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _ssl_context(cfg: SinkConfig) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=cfg.ca_cert) if cfg.ca_cert else ssl.create_default_context()
    if not cfg.tls_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _send_syslog(cfg: SinkConfig, events: Sequence[Dict[str, Any]], timeout: float) -> None:
    parts = urlsplit(cfg.url)
    host, port = parts.hostname, parts.port
    messages = [format_syslog(e) for e in events]
    if parts.scheme == "udp":
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            for m in messages:
                sock.sendto(m.encode("utf-8"), (host, port))
        return
    conn = socket.create_connection((host, port), timeout=timeout)
    try:
        if parts.scheme == "tls":
            conn = _ssl_context(cfg).wrap_socket(conn, server_hostname=host)
        conn.sendall(b"".join(frame_octet_counting(m) for m in messages))
    finally:
        conn.close()


def _verify_arg(cfg: SinkConfig):
    if not cfg.tls_verify:
        return False
    return cfg.ca_cert or True


def _send_http(cfg: SinkConfig, events: Sequence[Dict[str, Any]], timeout: float) -> None:
    import requests

    if cfg.sink_type == "splunk_hec":
        url = cfg.url.rstrip("/")
        if "/services/collector" not in url:
            url += "/services/collector/event"
        body = hec_payload(events).encode("utf-8")
        headers = {"Authorization": f"Splunk {cfg.token}", "Content-Type": "application/json"}
    elif cfg.sink_type == "elastic":
        url = cfg.url.rstrip("/")
        if not url.endswith("/_bulk"):
            url += "/_bulk"
        body = elastic_bulk(events, cfg.index).encode("utf-8")
        headers = {"Authorization": f"ApiKey {cfg.token}", "Content-Type": "application/x-ndjson"}
    else:
        url = cfg.url
        body = webhook_body(events)
        headers = {"Content-Type": "application/json", "User-Agent": "TraceIQ-SIEM/1.0",
                   "X-TraceIQ-Signature": sign(body, cfg.token or ""),
                   "X-TraceIQ-Event-Count": str(len(events))}
    # No redirects: a 30x to somewhere else would bypass the destination check.
    resp = requests.post(url, data=body, headers=headers, timeout=timeout,
                         verify=_verify_arg(cfg), allow_redirects=False)
    if resp.status_code >= 300:
        raise SiemDeliveryError(f"{cfg.sink_type} answered HTTP {resp.status_code}: {resp.text[:300]}")
    if cfg.sink_type == "elastic":
        _check_bulk_response(resp)


def _check_bulk_response(resp: Any) -> None:
    try:
        data = resp.json()
    except ValueError:
        return
    if not data.get("errors"):
        return
    failed = []
    for item in data.get("items") or []:
        op = item.get("create") or item.get("index") or {}
        status = op.get("status", 0)
        if status >= 300 and status != 409:   # 409 = already have it (a retry)
            failed.append(f"{status}:{(op.get('error') or {}).get('type')}")
    if failed:
        raise SiemDeliveryError(f"elastic rejected {len(failed)} document(s): {', '.join(failed[:5])}")


def send(cfg: SinkConfig, events: Sequence[Dict[str, Any]], timeout: float = 10.0) -> None:
    """Deliver a batch or raise. Synchronous; called from Celery or a thread."""
    if not events:
        return
    if cfg.sink_type == "syslog":
        try:
            _send_syslog(cfg, events, timeout)
        except OSError as exc:
            raise SiemDeliveryError(f"syslog delivery failed: {exc}") from exc
    else:
        try:
            _send_http(cfg, events, timeout)
        except SiemDeliveryError:
            raise
        except Exception as exc:  # noqa: BLE001 — requests raises many types
            raise SiemDeliveryError(f"{cfg.sink_type} delivery failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------

def enqueue_audit_rows(session: Any, rows: Iterable[Any]) -> int:
    """Called from the audit chain's before_flush hook with every new AuditLog
    row in the flush (already sealed, so row_hash is known). Adds one outbox
    row per audit row when streaming or alerting is on. Never raises: auditing
    must not fail because the SIEM path is misconfigured."""
    try:
        from app.services.instance_settings import effective
        deliver = bool(effective("SIEM_ENABLED"))
        evaluate = bool(effective("SECURITY_ALERTS_ENABLED"))
        if not (deliver or evaluate):
            return 0
        from app.models import SecurityEventOutbox
        n = 0
        for row in rows:
            if not row.row_hash:
                continue
            session.add(SecurityEventOutbox(
                kind="audit", audit_row_hash=row.row_hash,
                needs_delivery=deliver, needs_alert_eval=evaluate))
            n += 1
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SIEM] could not enqueue audit events: %s", exc)
        return 0


def enqueue_event(session: Any, kind: str, payload: Dict[str, Any], *, deliver: bool = True) -> Any:
    from app.models import SecurityEventOutbox
    row = SecurityEventOutbox(kind=kind, payload=payload, needs_delivery=deliver,
                              needs_alert_eval=False)
    session.add(row)
    return row
