"""Versioned quality-gate policies against a real Postgres (WP15f L4).

    ./run-tests-live.sh tests/integration/test_gate_policy_versions_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api.endpoints import quality                        # noqa: E402
from app.core.auth import AuthPrincipal                      # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    AuditLog, GatePolicyVersion, Project, QualityGatePolicy, Tenant, TestRun, TestStatus, TestSuite,
    User, UserSystemRole, UserWorkspace, Workspace,
)
from app.services import gate_policy_versions as gpv          # noqa: E402
from app.services.rbac_service import rbac_service           # noqa: E402


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace, auditlog CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session):
    user = User(email="qa@corp.example.com", full_name="QA", hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=user.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=user.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
    ])
    project, other = Project(name="Shop", workspace_id=ws.id), Project(name="Other", workspace_id=ws.id)
    session.add_all([project, other])
    await session.flush()
    suite, other_suite = TestSuite(name="s", project_id=project.id), TestSuite(name="o", project_id=other.id)
    session.add_all([suite, other_suite])
    await session.flush()

    def run(st, proj=project, s=suite):
        return TestRun(test_suite_id=s.id, project_id=proj.id, status=st, git_commit="abc123")
    runs = [run(TestStatus.PASSED), run(TestStatus.FAILED), run(TestStatus.CANCELLED)]
    foreign = run(TestStatus.PASSED, other, other_suite)
    session.add_all(runs + [foreign])
    await session.commit()
    return SimpleNamespace(user=user, project_id=project.id, run_ids=[r.id for r in runs],
                           cancelled_id=runs[2].id, foreign_run_id=foreign.id)


def _principal(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _gate(session, scene, **kw):
    params = dict(git_commit=None, git_branch=None, policy_version=None, run_ids=None)
    params.update(kw)
    return await quality.evaluate_quality_gate(
        scene.project_id, session=session, principal=_principal(scene), **params)


async def _versions(session, scene):
    return (await session.exec(select(GatePolicyVersion).where(
        GatePolicyVersion.project_id == scene.project_id).order_by(GatePolicyVersion.version))).all()


async def test_first_evaluation_records_version_1_and_reuses_it(session, scene):
    r1 = await _gate(session, scene, git_commit="abc123")
    r2 = await _gate(session, scene, git_commit="abc123")
    assert r1.policy_version == r2.policy_version == 1
    assert r1.policy_sha256 == gpv.sha256_of(QualityGatePolicy().model_dump())
    assert len(await _versions(session, scene)) == 1


async def test_cancelled_runs_are_listed_not_counted(session, scene):
    r = await _gate(session, scene, git_commit="abc123")
    assert scene.cancelled_id in r.ignored_run_ids
    assert scene.cancelled_id not in r.evaluated_run_ids
    pass_rate = next(c for c in r.checks if c.name == "pass_rate")
    assert pass_rate.actual == "50.0%"                       # 1 passed of 2 with a verdict
    assert "cancelled / timed-out" in (pass_rate.detail or "")


async def test_policy_change_is_a_new_version_and_old_verdict_replays(session, scene):
    before = await _gate(session, scene, git_commit="abc123")
    assert before.passed is False and before.policy_version == 1      # default needs 100%

    await quality.set_quality_gate_policy(
        scene.project_id, policy=QualityGatePolicy(min_pass_rate=50.0),
        session=session, current_user=scene.user)
    versions = await _versions(session, scene)
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].created_by_id == scene.user.id and versions[1].policy["min_pass_rate"] == 50.0
    audit = (await session.exec(select(AuditLog).where(
        AuditLog.action == "quality_gate_policy_update"))).all()
    assert audit[-1].changes["policy_version"] == 2

    now = await _gate(session, scene, git_commit="abc123")
    assert now.passed is True and now.policy_version == 2

    # The old decision, reproduced from what it reported.
    replay = await _gate(session, scene, policy_version=before.policy_version,
                         run_ids=before.evaluated_run_ids)
    assert replay.passed is False and replay.policy_version == 1
    assert replay.policy_sha256 == before.policy_sha256
    assert replay.evaluated_run_ids == before.evaluated_run_ids

    # Saving the same policy again does not mint a version.
    await quality.set_quality_gate_policy(
        scene.project_id, policy=QualityGatePolicy(min_pass_rate=50.0),
        session=session, current_user=scene.user)
    assert len(await _versions(session, scene)) == 2

    listed = await quality.list_quality_gate_policy_versions(
        scene.project_id, session=session, principal=_principal(scene))
    assert [v["version"] for v in listed["versions"]] == [2, 1]
    one = await quality.get_quality_gate_policy_version(
        scene.project_id, 1, session=session, principal=_principal(scene))
    assert one["policy"]["min_pass_rate"] == 100.0


async def test_unknown_version_and_foreign_runs_are_404(session, scene):
    with pytest.raises(HTTPException) as e:
        await _gate(session, scene, policy_version=99)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await _gate(session, scene, run_ids=[scene.run_ids[0], scene.foreign_run_id])
    assert e.value.status_code == 404


async def test_changed_default_becomes_a_new_version(session, scene, monkeypatch):
    await _gate(session, scene, git_commit="abc123")
    # A release that changes a built-in default changes what the gate uses;
    # the next verdict must name a new version, not claim the old one.
    from app.services import gate_policy_versions as mod
    monkeypatch.setattr(mod, "effective_policy", lambda project: QualityGatePolicy(min_pass_rate=90.0))
    r = await _gate(session, scene, git_commit="abc123")
    assert r.policy_version == 2
