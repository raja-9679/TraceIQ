"""Webhook capture + test-inbox lookup against a real Postgres, over ASGI.

    ./run-tests-live.sh tests/integration/test_step_hooks_db.py -q
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import step_hooks as api                           # noqa: E402
from app.core.config import settings                            # noqa: E402
from app.models import TestRun, TestStatus, WebhookCapture      # noqa: E402
from app.services import step_hooks as th                       # noqa: E402
from app.services import inbox_providers as ti                       # noqa: E402

from tests.integration.test_targeted_runs_db import scene, session  # noqa: E402,F401

WORKER = {"X-Worker-Secret": settings.WEBHOOK_SECRET or settings.SECRET_KEY}


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    from app.core.database import engine
    from app.main import app
    await engine.dispose()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tiq.test") as c:
        yield c
    await engine.dispose()


async def _run(session, scene, status=TestStatus.RUNNING) -> TestRun:
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=status)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _url(run, case_id, label="default", row=None):
    return f"/api/hooks/capture/{th.webhook_token(run.id, case_id, row, label)}"


async def _search(client, run, case_id, label="default"):
    r = await client.post("/api/internal/webhook-captures/search", headers=WORKER,
                          json={"run_id": run.id, "test_case_id": case_id, "label": label})
    return r


async def test_capture_is_redacted_stored_and_found_by_the_owning_case(session, scene, client):
    run = await _run(session, scene)
    case = scene.root_cases[0]
    r = await client.post(_url(run, case) + "/events/paid?attempt=1",
                          headers={"Authorization": "Bearer live-secret", "X-Event": "paid"},
                          json={"type": "order.paid", "password": "hunter2", "data": {"id": 42}})
    assert r.status_code == 200, r.text

    found = (await _search(client, run, case)).json()["captures"]
    assert len(found) == 1
    cap = found[0]
    assert cap["method"] == "POST" and cap["path"] == "/events/paid" and cap["query"] == "attempt=1"
    assert cap["headers"]["authorization"] == "[REDACTED]"
    assert cap["headers"]["x-event"] == "paid"
    body = json.loads(cap["body"])
    assert body == {"type": "order.paid", "password": "[REDACTED]", "data": {"id": 42}}

    # Raw row in the DB is the redacted one — nothing sensitive at rest.
    row = (await session.exec(select(WebhookCapture))).one()
    assert "hunter2" not in row.body and "live-secret" not in json.dumps(row.headers)

    # Another label / case of the same run sees nothing: lookups are by the
    # derived token, not by run.
    assert (await _search(client, run, case, label="other")).json()["captures"] == []
    assert (await _search(client, run, scene.root_cases[1])).json()["captures"] == []


async def test_forged_finished_and_flooded_urls_are_refused(session, scene, client, monkeypatch):
    run = await _run(session, scene)
    case = scene.root_cases[0]
    tok = th.webhook_token(run.id, case, None, "default")
    forged = tok[:-1] + ("a" if tok[-1] != "a" else "b")
    assert (await client.post(f"/api/hooks/capture/{forged}", json={})).status_code == 404
    assert (await client.post("/api/hooks/capture/garbage", json={})).status_code == 404

    monkeypatch.setattr(api, "MAX_CAPTURES_PER_TOKEN", 2)
    for _ in range(2):
        assert (await client.put(_url(run, case), content=b"x")).status_code == 200
    assert (await client.put(_url(run, case), content=b"x")).status_code == 429

    done = await _run(session, scene, status=TestStatus.PASSED)
    assert (await client.post(_url(done, case), json={})).status_code == 410


async def test_large_bodies_are_truncated_not_buffered(session, scene, client, monkeypatch):
    monkeypatch.setattr(api, "MAX_CAPTURE_BYTES", 1024)
    run = await _run(session, scene)
    r = await client.post(_url(run, scene.root_cases[0]), content=b"a" * 5000,
                          headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    cap = (await _search(client, run, scene.root_cases[0])).json()["captures"][0]
    assert cap["truncated"] is True and len(cap["body"]) == 1024 and cap["body_size"] == 5000


async def test_internal_lookups_are_scoped_to_live_runs_and_the_runs_project(session, scene, client):
    run = await _run(session, scene)
    bad_secret = await client.post("/api/internal/webhook-captures/search",
                                   headers={"X-Worker-Secret": "nope"},
                                   json={"run_id": run.id, "test_case_id": scene.root_cases[0]})
    assert bad_secret.status_code == 403
    # A case from another project cannot be paired with this run.
    assert (await _search(client, run, scene.foreign_case)).status_code == 404
    done = await _run(session, scene, status=TestStatus.FAILED)
    assert (await _search(client, done, scene.root_cases[0])).status_code == 404
    r = await client.post("/api/internal/test-inbox/search", headers=WORKER,
                          json={"run_id": run.id, "test_case_id": scene.foreign_case})
    assert r.status_code == 404


async def test_inbox_search_derives_the_address_server_side(session, scene, client, monkeypatch):
    run = await _run(session, scene)
    case = scene.root_cases[0]
    cfg = th.InboxConfig(provider="mailpit", domain="inbox.traceiq.test",
                         mailpit_url="http://mailpit:8025")
    asked = []

    class Fake:
        async def list_messages(self, address):
            asked.append(address)
            return [ti.InboxMessage(id="1", subject="Your code 482913", sender="s@x",
                                    recipients=[address], text="Use 482913 to sign in")]

        async def load_body(self, msg):
            return None

    monkeypatch.setattr(th, "load_inbox_config", lambda get=None: cfg)
    monkeypatch.setattr(ti, "provider_for", lambda c: Fake())
    r = await client.post("/api/internal/test-inbox/search", headers=WORKER, json={
        "run_id": run.id, "test_case_id": case, "label": "signup", "extract": "code"})
    assert r.status_code == 200, r.text
    out = r.json()
    expected = th.inbox_address(cfg, run.id, case, None, "signup")
    assert asked == [expected] and out["address"] == expected
    assert out["value"] == "482913"                      # the step gets the real code…
    assert "482913" not in json.dumps(out["message"])    # …the preview does not

    bad = await client.post("/api/internal/test-inbox/search", headers=WORKER, json={
        "run_id": run.id, "test_case_id": case, "subject_regex": "("})
    assert bad.status_code == 422

    monkeypatch.setattr(th, "load_inbox_config", lambda get=None: th.InboxConfig())
    off = await client.post("/api/internal/test-inbox/search", headers=WORKER, json={
        "run_id": run.id, "test_case_id": case})
    assert off.status_code == 422 and "no test inbox" in off.json()["detail"]


async def test_retention_sweep_drops_only_expired_captures(session, scene):
    from app.tasks.cleanup_tasks import purge_webhook_captures
    run = await _run(session, scene)
    tok = th.webhook_token(run.id, scene.root_cases[0], None, "default")
    old = WebhookCapture(token=tok, run_id=run.id, test_case_id=scene.root_cases[0],
                         method="POST", received_at=datetime.utcnow() - timedelta(hours=25))
    fresh = WebhookCapture(token=tok, run_id=run.id, test_case_id=scene.root_cases[0],
                           method="POST")
    session.add_all([old, fresh])
    await session.commit()
    assert purge_webhook_captures() == 1
    left = (await session.exec(select(WebhookCapture.id))).all()
    assert left == [fresh.id]


async def test_deleting_the_run_deletes_its_captures(session, scene):
    from app.services.entity_delete import cascade_delete
    run = await _run(session, scene)
    session.add(WebhookCapture(token="t", run_id=run.id, test_case_id=scene.root_cases[0], method="POST"))
    await session.commit()
    await cascade_delete(session, "testrun", [run.id])
    await session.commit()
    assert (await session.exec(select(WebhookCapture))).all() == []
