"""SIEM streaming (WP7): formatters, transport, outbox retry and cap.

External sinks (Splunk, Elastic, generic webhooks) are verified against their
documented wire formats here, not against live services. Syslog is exercised
for real over loopback TCP and UDP sockets in-process.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.models import AuditLog, SecurityEventOutbox
from app.services import siem
from app.services.audit import chain_hash
from app.tasks import security_ops_tasks as ops

TS = datetime(2026, 10, 15, 9, 30, 0, 123456)


def _row(**kw):
    base = dict(id=42, entity_type="auth", entity_id=7, action="login_failed", user_id=7,
                workspace_id=3, timestamp=TS, changes={"email": "a@b.co", "reason": "bad_password"},
                actor_type="user", actor_label="a@b.co", ip_address="10.0.0.9",
                user_agent="curl/8", prev_hash="0" * 64)
    base.update(kw)
    row = SimpleNamespace(**base)
    row.row_hash = chain_hash(**base)
    return row


# --- event shape ---------------------------------------------------------------

def test_audit_event_carries_enough_to_recompute_the_chain():
    row = _row()
    ev = siem.audit_event(row)
    assert ev["chain"]["row_hash"] == row.row_hash
    assert ev["chain"]["prev_hash"] == row.prev_hash
    # A SIEM recomputes with the exact hashed timestamp string.
    recomputed = chain_hash(
        entity_type=ev["entity_type"], entity_id=ev["entity_id"], action=ev["action"],
        user_id=ev["user_id"], workspace_id=ev["workspace_id"],
        timestamp=ev["chain"]["hashed_timestamp"], changes=ev["changes"],
        prev_hash=ev["chain"]["prev_hash"])
    assert recomputed == row.row_hash
    assert ev["timestamp"] == "2026-10-15T09:30:00.123456Z"


# --- syslog --------------------------------------------------------------------

SYSLOG_RE = re.compile(
    r'^<(?P<pri>\d{1,3})>1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) '
    r'(?P<msgid>\S+) (?P<sd>\[traceiq@32473(?: [a-z_]+="(?:[^"\\\]]|\\.)*")*\]) (?P<msg>.+)$')


def test_syslog_line_is_rfc5424():
    line = siem.format_syslog(siem.audit_event(_row()), hostname="tiq-1")
    m = SYSLOG_RE.match(line)
    assert m, line
    assert int(m["pri"]) == 13 * 8 + 6           # log-audit facility, informational
    assert m["ts"] == "2026-10-15T09:30:00.123456Z"
    assert (m["host"], m["app"], m["procid"], m["msgid"]) == ("tiq-1", "traceiq", "-", "audit")
    assert 'action="login_failed"' in m["sd"]
    assert f'row_hash="{_row().row_hash}"' in m["sd"]
    assert json.loads(m["msg"])["action"] == "login_failed"


def test_syslog_alert_severity_maps_to_priority():
    alert = siem.alert_event({"rule": "admin_granted", "severity": "critical", "title": "x",
                              "timestamp": "2026-10-15T09:30:00Z"})
    m = SYSLOG_RE.match(siem.format_syslog(alert, hostname="h"))
    assert int(m["pri"]) == 13 * 8 + 2
    assert m["msgid"] == "security_alert"
    assert 'rule="admin_granted"' in m["sd"]


def test_syslog_structured_data_escapes_quote_backslash_and_bracket():
    ev = siem.audit_event(_row(action='we"ird\\act]ion'))
    m = SYSLOG_RE.match(siem.format_syslog(ev, hostname="h"))
    assert m
    assert r'action="we\"ird\\act\]ion"' in m["sd"]


def test_syslog_hostname_is_printable_ascii_only():
    line = siem.format_syslog(siem.test_event(), hostname="bad host\tname")
    assert line.split(" ")[2] == "badhostname"


def test_octet_counting_frame_counts_bytes_not_characters():
    framed = siem.frame_octet_counting("héllo")
    assert framed == b"6 h\xc3\xa9llo"


def _tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    received = []

    def run():
        conn, _ = srv.accept()
        chunks = []
        while True:
            data = conn.recv(65536)
            if not data:
                break
            chunks.append(data)
        received.append(b"".join(chunks))
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return srv.getsockname()[1], received, t


def _parse_octet_frames(data: bytes):
    frames = []
    while data:
        length, rest = data.split(b" ", 1)
        n = int(length)
        frames.append(rest[:n].decode())
        data = rest[n:]
    return frames


def test_syslog_tcp_delivery_to_a_local_socket():
    port, received, thread = _tcp_server()
    cfg = siem.SinkConfig(sink_type="syslog", url=f"tcp://127.0.0.1:{port}")
    siem.send(cfg, [siem.audit_event(_row()), siem.audit_event(_row(id=43, action="login"))])
    thread.join(timeout=5)
    frames = _parse_octet_frames(received[0])
    assert len(frames) == 2
    assert all(SYSLOG_RE.match(f) for f in frames)
    assert json.loads(SYSLOG_RE.match(frames[1])["msg"])["id"] == 43


def test_syslog_udp_delivery_is_one_datagram_per_event():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)
    cfg = siem.SinkConfig(sink_type="syslog", url=f"udp://127.0.0.1:{sock.getsockname()[1]}")
    siem.send(cfg, [siem.test_event("ops@x")])
    data, _ = sock.recvfrom(65536)
    sock.close()
    assert SYSLOG_RE.match(data.decode())


def test_syslog_unreachable_raises_delivery_error():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()     # nothing listens there now
    cfg = siem.SinkConfig(sink_type="syslog", url=f"tcp://127.0.0.1:{port}")
    with pytest.raises(siem.SiemDeliveryError):
        siem.send(cfg, [siem.test_event()], timeout=2)


# --- HTTP sinks (mock-verified wire formats) -------------------------------------

def test_hec_payload_is_concatenated_event_objects():
    body = siem.hec_payload([siem.audit_event(_row()), siem.test_event()])
    decoder = json.JSONDecoder()
    objs, idx = [], 0
    while idx < len(body):
        obj, idx = decoder.raw_decode(body, idx)
        objs.append(obj)
    assert len(objs) == 2
    assert objs[0]["sourcetype"] == "traceiq:audit"
    assert objs[0]["source"] == "traceiq"
    assert objs[0]["time"] == pytest.approx(TS.replace(tzinfo=timezone.utc).timestamp())
    assert objs[0]["event"]["chain"]["row_hash"] == _row().row_hash
    assert objs[1]["sourcetype"] == "traceiq:test"


def test_elastic_bulk_is_ndjson_with_idempotent_ids():
    body = siem.elastic_bulk([siem.audit_event(_row())], "traceiq-audit")
    assert body.endswith("\n")
    meta, doc = [json.loads(line) for line in body.strip().split("\n")]
    assert meta == {"create": {"_index": "traceiq-audit", "_id": _row().row_hash}}
    assert doc["@timestamp"] == "2026-10-15T09:30:00.123456Z"
    assert doc["action"] == "login_failed"


def test_elastic_partial_failure_raises_but_conflicts_count_as_delivered():
    class Resp:
        def __init__(self, data): self._d = data
        def json(self): return self._d
    siem._check_bulk_response(Resp({"errors": True, "items": [{"create": {"status": 409}}]}))
    with pytest.raises(siem.SiemDeliveryError):
        siem._check_bulk_response(Resp({"errors": True, "items": [
            {"create": {"status": 201}},
            {"create": {"status": 400, "error": {"type": "mapper_parsing_exception"}}}]}))


def test_webhook_signature_verifies():
    body = siem.webhook_body([siem.test_event()])
    sig = siem.sign(body, "s3cret")
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert sig == expected
    assert json.loads(body)["events"][0]["event_type"] == "test"


def test_http_sinks_post_to_the_documented_endpoints(monkeypatch):
    calls = []

    class Resp:
        status_code, text = 200, ""
        def json(self): return {"errors": False}

    def fake_post(url, data=None, headers=None, timeout=None, verify=None, allow_redirects=None):
        calls.append((url, headers, allow_redirects, verify))
        return Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    ev = [siem.test_event()]
    siem.send(siem.SinkConfig("splunk_hec", "https://splunk:8088", token="T"), ev)
    siem.send(siem.SinkConfig("elastic", "https://es:9200/", token="K"), ev)
    siem.send(siem.SinkConfig("webhook", "https://hooks.example.com/x", token="S"), ev)
    assert calls[0][0] == "https://splunk:8088/services/collector/event"
    assert calls[0][1]["Authorization"] == "Splunk T"
    assert calls[1][0] == "https://es:9200/_bulk"
    assert calls[1][1]["Authorization"] == "ApiKey K"
    assert calls[2][1]["X-TraceIQ-Signature"].startswith("sha256=")
    assert all(c[2] is False for c in calls)     # never follow redirects
    assert all(c[3] is True for c in calls)      # TLS verified by default


def test_http_error_status_is_a_delivery_error(monkeypatch):
    import requests

    class Resp:
        status_code, text = 403, "bad token"
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    with pytest.raises(siem.SiemDeliveryError, match="403"):
        siem.send(siem.SinkConfig("splunk_hec", "https://splunk:8088", token="T"),
                  [siem.test_event()])


# --- configuration -------------------------------------------------------------

def _getter(**values):
    return lambda key: values.get(key)


def test_disabled_streaming_has_no_config():
    assert siem.load_config(_getter(SIEM_ENABLED=False)) is None


@pytest.mark.parametrize("values,match", [
    (dict(SIEM_SINK_TYPE="kafka", SIEM_URL="x"), "SIEM_SINK_TYPE"),
    (dict(SIEM_SINK_TYPE="syslog", SIEM_URL="https://x:514"), "tcp://"),
    (dict(SIEM_SINK_TYPE="syslog", SIEM_URL="tcp://host"), "tcp://"),
    (dict(SIEM_SINK_TYPE="splunk_hec", SIEM_URL="https://s"), "SIEM_TOKEN"),
    (dict(SIEM_SINK_TYPE="elastic", SIEM_URL="ftp://s", SIEM_TOKEN="k"), "http"),
])
def test_bad_config_is_refused(values, match):
    with pytest.raises(siem.SiemConfigError, match=match):
        siem.load_config(_getter(SIEM_ENABLED=True, **values))


def test_syslog_destination_is_vetted_as_its_host():
    cfg = siem.SinkConfig("syslog", "tls://siem.corp:6514")
    assert siem.guard_url(cfg) == "http://siem.corp:6514/"


def test_backoff_doubles_and_caps():
    assert [siem.backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert siem.backoff_seconds(50) == siem.MAX_BACKOFF_SECONDS


# --- outbox ----------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AuditLog.__table__, SecurityEventOutbox.__table__])
    with Session(engine) as s:
        yield s


def _audit(session, **kw):
    row = AuditLog(entity_type="auth", entity_id=1, action=kw.pop("action", "login"),
                   timestamp=kw.pop("timestamp", datetime.utcnow()), changes={}, **kw)
    session.add(row)
    session.commit()
    return row


def test_outbox_rows_are_written_in_the_same_flush_when_streaming_is_on(db, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: {"SIEM_ENABLED": True, "SECURITY_ALERTS_ENABLED": False}.get(k))
    row = _audit(db)
    out = db.exec(select(SecurityEventOutbox)).all()
    assert len(out) == 1
    assert out[0].audit_row_hash == row.row_hash
    assert out[0].needs_delivery and not out[0].needs_alert_eval


def test_nothing_is_queued_when_streaming_and_alerts_are_off(db, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: False)
    _audit(db)
    assert db.exec(select(SecurityEventOutbox)).all() == []


def _queue(db, n):
    rows = []
    for i in range(n):
        a = AuditLog(entity_type="auth", entity_id=i, action="login", timestamp=datetime.utcnow(),
                     changes={}, row_hash=f"h{i}", prev_hash="0")
        db.add(a)
        rows.append(SecurityEventOutbox(kind="audit", audit_row_hash=f"h{i}", needs_delivery=True))
    db.add_all(rows)
    db.commit()
    return rows


def test_failed_delivery_backs_off_and_keeps_the_events(db, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: False)
    _queue(db, 3)
    # Relative to the real clock: new outbox rows are due from utcnow(), so a
    # fixed date started failing the day after this test was written.
    now = datetime.utcnow() + timedelta(seconds=1)

    def boom(cfg, events):
        raise siem.SiemDeliveryError("connection refused")

    cfg = siem.SinkConfig("webhook", "https://x", token="t")
    out = ops.deliver_pending(db, cfg, now=now, sender=boom)
    db.commit()
    assert out == {"sent": 0, "failed": 3, "error": "connection refused"}
    rows = db.exec(select(SecurityEventOutbox)).all()
    assert all(r.needs_delivery and r.attempts == 1 for r in rows)
    assert all(r.next_attempt_at == now + timedelta(seconds=5) for r in rows)
    assert rows[0].last_error == "connection refused"

    # Not due yet: nothing is retried inside the backoff window.
    assert ops.deliver_pending(db, cfg, now=now + timedelta(seconds=1), sender=boom) == {"sent": 0}

    sent = []
    out = ops.deliver_pending(db, cfg, now=now + timedelta(seconds=6),
                              sender=lambda c, evs: sent.extend(evs))
    db.commit()
    assert out == {"sent": 3}
    assert [e["entity_id"] for e in sent] == [0, 1, 2]       # in order
    assert not any(r.needs_delivery for r in db.exec(select(SecurityEventOutbox)).all())


def test_cap_drops_the_oldest_events(db, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: False)
    rows = _queue(db, 5)
    dropped = ops.enforce_cap(db, 3)
    db.commit()
    assert dropped == 2
    left = [r.audit_row_hash for r in db.exec(select(SecurityEventOutbox).order_by(SecurityEventOutbox.id)).all()]
    assert left == ["h2", "h3", "h4"]
    assert ops.enforce_cap(db, 0) == 0          # 0 = no cap
    assert len(rows) == 5


def test_an_event_whose_audit_row_aged_out_is_dropped_not_retried(db):
    db.add(SecurityEventOutbox(kind="audit", audit_row_hash="gone", needs_delivery=True))
    db.commit()
    out = ops.deliver_pending(db, siem.SinkConfig("webhook", "https://x", token="t"),
                              sender=lambda c, e: None)
    db.commit()
    assert out == {"sent": 0}
    assert not db.exec(select(SecurityEventOutbox)).first().needs_delivery
