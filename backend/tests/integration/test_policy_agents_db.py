"""Policy engine + agent controls against a real Postgres (WP14b).

    TRACEIQ_SCRATCH_DB=traceiq_wp14b ./run-tests-live.sh tests/integration/test_policy_agents_db.py -q

Covers: the AI opt-out blocking every LLM path (a fake provider that must
never be called), per-session budgets across runs / proposals / tokens with
one session not affecting another, kill-switch scope, deny-log contents, and
access control on the Policies / Agents endpoints.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import agent_ownership, agents as agents_api, policies as policies_api  # noqa: E402
from app.api import case_generation                                   # noqa: E402
from app.api.endpoints import test_runs                               # noqa: E402
from app.core.auth import AuthPrincipal                               # noqa: E402
from app.core.config import db_url_for, settings                      # noqa: E402
from app.models import (                                              # noqa: E402
    AgentControl, AgentSession, ApiKey, AuditLog, CaseProposalAction, CaseProposalCreate,
    CaseGenerationRequest, PolicyDecisionLog, Project, Tenant, TestCaseResult, TestRun,
    TestStatus, User, UserWorkspace, Workspace,
)
from app.services import policy                                       # noqa: E402
from app.services.rbac_service import rbac_service                    # noqa: E402

from tests.integration.test_targeted_runs_db import (                 # noqa: E402,F401
    no_queue, scene, session,
)


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, prompt, **kw):
        self.calls += 1
        return '[{"type": "goto", "value": "https://x.test"}]'


@pytest.fixture
def fake_llm(monkeypatch):
    from app.ai import providers
    llm = FakeLLM()
    monkeypatch.setattr(providers, "_resolve_provider", lambda pid=None: llm)
    return llm


@pytest.fixture(autouse=True)
def fresh_dedupe():
    policy.reset_dedupe()
    yield
    policy.reset_dedupe()


def _sync():
    return Session(create_engine(db_url_for(settings.DATABASE_URL, sync=True)))


async def _ws_id(session, scene) -> int:
    return (await session.get(Project, scene.project_id)).workspace_id


def _agent(scene, agent="bot", sess="s1", api_key=None):
    return AuthPrincipal(user=scene.user, api_key=api_key, agent_id=agent, agent_session_id=sess)


async def _run(session, scene, principal):
    return await test_runs.create_run(
        suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=test_runs.RunCreateContext(case_ids=[scene.root_cases[0]]),
        session=session, principal=principal)


async def _propose(session, scene, principal):
    return await agent_ownership.create_proposal(
        CaseProposalCreate(project_id=scene.project_id, test_suite_id=scene.sibling_id,
                           action=CaseProposalAction.CREATE,
                           payload={"name": "new", "steps": []}, ai_confidence=0.1),
        principal=principal, session=session)


async def _denies(session, **where):
    q = select(PolicyDecisionLog)
    for k, v in where.items():
        q = q.where(getattr(PolicyDecisionLog, k) == v)
    return (await session.exec(q.order_by(PolicyDecisionLog.id))).all()


async def _second_workspace(session, scene, *, member_of_first=None):
    """Another tenant's admin, with their own workspace + project."""
    other = User(email="eve@other.example.com", full_name="Eve", hashed_password="x", is_verified=True)
    session.add(other)
    await session.flush()
    tenant = Tenant(name="Other", owner_id=other.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Other ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    admin_role = await rbac_service.get_role_by_name(session, "Workspace Admin")
    session.add(UserWorkspace(user_id=other.id, workspace_id=ws.id, role="admin", role_id=admin_role.id))
    project = Project(name="Theirs", workspace_id=ws.id)
    session.add(project)
    await session.commit()
    return other, ws, project


# ---------------------------------------------------------------------------
# W2 — the AI opt-out blocks every LLM path
# ---------------------------------------------------------------------------

async def test_opt_out_blocks_every_llm_path(session, scene, fake_llm, monkeypatch):
    ws_id = await _ws_id(session, scene)
    ws = await session.get(Workspace, ws_id)
    ws.ai_enabled = False
    session.add(ws)
    await session.commit()
    me = AuthPrincipal(user=scene.user, api_key=None)

    # 1. Case generation (endpoint) refuses with the reason.
    with pytest.raises(HTTPException) as exc:
        await case_generation.generate_case(
            CaseGenerationRequest(description="log in", test_suite_id=scene.sibling_id, mode="propose"),
            principal=me, session=session)
    assert exc.value.status_code == 403 and "switched off" in exc.value.detail

    # 2. Generation used by requirement / incident proposals: no steps, no call.
    steps = await case_generation.generate_steps("log in", None, workspace_id=ws_id,
                                                 project_id=scene.project_id)
    assert steps == []

    # 3. Failure analysis (Celery task) falls back to the heuristic report.
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.FAILED)
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name="cart", status=TestStatus.FAILED,
                               duration_ms=1, error_message="Timeout waiting for locator('#x')",
                               test_case_id=scene.root_cases[0]))
    await session.commit()
    from app.tasks import analysis_tasks
    assert analysis_tasks.analyze_run_failures(run.id)["status"] == "ok"

    # 4. Proactive heal stops before its loop.
    monkeypatch.setenv("PROACTIVE_HEAL_ENABLED", "true")
    from app.tasks import heal_tasks
    assert heal_tasks.propose_selector_heals_for_run(run.id) == {
        "status": "ai_not_allowed", "rule": "ai.workspace_opt_out"}

    # 5. The legacy AI engine (in-process heal / analysis).
    from app.ai.engine import ai_engine
    from app.services.llm_usage import llm_call_context
    with llm_call_context(workspace_id=ws_id):
        assert ai_engine.heal_selector("#x", "<html/>") == ""

    # 6. The assistant's entry point.
    verdict = await policy.ai_allowed(session, ws_id, principal=me)
    assert not verdict.allowed and verdict.rule == "ai.workspace_opt_out"

    # 7. Worker jobs carry ai_allowed=false.
    with _sync() as s:
        cfg = policy.evaluate_sync("run.dispatch", policy.PolicyContext(
            workspace_id=ws_id, project_id=scene.project_id, stage="configure", session=s,
            facts={"run": None}))
    assert cfg.data["ai_allowed"] is False and cfg.data["ai_blocked_by"] == "ai.workspace_opt_out"

    assert fake_llm.calls == 0
    rows = await _denies(session, rule="ai.workspace_opt_out")
    assert rows and all(r.workspace_id == ws_id and r.action == "ai.use" for r in rows)

    # Switched back on, the same paths reach the provider: it was the opt-out.
    ws.ai_enabled = True
    session.add(ws)
    await session.commit()
    assert await case_generation.generate_steps("log in", None, workspace_id=ws_id,
                                                project_id=scene.project_id)
    assert fake_llm.calls == 1
    with _sync() as s:
        cfg = policy.evaluate_sync("run.dispatch", policy.PolicyContext(
            workspace_id=ws_id, project_id=scene.project_id, stage="configure", session=s,
            facts={"run": None}))
    assert cfg.data["ai_allowed"] is True


async def test_ai_policy_endpoint_is_admin_only_and_audited(session, scene):
    ws_id = await _ws_id(session, scene)
    me = AuthPrincipal(user=scene.user, api_key=None)
    out = await policies_api.set_ai_policy(ws_id, policies_api.AiPolicyBody(ai_enabled=False),
                                           session=session, principal=me)
    assert out["ai_enabled"] is False
    audit = (await session.exec(select(AuditLog).where(AuditLog.action == "ai_disabled"))).all()
    assert len(audit) == 1 and audit[0].workspace_id == ws_id

    key = ApiKey(name="k", prefix="tiq_x", hashed_key="h", workspace_id=ws_id, created_by_id=scene.user.id)
    session.add(key)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await policies_api.set_ai_policy(ws_id, policies_api.AiPolicyBody(ai_enabled=True),
                                         session=session, principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code == 403

    other, other_ws, _ = await _second_workspace(session, scene)
    with pytest.raises(HTTPException) as exc:
        await policies_api.set_ai_policy(ws_id, policies_api.AiPolicyBody(ai_enabled=True),
                                         session=session, principal=AuthPrincipal(user=other))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# W3 — budgets per session
# ---------------------------------------------------------------------------

async def test_run_budget_exhaustion_is_per_session(session, scene):
    ws_id = await _ws_id(session, scene)
    session.add(AgentControl(workspace_id=ws_id, agent_id="bot", max_runs=2))
    await session.commit()

    s1 = _agent(scene, sess="s1")
    assert len(await _run(session, scene, s1)) == 1
    assert len(await _run(session, scene, s1)) == 1
    with pytest.raises(HTTPException) as exc:
        await _run(session, scene, s1)
    assert exc.value.status_code == 429
    detail = exc.value.detail
    assert detail["error"] == "agent_budget_exceeded" and detail["limit"] == "max_runs"
    assert (detail["used"], detail["max"], detail["agent_session_id"]) == (2, 2, "s1")

    # Another session of the same agent, and the same session id under
    # another agent, are unaffected.
    assert len(await _run(session, scene, _agent(scene, sess="s2"))) == 1
    assert len(await _run(session, scene, _agent(scene, agent="other", sess="s1"))) == 1

    runs = (await session.exec(select(TestRun).where(TestRun.agent_session_id == "s1",
                                                     TestRun.agent_id == "bot"))).all()
    assert len(runs) == 2
    rows = await _denies(session, rule="agent.budget")
    assert len(rows) == 1
    d = rows[0]
    assert (d.action, d.workspace_id, d.project_id, d.user_id, d.agent_id, d.agent_session_id) == (
        "run.dispatch", ws_id, scene.project_id, scene.user.id, "bot", "s1")
    assert d.data["limit"] == "max_runs" and d.data["kind"] == "run"
    sess = (await session.exec(select(AgentSession).where(AgentSession.agent_session_id == "s1",
                                                          AgentSession.agent_id == "bot"))).one()
    await session.refresh(sess)
    assert (sess.runs, sess.denials) == (2, 1)


async def test_proposal_budget_and_workspace_default(session, scene):
    ws_id = await _ws_id(session, scene)
    # The "*" row caps every agent that has no row of its own.
    session.add(AgentControl(workspace_id=ws_id, agent_id="*", max_proposals=1))
    await session.commit()
    await _propose(session, scene, _agent(scene, sess="p1"))
    with pytest.raises(HTTPException) as exc:
        await _propose(session, scene, _agent(scene, sess="p1"))
    assert exc.value.status_code == 429 and exc.value.detail["limit"] == "max_proposals"
    await _propose(session, scene, _agent(scene, sess="p2"))
    # A human without agent headers is never budgeted.
    await _propose(session, scene, AuthPrincipal(user=scene.user))


async def test_token_budget_counts_metered_calls_per_session(session, scene, fake_llm):
    ws_id = await _ws_id(session, scene)
    session.add(AgentControl(workspace_id=ws_id, agent_id="bot", max_llm_tokens=100))
    await session.commit()
    from app.services.llm_usage import llm_call_context, record_llm_usage
    with llm_call_context(workspace_id=ws_id, agent_id="bot", agent_session_id="t1"):
        record_llm_usage(provider="fake", model="m", input_tokens=90, output_tokens=60)
    assert not policy.ai_allowed_sync(ws_id, agent_id="bot", agent_session_id="t1").allowed
    assert policy.ai_allowed_sync(ws_id, agent_id="bot", agent_session_id="t2").allowed
    assert policy.ai_allowed_sync(ws_id).allowed      # no agent: no agent budget

    # Through the provider guard: the over-budget session never reaches the LLM.
    from app.ai.providers import get_provider
    with llm_call_context(workspace_id=ws_id, agent_id="bot", agent_session_id="t1"):
        assert get_provider().complete("hi") == ""
    with llm_call_context(workspace_id=ws_id, agent_id="bot", agent_session_id="t2"):
        assert get_provider().complete("hi")
    assert fake_llm.calls == 1

    from app.models import LLMUsageEvent
    ev = (await session.exec(select(LLMUsageEvent).where(LLMUsageEvent.agent_session_id == "t1"))).one()
    assert ev.agent_id == "bot" and ev.total_tokens == 150
    rows = await _denies(session, rule="agent.budget", agent_session_id="t1")
    assert rows and rows[0].action == "ai.use" and rows[0].data["limit"] == "max_llm_tokens"


async def test_instance_default_budget(session, scene, monkeypatch):
    from app.services import instance_settings
    real = instance_settings.effective
    monkeypatch.setattr(instance_settings, "effective",
                        lambda key: 1 if key == "AGENT_SESSION_MAX_RUNS" else real(key))
    await _run(session, scene, _agent(scene, sess="i1"))
    with pytest.raises(HTTPException) as exc:
        await _run(session, scene, _agent(scene, sess="i1"))
    assert exc.value.status_code == 429
    # A workspace row with an explicit 0 lifts it for that agent.
    session.add(AgentControl(workspace_id=await _ws_id(session, scene), agent_id="bot", max_runs=0))
    await session.commit()
    await _run(session, scene, _agent(scene, sess="i1"))


# ---------------------------------------------------------------------------
# W3 — kill switch
# ---------------------------------------------------------------------------

async def test_kill_switch_scope(session, scene):
    ws_id = await _ws_id(session, scene)
    me = AuthPrincipal(user=scene.user)
    await agents_api.put_control(ws_id, agents_api.AgentControlBody(
        agent_id="bot", blocked=True, blocked_reason="looping"), session=session, principal=me)

    with pytest.raises(HTTPException) as exc:
        await _run(session, scene, _agent(scene))
    assert exc.value.status_code == 403 and exc.value.detail["error"] == "agent_blocked"
    with pytest.raises(HTTPException) as exc:
        await _propose(session, scene, _agent(scene))
    assert exc.value.status_code == 403
    # Other agents, and the same user without agent headers, still write.
    await _run(session, scene, _agent(scene, agent="other"))
    await _run(session, scene, me)

    # The same agent id in another workspace is not blocked.
    other, other_ws, other_project = await _second_workspace(session, scene)
    verdict = await policy.evaluate("agent.write", policy.PolicyContext(
        workspace_id=other_ws.id, agent_id="bot", session=session))
    assert verdict.allowed

    # API-key writes are gated in auth; reads are not.
    from starlette.requests import Request
    from app.core.auth import _agent_gate
    key = ApiKey(name="k", prefix="tiq_y", hashed_key="h2", workspace_id=ws_id, created_by_id=scene.user.id)
    session.add(key)
    await session.commit()
    keyed = _agent(scene, api_key=key)

    def req(method):
        return Request({"type": "http", "method": method, "path": "/api/anything", "headers": [],
                        "query_string": b""})
    await _agent_gate(req("GET"), session, keyed)
    with pytest.raises(HTTPException) as exc:
        await _agent_gate(req("POST"), session, keyed)
    assert exc.value.status_code == 403

    rows = await _denies(session, rule="agent.kill_switch")
    assert {r.action for r in rows} >= {"run.dispatch", "proposal.create", "agent.write"}
    assert all(r.workspace_id == ws_id and r.agent_id == "bot" for r in rows)

    audit = (await session.exec(select(AuditLog).where(AuditLog.entity_type == "agent_control"))).all()
    assert [a.action for a in audit] == ["agent_blocked"]

    # Unblocking restores writes.
    await agents_api.put_control(ws_id, agents_api.AgentControlBody(agent_id="bot", blocked=False),
                                 session=session, principal=me)
    await _run(session, scene, _agent(scene))


# ---------------------------------------------------------------------------
# Access control + the activity view
# ---------------------------------------------------------------------------

async def test_agents_and_denials_need_workspace_admin(session, scene):
    ws_id = await _ws_id(session, scene)
    member = User(email="mem@corp.example.com", full_name="Mem", hashed_password="x", is_verified=True)
    session.add(member)
    await session.flush()
    member_role = await rbac_service.get_role_by_name(session, "Workspace Member")
    session.add(UserWorkspace(user_id=member.id, workspace_id=ws_id, role="member", role_id=member_role.id))
    await session.commit()
    other, other_ws, _ = await _second_workspace(session, scene)
    admin = AuthPrincipal(user=scene.user)

    for who in (AuthPrincipal(user=member), AuthPrincipal(user=other)):
        for call in (
            lambda p: agents_api.list_agents(ws_id, session=session, principal=p),
            lambda p: agents_api.list_sessions(ws_id, limit=100, session=session, principal=p),
            lambda p: agents_api.session_timeline(ws_id, "bot", "s1", session=session, principal=p),
            lambda p: agents_api.get_controls(ws_id, session=session, principal=p),
            lambda p: agents_api.put_control(ws_id, agents_api.AgentControlBody(agent_id="bot"),
                                             session=session, principal=p),
            lambda p: policies_api.workspace_denials(ws_id, days=30, limit=10, offset=0,
                                                     session=session, principal=p),
        ):
            with pytest.raises(HTTPException) as exc:
                await call(who)
            assert exc.value.status_code == 403
    # Members may read whether AI is on, but not change it.
    got = await policies_api.get_ai_policy(ws_id, session=session, principal=AuthPrincipal(user=member))
    assert got["ai_enabled"] is True and got["can_edit"] is False
    assert (await policies_api.get_ai_policy(ws_id, session=session, principal=admin))["can_edit"] is True
    # The other workspace's admin sees only their own denials.
    out = await policies_api.workspace_denials(other_ws.id, days=30, limit=10, offset=0,
                                               session=session, principal=AuthPrincipal(user=other))
    assert out["total"] == 0


async def test_activity_view_and_timeline(session, scene, fake_llm):
    ws_id = await _ws_id(session, scene)
    bot = _agent(scene, sess="a1")
    await _run(session, scene, bot)
    await _propose(session, scene, bot)
    from app.services.llm_usage import llm_call_context, record_llm_usage
    with llm_call_context(workspace_id=ws_id, agent_id="bot", agent_session_id="a1"):
        record_llm_usage(provider="fake", model="m", input_tokens=10, output_tokens=5,
                         feature="case_generation")
    # Something in another workspace must not show up.
    other, other_ws, other_project = await _second_workspace(session, scene)
    session.add(TestRun(test_suite_id=scene.root_id, project_id=other_project.id, agent_id="bot",
                        agent_session_id="a1", status=TestStatus.PASSED))
    await session.commit()

    admin = AuthPrincipal(user=scene.user)
    listed = await agents_api.list_agents(ws_id, session=session, principal=admin)
    bot_row = next(a for a in listed["agents"] if a["agent_id"] == "bot")
    assert (bot_row["runs"], bot_row["proposals"], bot_row["sessions"], bot_row["llm_tokens"]) == (1, 1, 1, 15)

    sessions = await agents_api.list_sessions(ws_id, agent_id="bot", limit=100, session=session, principal=admin)
    assert [(s["agent_session_id"], s["runs"], s["proposals"], s["llm_tokens"])
            for s in sessions["sessions"]] == [("a1", 1, 1, 15)]

    tl = await agents_api.session_timeline(ws_id, "bot", "a1", session=session, principal=admin)
    kinds = sorted(i["kind"] for i in tl["items"])
    assert kinds == ["llm_call", "proposal_made", "run_started"]
    run_item = next(i for i in tl["items"] if i["kind"] == "run_started")
    assert run_item["link"].startswith("/runs/") and run_item["project_id"] == scene.project_id


async def test_separation_and_scan_denies_are_logged(session, scene):
    ws_id = await _ws_id(session, scene)
    ws = await session.get(Workspace, ws_id)
    ws.require_separate_approver = True
    session.add(ws)
    await session.commit()
    me = AuthPrincipal(user=scene.user)
    created = await _propose(session, scene, me)
    with pytest.raises(HTTPException) as exc:
        await agent_ownership.accept_proposal(created.id, None, principal=me, session=session)
    assert exc.value.status_code == 403
    rows = await _denies(session, rule="proposal.separation_of_duties")
    assert len(rows) == 1 and rows[0].workspace_id == ws_id and rows[0].user_id == scene.user.id

    from app.api import security
    project = await session.get(Project, scene.project_id)
    reason = await security._scan_gate(session, project, "https://example.com", "baseline", principal=me)
    assert reason == "Security scanning is disabled for this project"
    rows = await _denies(session, rule="security.scan_gate")
    assert rows[0].data["gate"] == "security.scanning_enabled" and rows[0].project_id == scene.project_id
