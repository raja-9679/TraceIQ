"""WP8 against a real Postgres: IP allowlists (API keys, SCIM), the fleet
endpoint's admin gate, paging + maintenance windows, multi-region monitors,
region-pinned schedules and the egress policy.

    TRACEIQ_SCRATCH_DB=traceiq_wp8 ./run-tests-live.sh tests/integration/test_fleet_wp8_db.py -q
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from starlette.requests import Request

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import api_keys, fleet, paging as paging_api, scim       # noqa: E402
from app.api.instance_settings import get_current_instance_admin     # noqa: E402
from app.core import auth                                            # noqa: E402
from app.core.auth import AuthPrincipal                              # noqa: E402
from app.models import (                                             # noqa: E402
    ApiKey, ApiKeyCreate, AuditLog, MonitorCheck, PagingEvent, Project, RunTrigger,
    TestRun, TestSchedule, TestStatus, User, UserWorkspace,
)
from app.services import paging as paging_svc                        # noqa: E402
from app.services import instance_settings                           # noqa: E402

from tests.integration.test_targeted_runs_db import (                # noqa: E402,F401
    no_queue, scene, session,
)
from tests.test_fleet_wp8 import FakeRedis                           # noqa: E402


def _req(headers=None, client=("203.0.113.5", 1234), path="/api/x"):
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": client}
    return Request(scope)


def _settings(monkeypatch, **values):
    real = instance_settings.effective

    def fake(key):
        return values[key] if key in values else real(key)
    monkeypatch.setattr(instance_settings, "effective", fake)
    monkeypatch.setattr(scim, "effective", fake)


async def _member(session, scene, email="member@corp.example.com") -> User:
    from app.models import Workspace
    ws_id = (await session.exec(select(Project.workspace_id).where(
        Project.id == scene.project_id))).one()
    user = User(email=email, full_name="Member", hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    session.add(UserWorkspace(user_id=user.id, workspace_id=ws_id, role="member"))
    await session.commit()
    return user


async def _mint_key(session, scene, cidrs=None):
    ws_id = (await session.exec(select(Project.workspace_id).where(
        Project.id == scene.project_id))).one()
    out = await api_keys.create_api_key(
        ws_id, ApiKeyCreate(name="ci", workspace_id=ws_id, allowed_cidrs=cidrs),
        principal=AuthPrincipal(user=scene.user), session=session)
    return out, ws_id


# --- API key IP allowlist --------------------------------------------------------

async def test_api_key_outside_its_cidrs_is_refused_and_audited(session, scene):
    out, _ = await _mint_key(session, scene, ["198.51.100.0/24"])
    assert out.allowed_cidrs == ["198.51.100.0/24"]

    with pytest.raises(HTTPException) as exc:
        await auth._principal_from_api_key(out.secret, session, None, client_addr="203.0.113.5")
    assert exc.value.status_code == 403

    key = await session.get(ApiKey, out.id)
    await session.refresh(key)
    assert key.last_used_at is None          # a refused attempt is not "use"
    refused = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "api_key", AuditLog.entity_id == out.id,
        AuditLog.action == "ip_refused"))).all()
    assert len(refused) == 1
    assert refused[0].changes["client_ip"] == "203.0.113.5"
    assert refused[0].actor_label == out.prefix

    principal = await auth._principal_from_api_key(out.secret, session, None,
                                                   client_addr="198.51.100.20")
    assert principal and principal.api_key.id == out.id


async def test_principal_resolution_uses_forwarded_for_only_from_trusted_proxies(
        session, scene, monkeypatch):
    out, _ = await _mint_key(session, scene, ["198.51.100.0/24"])
    headers = {"X-API-Key": out.secret, "X-Forwarded-For": "198.51.100.20"}

    _settings(monkeypatch, TRUSTED_PROXY_CIDRS=[])
    with pytest.raises(HTTPException) as exc:     # peer 172.18.0.2 is not allowed
        await auth.get_current_principal(_req(headers, client=("172.18.0.2", 1)), None, session)
    assert exc.value.status_code == 403

    _settings(monkeypatch, TRUSTED_PROXY_CIDRS=["172.18.0.0/16"])
    p = await auth.get_current_principal(_req(headers, client=("172.18.0.2", 1)), None, session)
    assert p.api_key.id == out.id


async def test_api_key_cidr_validation_and_who_may_change_it(session, scene):
    ws_id = (await session.exec(select(Project.workspace_id).where(
        Project.id == scene.project_id))).one()
    with pytest.raises(HTTPException) as exc:
        await api_keys.create_api_key(
            ws_id, ApiKeyCreate(name="bad", workspace_id=ws_id, allowed_cidrs=["10.0.0.0/33"]),
            principal=AuthPrincipal(user=scene.user), session=session)
    assert exc.value.status_code == 400

    out, _ = await _mint_key(session, scene)
    member = await _member(session, scene)
    body = api_keys.ApiKeyNetworkUpdate(allowed_cidrs=["10.0.0.0/8"])
    with pytest.raises(HTTPException) as exc:
        await api_keys.update_api_key_cidrs(ws_id, out.id, body,
                                            principal=AuthPrincipal(user=member), session=session)
    assert exc.value.status_code == 403
    # The key itself can never loosen (or set) its own allowlist.
    key = await session.get(ApiKey, out.id)
    with pytest.raises(HTTPException):
        await api_keys.update_api_key_cidrs(
            ws_id, out.id, body, principal=AuthPrincipal(user=scene.user, api_key=key),
            session=session)
    updated = await api_keys.update_api_key_cidrs(
        ws_id, out.id, body, principal=AuthPrincipal(user=scene.user), session=session)
    assert updated.allowed_cidrs == ["10.0.0.0/8"]
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "api_key", AuditLog.entity_id == out.id))).all()
    assert "update_allowed_cidrs" in actions


# --- SCIM allowlist ------------------------------------------------------------------

async def test_scim_allowlist(session, scene, monkeypatch):
    _settings(monkeypatch, SCIM_TOKEN="scim-token", SCIM_ALLOWED_CIDRS=["192.0.2.0/24"],
              TRUSTED_PROXY_CIDRS=[])
    outside = _req(client=("203.0.113.5", 1))
    started = datetime.utcnow() - timedelta(seconds=1)

    # A wrong token from outside: refused, NOT audited (scanner noise).
    with pytest.raises(scim.ScimError) as exc:
        await scim.scim_principal(outside, "Bearer wrong", session)
    assert exc.value.status == 403
    # The real token from outside: refused and audited (a leaked credential).
    with pytest.raises(scim.ScimError) as exc:
        await scim.scim_principal(outside, "Bearer scim-token", session)
    assert exc.value.status == 403
    rows = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "scim", AuditLog.action == "ip_refused",
        AuditLog.timestamp >= started))).all()
    assert len(rows) == 1 and rows[0].changes["client_ip"] == "203.0.113.5"

    inside = _req(client=("192.0.2.10", 1))
    assert await scim.scim_principal(inside, "Bearer scim-token", session) == scim.ACTOR_LABEL
    with pytest.raises(scim.ScimError) as exc:
        await scim.scim_principal(inside, "Bearer wrong", session)
    assert exc.value.status == 401


# --- fleet endpoint ---------------------------------------------------------------------

async def test_fleet_endpoint_is_instance_admin_only(session, scene, monkeypatch):
    member = await _member(session, scene)
    with pytest.raises(HTTPException) as exc:
        await get_current_instance_admin(session=session, current_user=member)
    assert exc.value.status_code == 403

    admin = await get_current_instance_admin(session=session, current_user=scene.user)
    fake = FakeRedis()
    fake.kv["workers:hb:w1"] = '{"worker_id": "w1", "kind": "web", "step_types": []}'
    import time
    fake.z["workers:index"] = {"w1": time.time()}

    async def _fake_redis():
        return fake
    monkeypatch.setattr(fleet, "_redis", _fake_redis)
    out = await fleet.list_workers(admin)
    assert out["summary"]["alive"] == 1


async def test_local_heartbeat_needs_an_api_key(session, scene, monkeypatch):
    fake = FakeRedis()

    async def _fake_redis():
        return fake
    monkeypatch.setattr(fleet, "_redis", _fake_redis)
    body = fleet.LocalHeartbeat(worker_id="laptop", step_types=["goto"])
    with pytest.raises(HTTPException):
        await fleet.local_worker_heartbeat(body, principal=AuthPrincipal(user=scene.user))
    out, ws_id = await _mint_key(session, scene)
    key = await session.get(ApiKey, out.id)
    await fleet.local_worker_heartbeat(body, principal=AuthPrincipal(user=scene.user, api_key=key))
    assert f"workers:hb:local:{ws_id}:laptop" in fake.kv


# --- paging + maintenance windows ---------------------------------------------------------

@pytest.fixture
def vendor(monkeypatch):
    sent = []

    class R:
        status_code = 202
        text = ""
    monkeypatch.setattr(paging_svc, "_post",
                        lambda url, json, headers=None: sent.append((url, json)) or R())
    return sent


def _sync_session():
    from app.tasks.monitor_tasks import sync_engine
    return Session(sync_engine)


async def _channel(session, scene, **kw):
    body = paging_api.ChannelIn(name="oncall", kind="pagerduty", secret="RK-123", **kw)
    return await paging_api.create_channel(scene.project_id, body, _req(), session=session,
                                           principal=AuthPrincipal(user=scene.user))


async def test_channel_api_is_admin_only_and_never_returns_the_key(session, scene):
    member = await _member(session, scene)
    body = paging_api.ChannelIn(name="x", kind="pagerduty", secret="RK")
    with pytest.raises(HTTPException) as exc:
        await paging_api.create_channel(scene.project_id, body, _req(), session=session,
                                        principal=AuthPrincipal(user=member))
    assert exc.value.status_code == 403
    ch = await _channel(session, scene)
    assert ch["secret_set"] and "RK-123" not in str(ch)
    listed = await paging_api.list_channels(scene.project_id, session=session,
                                            principal=AuthPrincipal(user=scene.user))
    assert "RK-123" not in str(listed)
    with pytest.raises(HTTPException) as exc:
        await paging_api.create_channel(
            scene.project_id, paging_api.ChannelIn(name="y", kind="sms", secret="x"), _req(),
            session=session, principal=AuthPrincipal(user=scene.user))
    assert exc.value.status_code == 400


async def test_trigger_resolve_and_maintenance_suppression(session, scene, vendor):
    ch = await _channel(session, scene)
    key = paging_svc.monitor_dedup_key(1, None)
    with _sync_session() as s:
        # Resolve with nothing open: no call at all.
        assert paging_svc.page(s, project_id=scene.project_id, source="monitor",
                               action="resolve", dedup_key=key, summary="ok") == []
        out = paging_svc.page(s, project_id=scene.project_id, source="monitor",
                              action="trigger", dedup_key=key, summary="down")
        assert out[0]["outcome"] == "sent"
        out = paging_svc.page(s, project_id=scene.project_id, source="monitor",
                              action="resolve", dedup_key=key, summary="ok")
        assert out[0]["outcome"] == "sent"
    assert [b["event_action"] for _, b in vendor] == ["trigger", "resolve"]
    assert vendor[0][1]["routing_key"] == "RK-123"

    # A maintenance window active now suppresses — and records.
    now = datetime.utcnow()
    await paging_api.create_window(
        scene.project_id,
        paging_api.WindowIn(name="deploy", starts_at=now - timedelta(minutes=5),
                            ends_at=now + timedelta(hours=1)),
        _req(), session=session, principal=AuthPrincipal(user=scene.user))
    with _sync_session() as s:
        out = paging_svc.page(s, project_id=scene.project_id, source="monitor",
                              action="trigger", dedup_key=key, summary="down again")
        assert out[0]["outcome"] == "suppressed" and "deploy" in out[0]["detail"]
        # The suppressed trigger opened nothing, so the recovery sends nothing.
        assert paging_svc.page(s, project_id=scene.project_id, source="monitor",
                               action="resolve", dedup_key=key, summary="ok") == []
    assert len(vendor) == 2
    events = (await session.exec(select(PagingEvent).where(
        PagingEvent.project_id == scene.project_id))).all()
    assert [e.outcome for e in events].count("suppressed") == 1
    assert all("RK-123" not in (e.detail or "") for e in events)
    windows = await paging_api.list_windows(scene.project_id, session=session,
                                            principal=AuthPrincipal(user=scene.user))
    assert windows[0]["active_now"] is True
    assert ch["id"] == events[0].channel_id


async def test_vendor_failure_is_recorded_not_raised(session, scene, monkeypatch):
    class R:
        status_code = 400
        text = "Invalid routing key"
    monkeypatch.setattr(paging_svc, "_post", lambda *a, **k: R())
    await _channel(session, scene)
    with _sync_session() as s:
        out = paging_svc.page(s, project_id=scene.project_id, source="monitor",
                              action="trigger", dedup_key="k", summary="down")
    assert out[0]["outcome"] == "failed" and "HTTP 400" in out[0]["detail"]


# --- multi-region monitors ---------------------------------------------------------------

async def _monitor(session, scene, regions):
    sched = TestSchedule(name="Checkout monitor", project_id=scene.project_id,
                         test_suite_id=scene.root_id, cron_expression="*/5 * * * *",
                         is_monitor=True, regions=regions, alert_after_failures=1,
                         created_by_id=scene.user.id)
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return sched


async def _finished_run(session, scene, sched, region, status):
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=status,
                  agent_id=f"schedule:{sched.id}", triggered_by=RunTrigger.SCHEDULE,
                  region=region)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def test_each_region_has_its_own_state_and_page(session, scene, vendor):
    from app.tasks.monitor_tasks import evaluate_monitor_for_run
    await _channel(session, scene)
    sched = await _monitor(session, scene, ["eu", "us"])

    for region, status in (("eu", TestStatus.FAILED), ("us", TestStatus.PASSED)):
        run = await _finished_run(session, scene, sched, region, status)
        await asyncio.to_thread(evaluate_monitor_for_run, run.id)

    await session.refresh(sched)
    assert sched.region_states["eu"]["state"] == "down"
    assert sched.region_states["us"]["state"] == "up"
    assert sched.monitor_state == "down"                   # any region down
    assert [b["dedup_key"] for _, b in vendor] == [f"traceiq:monitor:{sched.id}:eu"]
    checks = (await session.exec(select(MonitorCheck.region).where(
        MonitorCheck.schedule_id == sched.id))).all()
    assert sorted(checks) == ["eu", "us"]

    run = await _finished_run(session, scene, sched, "eu", TestStatus.PASSED)
    await asyncio.to_thread(evaluate_monitor_for_run, run.id)
    await session.refresh(sched)
    assert sched.monitor_state == "up"
    assert [b["event_action"] for _, b in vendor] == ["trigger", "resolve"]

    from app.api.endpoints.schedules import _build_monitor_status
    status = await _build_monitor_status(sched, session)
    assert {r.region: r.state for r in status.regions} == {"eu": "up", "us": "up"}


async def test_unlabelled_monitor_behaves_as_before(session, scene, vendor):
    from app.tasks.monitor_tasks import evaluate_monitor_for_run
    sched = await _monitor(session, scene, None)
    run = await _finished_run(session, scene, sched, None, TestStatus.FAILED)
    await asyncio.to_thread(evaluate_monitor_for_run, run.id)
    await session.refresh(sched)
    assert sched.monitor_state == "down" and sched.last_alert_state == "down"
    assert sched.region_states is None


async def test_region_schedule_dispatches_one_run_per_region(session, scene, monkeypatch):
    from app.tasks import schedule_tasks
    from app import worker
    monkeypatch.setattr(worker.run_test_suite, "delay", lambda *a, **k: None)
    sched = TestSchedule(name="multi", project_id=scene.project_id, test_case_id=scene.root_cases[0],
                         cron_expression="* * * * *", regions=["eu", "ap"],
                         created_by_id=scene.user.id)
    session.add(sched)
    await session.commit()
    await schedule_tasks._dispatch_schedule(sched, session)
    runs = (await session.exec(select(TestRun).where(
        TestRun.agent_id == f"schedule:{sched.id}"))).all()
    assert sorted(r.region for r in runs) == ["ap", "eu"]


async def test_run_region_is_validated_on_create(session, scene):
    from app.api.endpoints import test_runs
    with pytest.raises(HTTPException) as exc:
        await test_runs.create_run(
            suite_id=scene.root_id, case_id=None, browser=None, device=None, tags=None,
            context=test_runs.RunCreateContext(region="eu west"),
            session=session, principal=AuthPrincipal(user=scene.user))
    assert exc.value.status_code == 400
    runs = await test_runs.create_run(
        suite_id=scene.root_id, case_id=scene.root_cases[0], browser=None, device=None,
        tags=None, context=test_runs.RunCreateContext(region="EU"),
        session=session, principal=AuthPrincipal(user=scene.user))
    run = runs[0] if isinstance(runs, list) else runs
    assert run.region == "eu"


# --- egress policy -----------------------------------------------------------------------

async def test_network_policy_endpoint_and_dispatch(session, scene, monkeypatch):
    member = await _member(session, scene)
    with pytest.raises(HTTPException) as exc:
        await paging_api.put_network_policy(scene.project_id, {"enforce": True}, _req(),
                                            session=session, principal=AuthPrincipal(user=member))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await paging_api.put_network_policy(scene.project_id, {"allowed_hosts": ["bad host"]},
                                            _req(), session=session,
                                            principal=AuthPrincipal(user=scene.user))
    assert exc.value.status_code == 422

    _settings(monkeypatch, EGRESS_ALLOWLIST_ENFORCED=False, EGRESS_ALLOWED_HOSTS=["sso.corp.com"],
              ALLOW_PRIVATE_NETWORK_TARGETS=False)
    out = await paging_api.put_network_policy(
        scene.project_id, {"enforce": True, "allowed_hosts": ["https://Shop.example.com"],
                           "allow_private": True},
        _req(), session=session, principal=AuthPrincipal(user=scene.user))
    assert out["stored"]["allowed_hosts"] == ["shop.example.com"]
    assert out["effective"] == {"enforce": True, "allowed_hosts": ["shop.example.com", "sso.corp.com"],
                                "allow_private": False}          # instance ceiling
    from app.worker import _load_egress_policy
    with _sync_session() as s:
        dispatched = _load_egress_policy(scene.project_id, {"base_url": "https://api.example.com"}, s)
    assert dispatched["allowed_hosts"][-1] == "api.example.com"
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "project", AuditLog.entity_id == scene.project_id))).all()
    assert "update_network_policy" in actions
