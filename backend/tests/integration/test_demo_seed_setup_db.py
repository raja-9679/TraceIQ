"""Demo workspace — setup/admin datasets (app/services/demo_seed/setup.py)
against a real Postgres, read back through the endpoints the pages call.

    TRACEIQ_SCRATCH_DB=traceiq_gseed_setup ./run-tests-live.sh tests/integration/test_demo_seed_setup_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                              # noqa: E402
from app.services import demo_seed                                   # noqa: E402
from app.services import demo_workspace as dw                        # noqa: E402
from tests.integration.demo_seed_fixtures import (                   # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, owner, session,
)


@pytest.fixture(autouse=True)
def only_setup(monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("setup",))


@pytest_asyncio.fixture(loop_scope="function")
async def demo(session, owner):
    return await dw.create_demo_workspace(session, owner)


def _p(user):
    return AuthPrincipal(user=user, api_key=None)


async def test_members_team_and_invitation(session, owner, demo):
    from app.api import workspaces
    from app.models import Team, TeamProjectAccess, UserTeam
    ws_id, web = demo["workspace_id"], demo["projects"][0]["id"]
    members = await workspaces.list_workspace_members_detailed(ws_id, session=session, current_user=owner)
    emails = {m.email if hasattr(m, "email") else m["email"] for m in members}
    assert len(emails) == 4 and all(e.endswith("@demo.traceiq.invalid") for e in emails - {owner.email})
    team = (await session.exec(select(Team).where(Team.workspace_id == ws_id))).one()
    assert len((await session.exec(select(UserTeam).where(UserTeam.team_id == team.id))).all()) == 2
    grant = (await session.exec(select(TeamProjectAccess).where(TeamProjectAccess.team_id == team.id))).one()
    assert grant.project_id == web and grant.role_id
    invites = await workspaces.list_workspace_invitations(ws_id, session=session, current_user=owner)
    assert len(invites) == 1 and invites[0]["email"].endswith(".invalid")


async def test_api_keys_are_dead(session, owner, demo):
    from app.api import api_keys
    from app.core.auth import _principal_from_api_key
    from app.models import ApiKey
    keys = await api_keys.list_api_keys(demo["workspace_id"], principal=_p(owner), session=session)
    assert len(keys) == 2
    now = datetime.utcnow()
    rows = (await session.exec(select(ApiKey).where(ApiKey.workspace_id == demo["workspace_id"]))).all()
    assert all(k.revoked_at or (k.expires_at and k.expires_at < now) for k in rows)
    # No raw key exists for them; and a key named by its prefix authenticates nothing.
    for k in rows:
        assert await _principal_from_api_key(k.prefix + "x" * 30, session, None) is None


async def test_openapi_has_two_versions_with_a_diff(session, owner, demo):
    from app.api import developer
    api = demo["projects"][1]["id"]
    out = await developer.list_openapi_spec_versions(api, principal=_p(owner), session=session)
    assert [v["version"] for v in out["versions"]] == [2, 1]
    assert out["versions"][0]["is_current"] is True
    diff = await developer.diff_openapi_spec_versions(api, from_=1, to=2, principal=_p(owner), session=session)
    assert len(diff["changes"]) >= 3
    assert any(c["severity"] == "breaking" for c in diff["changes"]), diff["changes"]


async def test_webhook_tracker_usage_policy_agents(session, owner, demo):
    from app.api import agents, issue_trackers, llm_usage, policies, workspace_webhooks
    from app.models import UsageRecord, WorkspaceWebhook
    ws_id = demo["workspace_id"]
    hook = (await session.exec(select(WorkspaceWebhook).where(WorkspaceWebhook.workspace_id == ws_id))).one()
    assert hook.is_active is False and "example.invalid" in hook.url
    log = await workspace_webhooks.list_deliveries(ws_id, hook.id, limit=50, status=None,
                                                    principal=_p(owner), session=session)
    statuses = [d["status"] for d in log["deliveries"]]
    assert statuses.count("failed") == 1 and statuses.count("ok") >= 6

    trackers = await issue_trackers.list_trackers(ws_id, session=session, principal=_p(owner))
    assert len(trackers) == 1 and trackers[0].enabled is False

    usage = await llm_usage.workspace_llm_usage(ws_id, days=30, session=session, current_user=owner)
    assert usage["totals"]["calls"] > 60
    assert {p["provider"] for p in usage["by_provider"]} == {"anthropic", "ollama"}
    assert len(usage["by_feature"]) >= 4 and len(usage["daily"]) >= 25
    assert any(p["success_rate"] < 1 for p in usage["by_provider"])
    records = (await session.exec(select(UsageRecord).where(UsageRecord.workspace_id == ws_id))).all()
    assert {r.metric for r in records} >= {"llm_tokens", "runs"}
    assert usage["period_tokens_used"] > 0

    denials = await policies.workspace_denials(ws_id, action=None, rule=None, agent_id=None, project_id=None,
                                               days=30, limit=100, offset=0, session=session,
                                               principal=_p(owner))
    assert denials["total"] == 4

    listed = await agents.list_agents(ws_id, session=session, principal=_p(owner))
    claude = next(a for a in listed["agents"] if a["agent_id"] == "claude-code")
    assert claude["sessions"] == 1 and claude["runs"] == 3 and claude["llm_tokens"] > 0
    assert claude["denials"] == 1


async def test_nothing_is_in_the_future(session, demo):
    from sqlalchemy import text
    now = datetime.utcnow() + timedelta(seconds=5)
    ws = demo["workspace_id"]
    for table, col in (("llmusageevent", "created_at"), ("webhookdelivery", "created_at"),
                       ("policydecisionlog", "created_at"), ("apikey", "created_at"),
                       ("apikey", "revoked_at"), ("apikey", "expires_at"),
                       ("agentsession", "last_seen_at"), ("issuetrackerconfig", "created_at")):
        n = (await session.exec(text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w AND {col} > :n"),
                                params={"w": ws, "n": now})).one()[0]
        assert n == 0, f"{table}.{col}"


async def test_delete_removes_everything_including_the_members(session, owner, monkeypatch):
    from app.core import storage
    from app.models import User
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    owner_id = owner.id
    before = await count_all_tables(session)
    out = await dw.create_demo_workspace(session, owner)
    assert len((await session.exec(select(User).where(
        User.email.like(f"%.demo{out['workspace_id']}@demo.traceiq.invalid")))).all()) == 3
    report = await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    assert report["demo_members_deleted"] == 3
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"
    assert await session.get(User, owner_id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_demo_invitation_cannot_be_used_to_join(session, owner):
    """The demo invite token is random (not derived from the constant seed),
    and nobody can register an address in the demo members' domain."""
    from fastapi import HTTPException
    from app.api.auth import UserCreate, register_user
    from app.models import WorkspaceInvitation

    a = await dw.create_demo_workspace(session, owner)
    invite = (await session.exec(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == a["workspace_id"]))).one()
    assert not invite.token.startswith("demo")
    register = getattr(register_user, "__wrapped__", register_user)  # skip the rate limiter
    with pytest.raises(HTTPException) as e:
        await register(SimpleNamespace(), UserCreate(
            email=invite.email.upper(), password="A-strong-passw0rd!x", full_name="x",
            invite_token=invite.token), session=session)
    assert e.value.status_code == 400 and "reserved" in e.value.detail
