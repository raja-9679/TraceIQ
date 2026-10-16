"""In-app assistant (WP14a) against a real Postgres: the real access rules,
API-key scope, conversation privacy, hiding on lost access, retention purge,
erasure and workspace purge.

    TRACEIQ_SCRATCH_DB=traceiq_wp14a ./run-tests-live.sh tests/integration/test_assistant_db.py -q

The provider is scripted (no network): it plays a model that tries to read
another project's run.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import NamedTuple

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import assistant as api                          # noqa: E402
from app.core.auth import AuthPrincipal                       # noqa: E402
from app.core.config import db_url_for, settings              # noqa: E402
from app.models import (                                      # noqa: E402
    ApiKey, AssistantConversation, AssistantMessage, FailureCluster, Project, Tenant,
    TestCaseResult, TestRun, TestStatus, TestSuite, User, UserProjectAccess, UserWorkspace,
    Workspace,
)
from app.services import assistant as svc                     # noqa: E402
from app.services.rbac_service import rbac_service            # noqa: E402


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


class World(NamedTuple):
    admin: User          # workspace admin: sees both projects
    viewer: User         # member with a grant on Shop only
    other: User          # another member with a grant on Shop
    ws: int
    other_ws: int
    shop: int
    secret: int
    shop_run: int
    shop_result: int
    secret_run: int
    secret_cluster: int


@pytest_asyncio.fixture(loop_scope="function")
async def world(session) -> World:
    def user(email):
        return User(email=email, full_name=email.split("@")[0], hashed_password="x",
                    is_verified=True)
    admin, viewer, other = user("admin@corp.example.com"), user("vic@corp.example.com"), \
        user("otto@corp.example.com")
    session.add_all([admin, viewer, other])
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=admin.id)
    session.add(tenant)
    await session.flush()
    ws, ws2 = Workspace(name="Corp", tenant_id=tenant.id), Workspace(name="Else", tenant_id=tenant.id)
    session.add_all([ws, ws2])
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    session.add_all([
        UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=admin.id, workspace_id=ws2.id, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=viewer.id, workspace_id=ws.id, role="member"),
        UserWorkspace(user_id=other.id, workspace_id=ws.id, role="member"),
    ])
    shop, secret = Project(name="Shop", workspace_id=ws.id), Project(name="Secret", workspace_id=ws.id)
    session.add_all([shop, secret])
    await session.flush()
    session.add_all([
        UserProjectAccess(user_id=viewer.id, project_id=shop.id, access_level="viewer"),
        UserProjectAccess(user_id=other.id, project_id=shop.id, access_level="viewer"),
    ])
    s1, s2 = TestSuite(name="Checkout", project_id=shop.id), TestSuite(name="Vault", project_id=secret.id)
    session.add_all([s1, s2])
    await session.flush()
    now = datetime.utcnow()
    r1 = TestRun(test_suite_id=s1.id, project_id=shop.id, suite_name="Checkout",
                 status=TestStatus.FAILED, created_at=now - timedelta(hours=8))
    r2 = TestRun(test_suite_id=s2.id, project_id=secret.id, suite_name="Vault",
                 status=TestStatus.FAILED, created_at=now - timedelta(hours=8),
                 error_message="vault password=topsecret leaked")
    session.add_all([r1, r2])
    await session.flush()
    res = TestCaseResult(test_run_id=r1.id, test_name="checkout pays", status=TestStatus.FAILED,
                         duration_ms=900.0, error_message="Timeout 30000ms exceeded for alice@x.example.com")
    cl = FailureCluster(project_id=secret.id, signature="sig", title="Vault cluster",
                        category="timeout")
    session.add_all([res, cl])
    await session.commit()
    return World(admin=admin, viewer=viewer, other=other, ws=ws.id, other_ws=ws2.id,
                 shop=shop.id, secret=secret.id, shop_run=r1.id, shop_result=res.id,
                 secret_run=r2.id, secret_cluster=cl.id)


class Scripted:
    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, system=None, max_tokens=1024):
        self.prompts.append(prompt)
        return json.dumps(self.replies.pop(0)) if self.replies else ""


@pytest.fixture
def provider(monkeypatch):
    from app.ai import providers
    from app.services import instance_settings, llm_usage
    holder = {}
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: holder["p"])
    real = instance_settings.effective
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: True if k == "ASSISTANT_ENABLED" else real(k))
    monkeypatch.setattr(llm_usage, "llm_tokens_quota_exceeded", lambda *a, **k: False)
    # Metering writes through its own sync engine; not under test here.
    monkeypatch.setattr(llm_usage, "record_llm_usage", lambda **k: None)

    def set_replies(replies):
        holder["p"] = Scripted(replies)
        return holder["p"]
    return set_replies


def P(user, key=None):
    return AuthPrincipal(user=user, api_key=key)


async def _conversation(session, principal, ws, project_id=None):
    return await api.create_conversation(
        api.ConversationCreate(workspace_id=ws, project_id=project_id),
        session=session, principal=principal)


async def _ask(session, principal, conv_id, text):
    conv = await session.get(AssistantConversation, conv_id)
    scope = await api.scope_for(session, principal, conv.workspace_id)
    return await svc.ask(session, user_id=principal.user.id, conversation=conv, question=text,
                         allowed_projects=scope)


async def test_scope_follows_project_access(session, world):
    assert set(await api.scope_for(session, P(world.admin), world.ws)) == {world.shop, world.secret}
    assert set(await api.scope_for(session, P(world.viewer), world.ws)) == {world.shop}
    with pytest.raises(HTTPException) as exc:
        await api.scope_for(session, P(world.viewer), world.other_ws)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await _conversation(session, P(world.viewer), world.ws, project_id=world.secret)
    assert exc.value.status_code == 403


async def test_model_cannot_read_another_projects_run(session, world, provider):
    p = provider([
        {"tool": "get_run", "args": {"run_id": world.secret_run}},
        {"tool": "get_failure_cluster", "args": {"cluster_id": world.secret_cluster}},
        {"tool": "list_runs", "args": {"project_id": world.secret}},
        {"tool": "get_run", "args": {"run_id": world.shop_run}},
        {"answer": f"Checkout timed out [result:{world.shop_result}]; vault [run:{world.secret_run}].",
         "citations": [f"run:{world.shop_run}", f"run:{world.secret_run}"]},
    ])
    conv = await _conversation(session, P(world.viewer), world.ws)
    msg = await _ask(session, P(world.viewer), conv["id"], "why did checkout fail last night?")

    assert msg.status == "answered"
    assert {c["ref"] for c in msg.citations} == {f"result:{world.shop_result}", f"run:{world.shop_run}"}
    assert msg.dropped_citations == 2 and "[unverified]" in msg.content
    errors = [t.get("error") for t in msg.tool_calls]
    assert errors[:3] == [f"run {world.secret_run} not found or not accessible to this user",
                          f"cluster {world.secret_cluster} not found or not accessible to this user",
                          f"project {world.secret} not found or not accessible to this user"]
    assert errors[3] is None and msg.project_ids == [world.shop]
    blob = "\n".join(p.prompts)
    assert "Vault" not in blob and "Secret" not in blob and "topsecret" not in blob
    assert "alice@x.example.com" not in blob and "checkout pays" in blob


async def test_admin_sees_both_but_a_project_scoped_key_does_not(session, world, provider):
    key = ApiKey(workspace_id=world.ws, project_id=world.shop, name="ci", prefix="tiq_ab",
                 hashed_key="x", created_by_id=world.admin.id)
    session.add(key)
    await session.commit()
    assert set(await api.scope_for(session, P(world.admin, key), world.ws)) == {world.shop}

    provider([{"tool": "get_run", "args": {"run_id": world.secret_run}},
              {"answer": "n/a", "citations": []}])
    conv = await _conversation(session, P(world.admin, key), world.ws)
    msg = await _ask(session, P(world.admin, key), conv["id"], "vault?")
    assert msg.tool_calls[0]["error"].endswith("not accessible to this user")

    foreign = ApiKey(workspace_id=world.other_ws, name="other", prefix="tiq_cd",
                     hashed_key="x", created_by_id=world.admin.id)
    session.add(foreign)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await api.scope_for(session, P(world.admin, foreign), world.ws)
    assert exc.value.status_code == 403
    # A conversation made with the workspace key is not reachable with a key
    # from another workspace, even for the same user.
    with pytest.raises(HTTPException) as exc:
        await api.get_conversation(conv["id"], session=session, principal=P(world.admin, foreign))
    assert exc.value.status_code == 404


async def test_answer_from_the_ui_is_hidden_from_a_narrower_key(session, world, provider):
    provider([{"tool": "get_run", "args": {"run_id": world.secret_run}},
              {"answer": f"Vault failed [run:{world.secret_run}]",
               "citations": [f"run:{world.secret_run}"]}])
    conv = await _conversation(session, P(world.admin), world.ws)
    await _ask(session, P(world.admin), conv["id"], "vault?")
    full = await api.get_conversation(conv["id"], session=session, principal=P(world.admin))
    assert full["messages"][1]["content"].startswith("Vault failed")

    key = ApiKey(workspace_id=world.ws, project_id=world.shop, name="ci", prefix="tiq_ef",
                 hashed_key="x", created_by_id=world.admin.id)
    session.add(key)
    await session.commit()
    narrow = await api.get_conversation(conv["id"], session=session, principal=P(world.admin, key))
    assert narrow["messages"][1]["hidden"] and narrow["messages"][1]["citations"] == []


async def test_conversations_are_private(session, world, provider):
    provider([{"answer": "hello", "citations": []}])
    conv = await _conversation(session, P(world.viewer), world.ws)
    await _ask(session, P(world.viewer), conv["id"], "hi")

    for call in (api.get_conversation, api.delete_conversation):
        with pytest.raises(HTTPException) as exc:
            await call(conv["id"], session=session, principal=P(world.other))
        assert exc.value.status_code == 404
    # Workspace admins do not get to read members' chats either.
    with pytest.raises(HTTPException):
        await api.get_conversation(conv["id"], session=session, principal=P(world.admin))
    assert await api.list_conversations(workspace_id=world.ws, session=session,
                                        principal=P(world.other)) == []
    mine = await api.list_conversations(workspace_id=world.ws, session=session,
                                        principal=P(world.viewer))
    assert [c["id"] for c in mine] == [conv["id"]] and mine[0]["title"] == "hi"

    await api.delete_conversation(conv["id"], session=session, principal=P(world.viewer))
    left = (await session.exec(select(AssistantMessage).where(
        AssistantMessage.conversation_id == conv["id"]))).all()
    assert left == [] and await session.get(AssistantConversation, conv["id"]) is None


async def test_lost_access_hides_old_answers(session, world, provider):
    provider([{"tool": "list_runs", "args": {}},
              {"answer": f"Checkout [run:{world.shop_run}]", "citations": [f"run:{world.shop_run}"]}])
    conv = await _conversation(session, P(world.viewer), world.ws)
    await _ask(session, P(world.viewer), conv["id"], "runs?")
    grant = await session.get(UserProjectAccess, (world.viewer.id, world.shop))
    await session.delete(grant)
    await session.commit()
    out = await api.get_conversation(conv["id"], session=session, principal=P(world.viewer))
    assert out["messages"][0]["content"] == "runs?"
    assert out["messages"][1]["hidden"] and out["messages"][1]["content"] == svc.HIDDEN


async def test_retention_purge_and_read_filter(session, world, provider):
    provider([{"answer": "new", "citations": []}])
    conv = await _conversation(session, P(world.viewer), world.ws)
    await _ask(session, P(world.viewer), conv["id"], "fresh")
    old_at = datetime.utcnow() - timedelta(days=svc.RETENTION_DAYS + 1)
    stale = AssistantConversation(user_id=world.viewer.id, workspace_id=world.ws, title="old",
                                  created_at=old_at, updated_at=old_at)
    session.add(stale)
    await session.flush()
    session.add_all([
        AssistantMessage(conversation_id=stale.id, user_id=world.viewer.id, workspace_id=world.ws,
                         role="user", content="ancient", created_at=old_at),
        AssistantMessage(conversation_id=conv["id"], user_id=world.viewer.id, workspace_id=world.ws,
                         role="user", content="old line", created_at=old_at),
    ])
    await session.commit()
    stale_id = stale.id

    # The read path never shows expired rows, even before the beat task runs.
    listed = await api.list_conversations(workspace_id=world.ws, session=session,
                                          principal=P(world.viewer))
    assert [c["id"] for c in listed] == [conv["id"]]
    out = await api.get_conversation(conv["id"], session=session, principal=P(world.viewer))
    assert "old line" not in [m["content"] for m in out["messages"]]

    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    with Session(engine) as s:
        assert svc.purge_expired_sync(s) == 3          # two messages + the idle conversation
    engine.dispose()
    session.expire_all()
    assert await session.get(AssistantConversation, stale_id) is None
    kept = (await session.exec(select(AssistantMessage.content).where(
        AssistantMessage.conversation_id == conv["id"]))).all()
    assert sorted(kept) == ["fresh", "new"]


async def test_erasure_and_workspace_purge_remove_conversations(session, world, provider):
    provider([{"answer": "a", "citations": []}, {"answer": "b", "citations": []}])
    c1 = await _conversation(session, P(world.viewer), world.ws, project_id=world.shop)
    await _ask(session, P(world.viewer), c1["id"], "one")
    c2 = await _conversation(session, P(world.other), world.ws)
    await _ask(session, P(world.other), c2["id"], "two")

    from app.services.erasure import erase_user
    report = await erase_user(session, world.viewer)
    await session.commit()
    assert report.erased.get("assistantmessage") == 2
    assert report.erased.get("assistantconversation") == 1
    assert await session.get(AssistantConversation, c2["id"]) is not None

    from app.services.purge import PURGE_PLAN
    for step in PURGE_PLAN:
        if step.table.startswith("assistant"):
            await session.exec(text(step.sql), params={"workspace_id": world.ws})
    await session.commit()
    n = (await session.exec(select(AssistantConversation))).all()
    assert n == []


async def test_every_tool_runs_against_the_real_schema(session, world):
    """The queries themselves (the unit suite fakes the data layer)."""
    from app.services import assistant_tools as tools
    scope = await api.scope_for(session, P(world.viewer), world.ws)
    ctx = tools.ToolContext(session=session, workspace_id=world.ws,
                            allowed_project_ids=frozenset(scope), project_names=scope,
                            data=tools.AssistantData(session))
    calls = {
        "list_projects": {}, "project_overview": {"project_id": world.shop},
        "list_runs": {"status": "failed"}, "get_run": {"run_id": world.shop_run},
        "search_results": {"test_name_contains": "checkout"}, "list_flaky_tests": {},
        "list_failure_clusters": {"status": "any"}, "release_readiness": {"project_id": world.shop},
        "list_security_findings": {"status": "open", "severity": "high"},
        "find_cases": {"name_contains": "x", "priority": "critical"},
    }
    assert set(calls) | {"get_failure_cluster"} == set(tools.TOOLS)
    for name, args in calls.items():
        res = await tools.run_tool(ctx, name, args)
        assert res.error is None, (name, res.error)
        assert all(m["project_id"] == world.shop for m in res.refs.values()), name
    res = await tools.run_tool(ctx, "search_results", {"test_name_contains": "checkout"})
    assert set(res.refs) == {f"result:{world.shop_result}"}
    assert "alice@x.example.com" not in json.dumps(res.payload)
    res = await tools.run_tool(ctx, "list_failure_clusters", {"status": "any"})
    assert res.refs == {}       # the only cluster is in the project the viewer cannot see
