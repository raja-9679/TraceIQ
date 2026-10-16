"""WP12b reports against a real Postgres: deployment ingest access control,
DORA from stored rows, SLO from MonitorCheck rows, flake snapshot and vendor
roll-up idempotency, the flake-pattern SQL.

    TRACEIQ_SCRATCH_DB=traceiq_wp12b ./run-tests-live.sh tests/integration/test_reliability_reports_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlmodel import Session, func, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import dora as dora_api, reliability_reports as rr   # noqa: E402
from app.core.auth import AuthPrincipal                           # noqa: E402
from app.core.config import db_url_for, settings                 # noqa: E402
from app.models import (                                          # noqa: E402
    ApiKey, AuditLog, Deployment, FlakeCaseScoreDaily, FlakeDailySnapshot, FlakeRecord,
    Incident, MonitorCheck, ProjectEnvironment, TestCaseResult, TestRun, TestSchedule,
    TestStatus, User, UserProjectAccess, VendorHealthDaily,
)
from app.tasks import reliability_tasks                           # noqa: E402

from tests.integration.test_targeted_runs_db import (             # noqa: E402,F401
    no_queue, scene, session,
)


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


def _sync():
    return create_engine(db_url_for(settings.DATABASE_URL, sync=True))


async def _member(session, scene, email, level) -> AuthPrincipal:
    u = User(email=email, full_name=email, hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    session.add(UserProjectAccess(user_id=u.id, project_id=scene.project_id, access_level=level))
    await session.commit()
    return AuthPrincipal(user=u, api_key=None)


async def _env(session, scene, name="prod") -> ProjectEnvironment:
    env = ProjectEnvironment(project_id=scene.project_id, name=name, base_url="https://shop.example.com")
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


async def _post(session, scene, principal, **body):
    return await dora_api.record_deployment(scene.project_id, dora_api.DeploymentIn(**body), Response(),
                                            principal=principal, session=session)


# --- deployments: access control + idempotency ---------------------------------

async def test_deployment_ingest_access_control(session, scene):
    await _env(session, scene)
    editor = await _member(session, scene, "ed@corp.example.com", "editor")
    viewer = await _member(session, scene, "vw@corp.example.com", "viewer")

    out = await _post(session, scene, editor, git_commit="abc1234", environment="prod", version="1.2.0")
    assert out["created"] and out["environment"] == "prod" and out["created_by_id"] == editor.user.id

    with pytest.raises(HTTPException) as exc:
        await _post(session, scene, viewer, git_commit="abc1234")
    assert exc.value.status_code == 403

    # A key scoped to another project is refused even though its user is editor here.
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await _post(session, scene, AuthPrincipal(user=editor.user, api_key=key), git_commit="abc1234")
    assert exc.value.status_code == 403

    # ...and a key scoped to this project is accepted.
    from app.models import Project
    ws_id = (await session.get(Project, scene.project_id)).workspace_id
    session.add(ApiKey(name="ci2", workspace_id=ws_id, project_id=scene.project_id, prefix="tiq_y",
                       hashed_key="h2", created_by_id=scene.user.id))
    await session.commit()
    own_key = (await session.exec(select(ApiKey).where(ApiKey.prefix == "tiq_y"))).one()
    ok = await _post(session, scene, AuthPrincipal(user=editor.user, api_key=own_key), git_commit="d00d")
    assert ok["api_key_id"] == own_key.id

    # Viewers can read, not delete.
    listed = await dora_api.list_deployments(scene.project_id, limit=50, environment_id=None,
                                             principal=viewer, session=session)
    assert len(listed) == 2
    with pytest.raises(HTTPException) as exc:
        await dora_api.delete_deployment(out["id"], principal=viewer, session=session)
    assert exc.value.status_code == 403

    # An outsider can't even see the project.
    outsider = AuthPrincipal(user=User(id=999999, email="x@y", full_name="x", hashed_password="x"))
    with pytest.raises(HTTPException) as exc:
        await dora_api.dora_metrics(scene.project_id, days=30, environment_id=None,
                                    principal=outsider, session=session)
    assert exc.value.status_code == 403

    actions = (await session.exec(select(AuditLog.action).where(AuditLog.entity_type == "deployment"))).all()
    assert actions.count("create") == 2


async def test_external_id_makes_ci_retries_idempotent_and_env_must_be_in_project(session, scene):
    await _post(session, scene, _me(scene), external_id="gh-run-7", git_commit="a1", status="failed")
    again = await _post(session, scene, _me(scene), external_id="gh-run-7", git_commit="a1", status="success")
    assert again["created"] is False and again["status"] == "success"
    n = (await session.exec(select(func.count()).select_from(Deployment))).one()
    assert n == 1

    other_env = ProjectEnvironment(project_id=scene.other_project_id, name="prod")
    session.add(other_env)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _post(session, scene, _me(scene), environment_id=other_env.id)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await _post(session, scene, _me(scene), deployed_at=datetime.utcnow() + timedelta(days=1))
    assert exc.value.status_code == 422


async def test_dora_from_stored_rows(session, scene):
    env = await _env(session, scene)
    now = datetime.utcnow()
    session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, git_commit="c0ffee1",
                        status=TestStatus.PASSED, created_at=now - timedelta(days=4)))
    await session.commit()
    d1 = await _post(session, scene, _me(scene), git_commit="c0ffee1", environment="prod",
                     deployed_at=now - timedelta(days=3))
    await _post(session, scene, _me(scene), git_commit="beef", environment="prod",
                deployed_at=now - timedelta(days=1), commit_at=now - timedelta(days=1, hours=2))
    session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, environment_id=env.id,
                        status=TestStatus.FAILED, created_at=now - timedelta(days=3) + timedelta(hours=1)))
    session.add(Incident(project_id=scene.project_id, title="checkout down", fingerprint="f",
                         started_at=now - timedelta(hours=20), resolved_at=now - timedelta(hours=19)))
    await session.commit()

    out = await dora_api.dora_metrics(scene.project_id, days=30, environment_id=None,
                                      principal=_me(scene), session=session)
    assert out["deployment_frequency"]["deployments"] == 2
    assert out["lead_time"]["sources"] == {"commit_at": 1, "first_run": 1}
    assert out["change_failure_rate"]["failed_deployments"] == 2
    causes = {d["id"]: d["failure_causes"] for d in out["deployments"]}
    assert causes[d1["id"]] == ["failed_run"]
    assert out["time_to_restore"]["median_hours"] == pytest.approx(1.0, abs=0.01)
    assert out["inputs"]["failed_runs"] == 1

    empty = await dora_api.dora_metrics(scene.other_project_id, days=30, environment_id=None,
                                        principal=_me(scene), session=session)
    assert "No deployments recorded" in empty["deployment_frequency"]["missing"]


# --- SLO -------------------------------------------------------------------------

async def _monitor(session, scene, **kw) -> TestSchedule:
    m = TestSchedule(name="Checkout monitor", project_id=scene.project_id, test_suite_id=scene.root_id,
                     cron_expression="*/5 * * * *", is_monitor=True, **kw)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


async def test_slo_report_from_monitor_checks(session, scene):
    m = await _monitor(session, scene, regions=["eu", "us"])
    now = datetime.utcnow()
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.PASSED,
                  duration_ms=1200.0)
    session.add(run)
    await session.flush()
    for i in range(100):
        session.add(MonitorCheck(schedule_id=m.id, run_id=run.id if i % 2 == 0 else None,
                                 status="passed" if i % 25 else "failed", is_up=bool(i % 25),
                                 checked_at=now - timedelta(hours=i + 1), region="eu" if i % 2 else "us"))
    session.add(MonitorCheck(schedule_id=m.id, status="failed", is_up=False,
                             checked_at=now - timedelta(days=60)))
    await session.commit()

    viewer = await _member(session, scene, "vw@corp.example.com", "viewer")
    rep = await rr.monitor_slo(m.id, window_days=None, principal=viewer, session=session)
    assert rep["window_days"] == 30 and rep["checks"] == 100 and rep["bad"] == 4
    assert rep["target_is_default"] and rep["status"] == "breached"
    assert rep["latency"]["checks_with_latency"] == 50 and rep["latency"]["p95_ms"] == 1200.0
    assert {r["region"] for r in rep["regions"]} == {"eu", "us"}

    with pytest.raises(HTTPException) as exc:
        await rr.set_monitor_slo(m.id, rr.SloTargets(availability_target=95), principal=viewer,
                                 session=session)
    assert exc.value.status_code == 403
    out = await rr.set_monitor_slo(m.id, rr.SloTargets(availability_target=95, latency_p95_ms=1500,
                                                       window_days=7),
                                   principal=_me(scene), session=session)
    assert out["window_days"] == 7 and out["target_pct"] == 95 and out["status"] == "meeting"
    assert out["latency"]["met"] is True
    with pytest.raises(HTTPException) as exc:
        await rr.monitor_slo(m.id, window_days=14, principal=_me(scene), session=session)
    assert exc.value.status_code == 422

    listing = await rr.project_slos(scene.project_id, window_days=90, principal=_me(scene), session=session)
    assert [r["schedule_id"] for r in listing["monitors"]] == [m.id]
    assert listing["monitors"][0]["checks"] == 101


# --- flake snapshots + patterns ---------------------------------------------------

async def _results(session, scene, *, browser="chromium", flaky=False, status=TestStatus.PASSED,
                   created_at=None, step_type=None, env_id=None):
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, browser=browser,
                  status=status, environment_id=env_id, created_at=created_at or datetime.utcnow())
    session.add(run)
    await session.flush()
    payload = {"step_evidence": [{"index": 0, "type": "goto", "status": "passed"},
                                 {"index": 1, "type": step_type, "status": "failed"}]} if step_type else None
    session.add(TestCaseResult(test_run_id=run.id, test_name="cart", status=status, duration_ms=1,
                               is_flaky=flaky, result_payload=payload))
    await session.commit()
    return run


async def test_flake_snapshot_is_idempotent(session, scene):
    a, b = scene.root_cases
    session.add_all([FlakeRecord(test_case_id=a, flake_score=0.5, is_quarantined=True, sample_count=10),
                     FlakeRecord(test_case_id=a, step_id="s1", flake_score=0.7, sample_count=5),
                     FlakeRecord(test_case_id=b, flake_score=0.1, sample_count=10)])
    await session.commit()
    await _results(session, scene, flaky=True)
    await _results(session, scene, status=TestStatus.FAILED)

    engine = _sync()
    with Session(engine) as s:
        reliability_tasks.run_flake_snapshots(s)
    with Session(engine) as s:
        reliability_tasks.run_flake_snapshots(s)   # second tick the same day
    engine.dispose()

    snaps = (await session.exec(select(FlakeDailySnapshot).where(
        FlakeDailySnapshot.project_id == scene.project_id))).all()
    assert len(snaps) == 1
    s0 = snaps[0]
    assert (s0.flaky_cases, s0.quarantined_cases) == (1, 1)
    assert (s0.results_total, s0.results_flaky, s0.results_failed) == (2, 1, 1)
    cases = (await session.exec(select(FlakeCaseScoreDaily).where(
        FlakeCaseScoreDaily.project_id == scene.project_id))).all()
    assert sorted((c.test_case_id, c.flake_score) for c in cases) == [(a, 0.7), (b, 0.1)]

    trend = await rr.flake_trend(scene.project_id, days=30, top=5, principal=_me(scene), session=session)
    assert len(trend["daily"]) == 1 and trend["daily"][0]["flaky_result_pct"] == 50.0
    assert [c["test_case_id"] for c in trend["cases"]] == [a, b]
    assert trend["missing"] is None


async def test_flake_patterns_sql(session, scene):
    env = await _env(session, scene)
    base = datetime.utcnow().replace(hour=3, minute=0, second=0, microsecond=0) - timedelta(days=1)
    await _results(session, scene, browser="firefox", flaky=True, created_at=base, step_type="click", env_id=env.id)
    await _results(session, scene, browser="firefox", status=TestStatus.FAILED, created_at=base, step_type="click")
    await _results(session, scene, browser="chromium", created_at=base + timedelta(hours=10))
    await _results(session, scene, browser="chromium", status=TestStatus.ERROR, created_at=base)  # no evidence

    out = await rr.flake_patterns_report(scene.project_id, days=30, principal=_me(scene), session=session)
    assert out["results"] == 4 and out["flaky_results"] == 1
    browsers = {b["key"]: b for b in out["by_browser"]}
    assert browsers["firefox"]["flaky_pct"] == 50.0 and browsers["chromium"]["failed"] == 1
    assert out["by_hour_utc"][3]["total"] == 3 and out["by_hour_utc"][13]["total"] == 1
    envs = {b["label"]: b for b in out["by_environment"]}
    assert envs["prod"]["flaky"] == 1 and envs["default (no environment)"]["total"] == 3
    steps = {b["step_type"]: b for b in out["by_failing_step_type"]}
    assert steps["click"] == {"step_type": "click", "flaky": 1, "failed": 1, "total": 2}
    assert steps["unknown"]["failed"] == 1


# --- vendor health ------------------------------------------------------------------

async def test_vendor_rollup_is_idempotent_and_strips_urls(session, scene):
    events = [
        {"url": "https://shop.example.com/", "resourceType": "document", "status": 200, "duration": 100, "testCaseId": 1},
        {"url": "https://js.stripe.com/v3/?key=pk_live_SECRET", "status": 200, "duration": 80},
        {"url": "https://api.stripe.com/v1/tokens?card=4242", "status": 502, "duration": 900},
    ]
    failed = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.FAILED,
                     network_events=events)
    passed = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.PASSED,
                     network_events=events[:2])
    session.add_all([failed, passed])
    await session.flush()
    session.add_all([TestCaseResult(test_run_id=failed.id, test_name="pay", status=TestStatus.FAILED, duration_ms=1),
                     TestCaseResult(test_run_id=passed.id, test_name="pay", status=TestStatus.PASSED, duration_ms=1)])
    await session.commit()

    engine = _sync()
    for _ in range(2):
        with Session(engine) as s:
            reliability_tasks.run_vendor_aggregation(s, days_back=1)
    engine.dispose()

    rows = (await session.exec(select(VendorHealthDaily).where(
        VendorHealthDaily.project_id == scene.project_id))).all()
    assert [r.vendor for r in rows] == ["stripe.com"]
    r = rows[0]
    assert (r.requests, r.errors, r.runs, r.runs_with_errors) == (3, 1, 2, 1)
    assert (r.failed_results, r.failed_results_vendor_errors) == (1, 1)
    assert (r.passed_results, r.passed_results_vendor_errors) == (1, 0)
    assert set(r.hosts) == {"js.stripe.com", "api.stripe.com"}

    out = await rr.dependency_health(scene.project_id, days=30, principal=_me(scene), session=session)
    v = out["vendors"][0]
    assert v["name"] == "Stripe" and v["correlation"]["failed_with_vendor_errors_pct"] == 100.0
    assert v["correlation"]["passed_with_vendor_errors_pct"] == 0.0
    blob = repr(out)
    assert "SECRET" not in blob and "?" not in blob and "/v1/tokens" not in blob

    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await rr.dependency_health(scene.project_id, days=30,
                                   principal=AuthPrincipal(user=scene.user, api_key=key), session=session)
    assert exc.value.status_code == 403
