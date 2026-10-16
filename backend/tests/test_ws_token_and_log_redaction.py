"""The run-progress WebSocket takes its JWT from the subprotocol header, and
credentials in URLs are masked in every log line.

Before: the browser sent `?token=<jwt>` and uvicorn logged the full URL on
every connection ("WebSocket /api/ws/runs/2?token=eyJ... [accepted]").
"""
import logging
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.endpoints import websockets as ws_mod
from app.core.telemetry import RedactSecretsFilter, redact_secret_query

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhQGIuYyJ9.c2lnbmF0dXJl"


# --- log redaction ------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (f"/api/ws/runs/2?token={JWT}", "/api/ws/runs/2?token=[REDACTED]"),
    (f"/x?a=1&access_token={JWT}&b=2", "/x?a=1&access_token=[REDACTED]&b=2"),
    ("/x?API_KEY=tiq_abc123 HTTP/1.1", "/x?API_KEY=[REDACTED] HTTP/1.1"),
    ('"GET /x?refresh_token=r1" 200', '"GET /x?refresh_token=[REDACTED]" 200'),
    ("/api/runs?suite_id=1&case_id=1", "/api/runs?suite_id=1&case_id=1"),   # untouched
    ("tokenizer=on&mytoken=keep", "tokenizer=on&mytoken=keep"),            # not a param boundary
])
def test_redact_secret_query(raw, expected):
    assert redact_secret_query(raw) == expected


def _uvicorn_style_record(name, msg, args):
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, None)


def test_filter_masks_uvicorn_access_and_websocket_lines():
    f = RedactSecretsFilter()
    access = _uvicorn_style_record(
        "uvicorn.access", '%s - "%s %s HTTP/%s" %d',
        ("172.28.0.10:1", "GET", f"/api/ws/runs/3?token={JWT}", "1.1", 101))
    accepted = _uvicorn_style_record(
        "uvicorn.error", '%s - "WebSocket %s" [accepted]',
        ("('172.28.0.10', 2)", f"/api/ws/runs/3?token={JWT}"))
    for rec in (access, accepted):
        assert f.filter(rec) is True
        assert JWT not in rec.getMessage()
        assert "token=[REDACTED]" in rec.getMessage()


def test_filter_leaves_ordinary_records_alone():
    rec = _uvicorn_style_record("app", "run %s finished", (7,))
    RedactSecretsFilter().filter(rec)
    assert (rec.msg, rec.args) == ("run %s finished", (7,))


def test_filter_is_installed_on_the_root_handler():
    from app.core import telemetry
    telemetry.configure_logging("test")
    assert any(isinstance(f, RedactSecretsFilter)
               for h in logging.getLogger().handlers for f in h.filters)


# --- subprotocol parsing ------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    (f"traceiq.bearer, {JWT}", JWT),
    (f"traceiq.bearer,{JWT}", JWT),
    ("traceiq.bearer", None),
    (f"{JWT}, traceiq.bearer", None),     # order matters: marker first
    ("graphql-ws", None),
    (None, None),
    ("", None),
])
def test_token_from_subprotocols(header, expected):
    assert ws_mod.token_from_subprotocols(header) == expected


# --- the handshake --------------------------------------------------------------

class _FakePubSub:
    async def subscribe(self, channel):
        self.channel = channel

    async def listen(self):
        yield {"type": "message", "data": '{"status": "running"}'}

    async def unsubscribe(self, channel):
        pass

    async def close(self):
        pass


class _FakeRedis:
    def pubsub(self):
        return _FakePubSub()


class _Run:
    project_id = 1


class _Session:
    async def get(self, model, run_id):
        return _Run() if run_id == 5 else None


@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def factory():
        yield _Session()

    async def user_from_jwt(token, session):
        return type("U", (), {"id": 1})() if token == JWT else None

    async def has_access(user_id, project_id, session):
        return True

    monkeypatch.setattr(ws_mod, "async_session_factory", factory)
    monkeypatch.setattr(ws_mod, "_user_from_jwt", user_from_jwt)
    monkeypatch.setattr(ws_mod.access_service, "has_project_access", has_access)
    monkeypatch.setattr(ws_mod.RedisClient, "get_instance", staticmethod(lambda: _FakeRedis()))
    app = FastAPI()
    app.include_router(ws_mod.router, prefix="/api")
    return TestClient(app)


def test_token_in_subprotocol_is_accepted_and_echoed(client):
    with client.websocket_connect("/api/ws/runs/5", subprotocols=["traceiq.bearer", JWT]) as ws:
        assert ws.accepted_subprotocol == "traceiq.bearer"
        assert ws.receive_text() == '{"status": "running"}'


def test_query_token_still_works_for_old_clients(client):
    with client.websocket_connect(f"/api/ws/runs/5?token={JWT}") as ws:
        assert ws.accepted_subprotocol is None
        assert ws.receive_text() == '{"status": "running"}'


@pytest.mark.parametrize("url,subprotocols,code", [
    ("/api/ws/runs/5", None, 4401),                                  # no token at all
    ("/api/ws/runs/5", ["traceiq.bearer", "not-a-valid-jwt"], 4401),  # bad token
    ("/api/ws/runs/404", ["traceiq.bearer", JWT], 4404),             # unknown run
])
def test_refusals(client, url, subprotocols, code):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, subprotocols=subprotocols):
            pass
    assert exc.value.code == code
