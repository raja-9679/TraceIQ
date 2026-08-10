"""Separation of duties on the proposal queue — workstream F4, against Postgres.

`tests/test_proposal_policy.py` covers the decision. This covers whether the
endpoints actually honour it, which is the part that was broken: creating and
accepting a proposal required the same role, and nothing compared the two
identities, so an editor could file a proposal and accept it themselves.

    ./run-tests-live.sh tests/integration/test_proposal_separation_db.py -q
"""
from __future__ import annotations

import os
from typing import NamedTuple

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

from app.core.config import db_url_for, settings                    # noqa: E402
from app.models import (                                             # noqa: E402
    CaseProposal, CaseProposalAction, CaseProposalCreate, Project, Role, Tenant,
    TestCase, TestSuite, User, UserProjectAccess, UserWorkspace, Workspace,
)
from app.services import instance_settings as insvc                  # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False),
                                 future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text(_WIPE))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


def _configure(monkeypatch, **overrides):
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: dict(overrides))
    insvc.invalidate_cache()


class Principal(NamedTuple):
    """Stands in for AuthPrincipal. The endpoints only read these three."""
    user: User
    agent_id: str | None = None
    agent_session_id: str | None = None
    is_api_caller: bool = False


class Fixture(NamedTuple):
    workspace_id: int
    project_id: int
    suite_id: int
    alice: User
    bob: User


async def _scene(session, *, require_separate_approver=False) -> Fixture:
    """Two editors on one project — the situation the control exists for."""
    alice = User(email="alice@corp.example.com", full_name="Alice",
                 hashed_password="x", is_verified=True)
    bob = User(email="bob@corp.example.com", full_name="Bob",
               hashed_password="x", is_verified=True)
    session.add_all([alice, bob])
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=alice.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp", tenant_id=tenant.id,
                   require_separate_approver=require_separate_approver)
    session.add(ws)
    await session.flush()
    project = Project(name="Web", workspace_id=ws.id)
    session.add(project)
    await session.flush()
    suite = TestSuite(name="Checkout", project_id=project.id)
    session.add(suite)
    await session.flush()

    editor = (await session.exec(
        select(Role).where(Role.name == "Project Editor"))).first()
    for user in (alice, bob):
        session.add(UserWorkspace(user_id=user.id, workspace_id=ws.id, role="member"))
        session.add(UserProjectAccess(user_id=user.id, project_id=project.id,
                                      access_level="editor", role_id=editor.id))
    await session.flush()
    await session.commit()
    return Fixture(workspace_id=ws.id, project_id=project.id, suite_id=suite.id,
                   alice=alice, bob=bob)


async def _propose(session, scene: Fixture, author: User,
                   confidence: float = 0.0) -> CaseProposal:
    from app.api.agent_ownership import create_proposal

    body = CaseProposalCreate(
        project_id=scene.project_id, test_suite_id=scene.suite_id,
        action=CaseProposalAction.CREATE,
        payload={"name": "new case", "steps": [{"type": "goto", "value": "/"}]},
        ai_confidence=confidence)
    read = await create_proposal(body=body, principal=Principal(user=author),
                                session=session)
    return await session.get(CaseProposal, read.id)


# --- Attribution -------------------------------------------------------------

async def test_a_proposal_records_who_filed_it(session, monkeypatch):
    # Without this there is nothing for the accept path to compare, which is why
    # the check could not have existed before.
    _configure(monkeypatch)
    scene = await _scene(session)
    proposal = await _propose(session, scene, scene.alice)
    assert proposal.created_by_id == scene.alice.id


async def test_an_api_key_proposal_is_attributed_to_its_owner(session, monkeypatch):
    # "Propose with my key, accept in the UI" must not be a way around the
    # queue: an agent acting under a developer's key IS that developer.
    _configure(monkeypatch)
    scene = await _scene(session)
    from app.api.agent_ownership import create_proposal
    body = CaseProposalCreate(
        project_id=scene.project_id, test_suite_id=scene.suite_id,
        action=CaseProposalAction.CREATE,
        payload={"name": "agent case", "steps": []})
    read = await create_proposal(
        body=body,
        principal=Principal(user=scene.alice, agent_id="claude", is_api_caller=True),
        session=session)
    proposal = await session.get(CaseProposal, read.id)
    assert proposal.created_by_id == scene.alice.id


# --- Enforcement -------------------------------------------------------------

async def test_self_approval_is_allowed_by_default(session, monkeypatch):
    # Existing single-person installs must keep working — the control is opt-in.
    _configure(monkeypatch)
    scene = await _scene(session)
    proposal = await _propose(session, scene, scene.alice)

    from app.api.agent_ownership import accept_proposal
    read = await accept_proposal(proposal_id=proposal.id, note=None,
                                 principal=Principal(user=scene.alice),
                                 session=session)
    assert read.status == "accepted"


async def test_self_approval_is_refused_when_the_workspace_requires_it(session, monkeypatch):
    _configure(monkeypatch)
    scene = await _scene(session, require_separate_approver=True)
    proposal = await _propose(session, scene, scene.alice)
    # Captured before the rollback below: rollback expires every instance in the
    # session, so touching proposal.id afterwards raises MissingGreenlet.
    proposal_id = proposal.id

    from app.api.agent_ownership import accept_proposal
    with pytest.raises(HTTPException) as raised:
        await accept_proposal(proposal_id=proposal_id, note=None,
                              principal=Principal(user=scene.alice), session=session)
    assert raised.value.status_code == 403

    await session.rollback()
    still = await session.get(CaseProposal, proposal_id)
    assert still.status == "pending", "a refused approval must not apply the change"


async def test_a_colleague_can_approve(session, monkeypatch):
    _configure(monkeypatch)
    scene = await _scene(session, require_separate_approver=True)
    proposal = await _propose(session, scene, scene.alice)

    from app.api.agent_ownership import accept_proposal
    read = await accept_proposal(proposal_id=proposal.id, note="looks right",
                                 principal=Principal(user=scene.bob),
                                 session=session)
    assert read.status == "accepted"
    created = (await session.exec(
        select(TestCase).where(TestCase.project_id == scene.project_id))).all()
    assert len(created) == 1, "the approved change should actually land"


async def test_the_instance_policy_overrides_an_opted_out_workspace(session, monkeypatch):
    _configure(monkeypatch, REQUIRE_SEPARATE_APPROVER="true")
    scene = await _scene(session, require_separate_approver=False)
    proposal = await _propose(session, scene, scene.alice)

    from app.api.agent_ownership import accept_proposal
    with pytest.raises(HTTPException) as raised:
        await accept_proposal(proposal_id=proposal.id, note=None,
                              principal=Principal(user=scene.alice), session=session)
    assert raised.value.status_code == 403


async def test_rejecting_your_own_proposal_is_still_allowed(session, monkeypatch):
    # Withdrawal is harmless. Gating it would leave proposals stuck with nobody
    # able to clear them.
    _configure(monkeypatch, REQUIRE_SEPARATE_APPROVER="true")
    scene = await _scene(session, require_separate_approver=True)
    proposal = await _propose(session, scene, scene.alice)

    from app.api.agent_ownership import reject_proposal
    read = await reject_proposal(proposal_id=proposal.id, note="never mind",
                                 principal=Principal(user=scene.alice),
                                 session=session)
    assert read.status == "rejected"


# --- Auto-apply kill switch --------------------------------------------------

async def test_auto_apply_still_works_when_not_disabled(session, monkeypatch):
    _configure(monkeypatch)
    scene = await _scene(session)
    workspace = await session.get(Workspace, scene.workspace_id)
    workspace.auto_apply_threshold = 0.8
    session.add(workspace)
    await session.commit()

    proposal = await _propose(session, scene, scene.alice, confidence=0.95)
    assert proposal.status == "accepted"
    assert proposal.decided_by_id is None


async def test_the_instance_kill_switch_stops_auto_apply(session, monkeypatch):
    # An auto-applied change has no human reviewer at any point. An operator has
    # to be able to switch that off instance-wide, whatever each workspace's
    # threshold says.
    _configure(monkeypatch, AUTO_APPLY_DISABLED="true")
    scene = await _scene(session)
    workspace = await session.get(Workspace, scene.workspace_id)
    workspace.auto_apply_threshold = 0.8
    session.add(workspace)
    await session.commit()

    proposal = await _propose(session, scene, scene.alice, confidence=0.99)
    assert proposal.status == "pending"


# --- Policy read-back --------------------------------------------------------

async def test_policy_endpoint_reports_the_effective_answer(session, monkeypatch):
    # The stored workspace flag and the effective one differ when the instance
    # forces the control on, and the UI has to be able to say so.
    _configure(monkeypatch, REQUIRE_SEPARATE_APPROVER="true")
    scene = await _scene(session, require_separate_approver=False)

    from app.api.agent_ownership import get_proposal_policy
    body = await get_proposal_policy(workspace_id=scene.workspace_id,
                                     principal=Principal(user=scene.alice),
                                     session=session)
    assert body["require_separate_approver"] is False
    assert body["separation_enforced"] is True


async def test_policy_endpoint_can_set_the_workspace_flag(session, monkeypatch):
    _configure(monkeypatch)
    scene = await _scene(session)
    admin_role = (await session.exec(
        select(Role).where(Role.name == "Workspace Admin"))).first()
    membership = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == scene.alice.id))).first()
    membership.role_id, membership.role = admin_role.id, "admin"
    session.add(membership)
    await session.commit()

    from app.api.agent_ownership import ProposalPolicyBody, set_proposal_policy
    body = await set_proposal_policy(
        workspace_id=scene.workspace_id,
        body=ProposalPolicyBody(require_separate_approver=True),
        principal=Principal(user=scene.alice), session=session)
    assert body["require_separate_approver"] is True

    await session.rollback()
    workspace = await session.get(Workspace, scene.workspace_id)
    assert workspace.require_separate_approver is True


async def test_setting_only_the_threshold_leaves_the_flag_alone(session, monkeypatch):
    # An omitted field must not silently switch a security control off.
    _configure(monkeypatch)
    scene = await _scene(session, require_separate_approver=True)
    admin_role = (await session.exec(
        select(Role).where(Role.name == "Workspace Admin"))).first()
    membership = (await session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == scene.alice.id))).first()
    membership.role_id, membership.role = admin_role.id, "admin"
    session.add(membership)
    await session.commit()

    from app.api.agent_ownership import ProposalPolicyBody, set_proposal_policy
    body = await set_proposal_policy(
        workspace_id=scene.workspace_id,
        body=ProposalPolicyBody(auto_apply_threshold=0.9),
        principal=Principal(user=scene.alice), session=session)
    assert body["require_separate_approver"] is True
