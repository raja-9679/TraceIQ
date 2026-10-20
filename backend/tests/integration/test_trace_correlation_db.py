"""Trace correlation (WP16a) against a real Postgres: endpoint access control,
project-scoped keys, the project setting and the dispatch host set.

    ./run-tests-live.sh tests/integration/test_trace_correlation_db.py -q

The trace backend itself is faked (mock-verified only)."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import trace_correlation as api                   # noqa: E402
from app.core.auth import AuthPrincipal                        # noqa: E402
from app.core.config import db_url_for, settings               # noqa: E402
from app.models import (                                       # noqa: E402
    AuditLog, Project, ProjectEnvironment, TestCaseResult, TestRun, TestStatus, User,
    UserProjectAccess,
)
from app.services import trace_backends, trace_correlation     # noqa: E402

from tests.integration.test_targeted_runs_db import (          # noqa: E402,F401
    scene, session,
)

TID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"
REQ = SimpleNamespace(headers={}, client=None)


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _result(session, project_id, suite_id, **run_kw):
    run = TestRun(test_suite_id=suite_id, project_id=project_id, status=TestStatus.FAILED,
                  browser="chromium", **run_kw)
    session.add(run)
    await session.flush()
    res = TestCaseResult(test_run_id=run.id, test_name="cart", status=TestStatus.FAILED,
                         duration_ms=10, result_payload={"step_evidence": [
                             {"id": "s0", "index": 0, "type": "goto", "status": "failed",
                              "trace": {"trace_id": TID, "span_id": SPAN, "requests": 1}}]})
    session.add(res)
    await session.commit()
    return run, res


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    trace_backends.cache_clear()
    calls = []
    monkeypatch.setattr(trace_backends, "configured",
                        lambda: trace_backends.BackendConfig("jaeger", "http://10.9.9.9:16686"))

    def fetch(cfg, tid, policy):
        calls.append(tid)
        return [trace_backends.Span("a000000000000001", SPAN, "api", "GET /", 0, 1000,
                                    True, "boom", {})]
    monkeypatch.setattr(trace_backends, "fetch_trace", fetch)
    yield calls
    trace_backends.cache_clear()


async def test_member_sees_spans_and_the_answer_is_cached(session, scene, fake_backend):
    run, res = await _result(session, scene.project_id, scene.root_id)
    out = await api.result_traces(run.id, res.id, principal=_me(scene), session=session)
    assert out["steps"][0]["status"] == "ok" and out["steps"][0]["error_spans"] == 1
    again = await api.result_traces(run.id, res.id, principal=_me(scene), session=session)
    assert again == out and fake_backend == [TID]


async def test_viewer_reads_outsider_and_wrong_key_refused(session, scene, fake_backend):
    run, res = await _result(session, scene.project_id, scene.root_id)
    viewer = User(email="v@corp.example.com", full_name="V", hashed_password="x")
    outsider = User(email="x@else.example.com", full_name="X", hashed_password="x")
    session.add_all([viewer, outsider])
    await session.flush()
    session.add(UserProjectAccess(user_id=viewer.id, project_id=scene.project_id, access_level="viewer"))
    await session.commit()

    out = await api.result_traces(run.id, res.id, principal=AuthPrincipal(user=viewer, api_key=None),
                                  session=session)
    assert out["status"] == "ok"
    trace_backends.cache_clear()
    for principal in (AuthPrincipal(user=outsider, api_key=None),
                      AuthPrincipal(user=scene.user,
                                    api_key=SimpleNamespace(id=1, project_id=scene.other_project_id))):
        with pytest.raises(HTTPException) as exc:
            await api.result_traces(run.id, res.id, principal=principal, session=session)
        assert exc.value.status_code == 403
    # A cached answer is never served before the access check.
    await api.result_traces(run.id, res.id, principal=_me(scene), session=session)
    with pytest.raises(HTTPException):
        await api.result_traces(run.id, res.id, principal=AuthPrincipal(user=outsider, api_key=None),
                                session=session)
    # A project-scoped key for the right project is fine.
    ok = await api.result_traces(run.id, res.id, session=session, principal=AuthPrincipal(
        user=scene.user, api_key=SimpleNamespace(id=2, project_id=scene.project_id)))
    assert ok["status"] == "ok"


async def test_result_of_another_run_is_404(session, scene):
    run, _ = await _result(session, scene.project_id, scene.root_id)
    other_run, other_res = await _result(session, scene.other_project_id, scene.root_id)
    with pytest.raises(HTTPException) as exc:
        await api.result_traces(run.id, other_res.id, principal=_me(scene), session=session)
    assert exc.value.status_code == 404


async def test_local_worker_results_are_never_looked_up(session, scene, fake_backend):
    run, res = await _result(session, scene.project_id, scene.root_id, local_worker_id="laptop")
    out = await api.result_traces(run.id, res.id, principal=_me(scene), session=session)
    assert out["status"] == "no_telemetry" and fake_backend == []


async def test_settings_admin_only_and_audited(session, scene):
    got = await api.get_trace_settings(scene.project_id, session=session, principal=_me(scene))
    assert got["enabled"] is False and got["backend_configured"] is True
    viewer = User(email="v2@corp.example.com", full_name="V", hashed_password="x")
    session.add(viewer)
    await session.flush()
    session.add(UserProjectAccess(user_id=viewer.id, project_id=scene.project_id, access_level="viewer"))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await api.put_trace_settings(scene.project_id, api.TraceSettingsIn(enabled=True), REQ,
                                     session=session, principal=AuthPrincipal(user=viewer, api_key=None))
    assert exc.value.status_code == 403

    out = await api.put_trace_settings(scene.project_id, api.TraceSettingsIn(enabled=True), REQ,
                                       session=session, principal=_me(scene))
    assert out["enabled"] is True and out["cross_origin"] is False
    out = await api.put_trace_settings(scene.project_id, api.TraceSettingsIn(cross_origin=True), REQ,
                                       session=session, principal=_me(scene))
    assert out == {**out, "enabled": True, "cross_origin": True}      # omitted field kept
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "project", AuditLog.entity_id == scene.project_id))).all()
    assert actions.count("update_trace_settings") == 2


async def test_dispatch_host_set_is_this_projects_hosts_only(session, scene):
    session.add_all([
        ProjectEnvironment(project_id=scene.project_id, name="stg", base_url="https://shop.example"),
        ProjectEnvironment(project_id=scene.other_project_id, name="x", base_url="https://other.example"),
    ])
    project = await session.get(Project, scene.project_id)
    project.trace_settings = {"enabled": True}
    project.security_settings = {"enabled": True, "allowed_domains": ["*.api.shop.example"]}
    session.add(project)
    await session.commit()
    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    try:
        with Session(engine) as sync:
            p = sync.get(Project, scene.project_id)
            out = trace_correlation.for_dispatch(p, sync, "standard")
            assert out["hosts"] == ["*.api.shop.example", "shop.example"]
            assert trace_correlation.for_dispatch(p, sync, "none") is None
            assert trace_correlation.for_dispatch(sync.get(Project, scene.other_project_id), sync,
                                                  "standard") is None
    finally:
        engine.dispose()
