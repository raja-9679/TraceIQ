"""Workspace invitations: the email, resend and cancel, through the API.

Before 2026-11 an invitation produced only a link shown once to the inviter;
nothing was emailed, a lost link could not be replaced and a wrong address
could not be withdrawn.

    ./run-tests-live.sh tests/integration/test_invitations_db.py -q
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import create_access_token                    # noqa: E402
from app.core.config import db_url_for, settings                 # noqa: E402
from app.models import (                                          # noqa: E402
    AuditLog, Tenant, User, UserWorkspace, Workspace, WorkspaceInvitation,
)
from app.services import invite_email                             # noqa: E402
from app.services.rbac_service import rbac_service                # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def maker():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    mk = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with mk() as s:
        await s.exec(text(_WIPE))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
    yield mk
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    from httpx import ASGITransport, AsyncClient
    from app.core.database import engine
    from app.main import app
    await engine.dispose()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://inv.test") as c:
        yield c
    await engine.dispose()


@pytest.fixture
def outbox(monkeypatch):
    """Mail handed to the queue, with email 'configured'."""
    sent: list[tuple] = []
    monkeypatch.setattr(invite_email, "email_configured", lambda: True)
    monkeypatch.setattr(invite_email, "deliver", lambda to, subject, html, text: sent.append((to, subject, html, text)))
    return sent


async def _scene(maker, *, demo: bool = False):
    async with maker() as s:
        admin = User(email="admin@acme.test", hashed_password="x", full_name="Maya Rao")
        member = User(email="member@acme.test", hashed_password="x", full_name="Li Chen")
        existing = User(email="already@acme.test", hashed_password="x", full_name="Sam Roy")
        s.add(admin); s.add(member); s.add(existing)
        await s.flush()
        tenant = Tenant(name="Acme", owner_id=admin.id)
        s.add(tenant)
        await s.flush()
        ws = Workspace(name="Checkout team", tenant_id=tenant.id, owner_id=admin.id, is_demo=demo)
        other = Workspace(name="Other", tenant_id=tenant.id, owner_id=admin.id)
        s.add(ws); s.add(other)
        await s.flush()
        admin_role = await rbac_service.get_role_by_name(s, "Workspace Admin")
        member_role = await rbac_service.get_role_by_name(s, "Workspace Member")
        s.add(UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin", role_id=admin_role.id))
        s.add(UserWorkspace(user_id=admin.id, workspace_id=other.id, role="admin", role_id=admin_role.id))
        s.add(UserWorkspace(user_id=member.id, workspace_id=ws.id, role="member", role_id=member_role.id))
        await s.commit()
        return {
            "ws": ws.id, "other": other.id,
            "ADMIN": {"Authorization": f"Bearer {create_access_token({'sub': admin.email})}"},
            "MEMBER": {"Authorization": f"Bearer {create_access_token({'sub': member.email})}"},
        }


async def _audit(maker, action):
    async with maker() as s:
        return (await s.exec(select(AuditLog).where(AuditLog.action == action))).all()


async def test_invitation_is_emailed_and_link_still_returned(maker, client, outbox):
    sc = await _scene(maker)
    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                          json={"email": "new@acme.test", "role": "member"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "invited" and body["email_sent"] is True and body["token"]

    assert len(outbox) == 1
    to, subject, html, text_body = outbox[0]
    assert to == "new@acme.test"
    assert subject == "Maya Rao invited you to Checkout team on TraceIQ"
    assert f"/signup?token={body['token']}" in text_body
    assert len(await _audit(maker, "invitation_created")) == 1

    # The list never carries the token.
    listed = (await client.get(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"])).json()
    assert listed[0]["id"] == body["invitation_id"] and "token" not in listed[0]
    assert listed[0]["expired"] is False


async def test_no_smtp_means_no_mail_but_the_link_works_as_before(maker, client, monkeypatch):
    sc = await _scene(maker)
    sent = []
    monkeypatch.setattr(invite_email, "email_configured", lambda: False)
    monkeypatch.setattr(invite_email, "deliver", lambda *a: sent.append(a))
    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                          json={"email": "new@acme.test"})
    assert r.json()["email_sent"] is False and r.json()["token"]
    assert sent == []


async def test_demo_workspace_never_sends(maker, client, outbox):
    sc = await _scene(maker, demo=True)
    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                          json={"email": "new@acme.test"})
    assert r.status_code == 200 and r.json()["email_sent"] is False
    assert outbox == []


async def test_existing_user_is_added_and_told(maker, client, outbox):
    sc = await _scene(maker)
    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                          json={"email": "already@acme.test", "role": "admin"})
    assert r.json() == {"status": "success", "message": "User added to workspace", "email_sent": True}
    to, subject, _html, text_body = outbox[0]
    assert to == "already@acme.test" and subject == "Maya Rao added you to Checkout team on TraceIQ"
    assert "token" not in text_body
    # Adding them again changes nothing and sends nothing.
    await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                      json={"email": "already@acme.test"})
    assert len(outbox) == 1


@pytest.mark.parametrize("bad", ["not-an-email", "a@b.test\nBcc: x@y.test", "a@b.test, c@d.test", "<a@b.test>", ""])
async def test_malformed_address_is_refused(maker, client, outbox, bad):
    sc = await _scene(maker)
    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"], json={"email": bad})
    assert r.status_code == 400
    assert outbox == []


async def test_resend_replaces_the_link(maker, client, outbox):
    sc = await _scene(maker)
    first = (await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                               json={"email": "new@acme.test"})).json()
    again = await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                              json={"email": "new@acme.test"})
    assert again.json()["status"] == "exists" and again.json()["invitation_id"] == first["invitation_id"]
    assert len(outbox) == 1  # a duplicate request does not mail again

    r = await client.post(f"/api/workspaces/{sc['ws']}/invitations/{first['invitation_id']}/resend", headers=sc["ADMIN"])
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["token"] and new["token"] != first["token"] and new["email_sent"] is True
    assert len(outbox) == 2 and new["token"] in outbox[1][3]
    assert len(await _audit(maker, "invitation_resent")) == 1

    # The old link is dead, the new one registers the account into the workspace.
    signup = {"email": "new@acme.test", "password": "Str0ng-enough-pass!", "full_name": "New Person"}
    old = await client.post("/api/auth/register", json={**signup, "invite_token": first["token"]})
    assert old.status_code == 400
    ok = await client.post("/api/auth/register", json={**signup, "invite_token": new["token"]})
    assert ok.status_code == 200, ok.text
    async with maker() as s:
        user = (await s.exec(select(User).where(User.email == "new@acme.test"))).one()
        assert (await s.exec(select(UserWorkspace).where(
            UserWorkspace.user_id == user.id, UserWorkspace.workspace_id == sc["ws"]))).first()


async def test_cancel_kills_the_link(maker, client, outbox):
    sc = await _scene(maker)
    inv = (await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                             json={"email": "typo@acme.test"})).json()
    r = await client.delete(f"/api/workspaces/{sc['ws']}/invitations/{inv['invitation_id']}", headers=sc["ADMIN"])
    assert r.status_code == 200
    assert (await client.get(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"])).json() == []
    reg = await client.post("/api/auth/register", json={
        "email": "typo@acme.test", "password": "Str0ng-enough-pass!", "full_name": "T", "invite_token": inv["token"]})
    assert reg.status_code == 400
    assert len(await _audit(maker, "invitation_cancelled")) == 1


async def test_only_user_managers_and_only_their_workspace(maker, client, outbox):
    sc = await _scene(maker)
    inv = (await client.post(f"/api/workspaces/{sc['ws']}/invitations", headers=sc["ADMIN"],
                             json={"email": "new@acme.test"})).json()
    iid = inv["invitation_id"]
    # A plain member can neither resend nor cancel.
    assert (await client.post(f"/api/workspaces/{sc['ws']}/invitations/{iid}/resend", headers=sc["MEMBER"])).status_code == 403
    assert (await client.delete(f"/api/workspaces/{sc['ws']}/invitations/{iid}", headers=sc["MEMBER"])).status_code == 403
    # An admin of another workspace cannot reach it through theirs.
    assert (await client.post(f"/api/workspaces/{sc['other']}/invitations/{iid}/resend", headers=sc["ADMIN"])).status_code == 404
    assert (await client.delete(f"/api/workspaces/{sc['other']}/invitations/{iid}", headers=sc["ADMIN"])).status_code == 404
    async with maker() as s:
        assert (await s.get(WorkspaceInvitation, iid)).token == inv["token"]
    assert len(outbox) == 1
