"""Workspace / team / API-key access control, through the real request path.

Regressions found while writing the user guide (info/GUIDE_FOLLOWUPS.md,
"From setup1"):

* nobody could delete a workspace (the endpoint asked for a permission no role
  grants);
* removing a member left their project grants, team memberships and per-case
  overrides behind, so they kept access — and nothing was audited;
* project-scoped API keys were not held to their project on suites/cases (and
  keys were not held to their workspace or their role anywhere);
* a team could be given members from outside its workspace;
* there was no way to change an existing member's workspace role.

    TRACEIQ_SCRATCH_DB=traceiq_gaccess ./run-tests-live.sh tests/integration/test_workspace_access_db.py -q
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import create_access_token, generate_api_key  # noqa: E402
from app.core.config import db_url_for, settings                # noqa: E402
from app.models import (                                         # noqa: E402
    ApiKey, AuditLog, Project, Team, TeamProjectAccess, Tenant, TestCase,
    TestSuite, User, UserProjectAccess, UserSystemRole, UserTeam,
    UserTestCaseAccess, UserWorkspace, Workspace,
)
from app.services.access_service import access_service           # noqa: E402
from app.services.rbac_service import rbac_service               # noqa: E402

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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ws.test") as c:
        yield c
    await engine.dispose()


class Scene(NamedTuple):
    ws: int
    other_ws: int
    p1: int
    p2: int
    other_p: int
    s1: int
    s2: int
    c1: int
    c2: int
    other_case: int
    team: int
    admin: int
    editor: int
    outsider: int


def _bearer(email: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


ADMIN = _bearer("admin@acme.example.com")

# Refused. With RLS enforcing (TRACEIQ_RLS_MODE=enforce) a row outside the
# key's scope is invisible before the access check runs, so it reads as 404.
DENIED = (403, 404)


async def _project(s, ws_id: int, name: str):
    p = Project(name=name, workspace_id=ws_id)
    s.add(p)
    await s.flush()
    suite = TestSuite(name=f"{name} suite", project_id=p.id)
    s.add(suite)
    await s.flush()
    case = TestCase(name=f"{name} case", test_suite_id=suite.id, project_id=p.id, steps=[])
    s.add(case)
    await s.flush()
    return p, suite, case


@pytest_asyncio.fixture(loop_scope="function")
async def scene(maker) -> Scene:
    async with maker() as s:
        admin = User(email="admin@acme.example.com", full_name="Admin", hashed_password="x", is_verified=True)
        editor = User(email="editor@acme.example.com", full_name="Editor", hashed_password="x", is_verified=True)
        outsider = User(email="out@else.example.com", full_name="Out", hashed_password="x", is_verified=True)
        s.add_all([admin, editor, outsider])
        await s.flush()
        tenant = Tenant(name="acme", owner_id=admin.id)
        s.add(tenant)
        await s.flush()
        ws = Workspace(name="acme ws", tenant_id=tenant.id)
        other_ws = Workspace(name="acme second ws", tenant_id=tenant.id)
        s.add_all([ws, other_ws])
        await s.flush()
        ws_admin = await rbac_service.get_role_by_name(s, "Workspace Admin")
        ws_member = await rbac_service.get_role_by_name(s, "Workspace Member")
        tenant_admin = await rbac_service.get_role_by_name(s, "Tenant Admin")
        p_editor = await rbac_service.get_role_by_name(s, "Project Editor")
        s.add_all([
            UserSystemRole(user_id=admin.id, role_id=tenant_admin.id, tenant_id=tenant.id),
            UserWorkspace(user_id=admin.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
            UserWorkspace(user_id=admin.id, workspace_id=other_ws.id, role="admin", role_id=ws_admin.id),
            UserWorkspace(user_id=editor.id, workspace_id=ws.id, role="member", role_id=ws_member.id),
        ])
        p1, s1, c1 = await _project(s, ws.id, "one")
        p2, s2, c2 = await _project(s, ws.id, "two")
        op, _, oc = await _project(s, other_ws.id, "elsewhere")
        team = Team(name="qa", workspace_id=ws.id)
        s.add(team)
        await s.flush()
        s.add_all([
            UserProjectAccess(user_id=editor.id, project_id=p1.id, access_level="editor",
                              role_id=p_editor.id),
            TeamProjectAccess(team_id=team.id, project_id=p2.id, access_level="editor"),
            UserTeam(user_id=editor.id, team_id=team.id),
            UserTestCaseAccess(user_id=editor.id, test_case_id=c2.id, access_level="editor"),
        ])
        await s.commit()
        return Scene(ws.id, other_ws.id, p1.id, p2.id, op.id, s1.id, s2.id, c1.id, c2.id,
                     oc.id, team.id, admin.id, editor.id, outsider.id)


async def _key(maker, *, owner: int, ws: int, project: int | None = None,
               role: str | None = None) -> dict:
    raw, prefix, hashed = generate_api_key()
    async with maker() as s:
        role_id = (await rbac_service.get_role_by_name(s, role)).id if role else None
        s.add(ApiKey(workspace_id=ws, project_id=project, name="k", prefix=prefix,
                     hashed_key=hashed, created_by_id=owner, role_id=role_id))
        await s.commit()
    return {"X-API-Key": raw}


async def _audit(maker, action: str, ws: int):
    async with maker() as s:
        return (await s.exec(select(AuditLog).where(
            AuditLog.action == action, AuditLog.workspace_id == ws))).all()


# ---------------------------------------------------------------------------
# 1. Deleting a workspace
# ---------------------------------------------------------------------------

async def test_workspace_admin_can_delete_a_workspace(maker, scene, client):
    r = await client.delete(f"/api/workspaces/{scene.other_ws}?dry_run=true", headers=ADMIN)
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/workspaces/{scene.other_ws}?confirm=acme second ws", headers=ADMIN)
    assert r.status_code == 200, r.text
    async with maker() as s:
        assert await s.get(Workspace, scene.other_ws) is None


async def test_plain_member_cannot_delete_a_workspace(scene, client):
    r = await client.delete(f"/api/workspaces/{scene.ws}?dry_run=true",
                            headers=_bearer("editor@acme.example.com"))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 2. Removing a member takes their access with them
# ---------------------------------------------------------------------------

async def test_removing_a_member_revokes_grants_teams_overrides_and_keys(maker, scene, client):
    key = await _key(maker, owner=scene.editor, ws=scene.ws)
    async with maker() as s:
        assert await access_service.has_project_access(scene.editor, scene.p1, s)
        assert await access_service.has_project_access(scene.editor, scene.p2, s)

    r = await client.delete(f"/api/workspaces/{scene.ws}/users/{scene.editor}", headers=ADMIN)
    assert r.status_code == 200, r.text

    async with maker() as s:
        for model, col in ((UserProjectAccess, UserProjectAccess.user_id),
                           (UserTeam, UserTeam.user_id),
                           (UserTestCaseAccess, UserTestCaseAccess.user_id),
                           (UserWorkspace, UserWorkspace.user_id)):
            assert (await s.exec(select(model).where(col == scene.editor))).all() == []
        assert not await access_service.has_project_access(scene.editor, scene.p1, s)
        assert not await access_service.has_project_access(scene.editor, scene.p2, s)
        assert not await access_service.has_test_case_access(scene.editor, scene.c2, s)
        keys = (await s.exec(select(ApiKey).where(ApiKey.created_by_id == scene.editor))).all()
        assert keys and all(k.revoked_at is not None for k in keys)
    assert (await client.get(f"/api/suites/{scene.s1}", headers=key)).status_code == 401

    rows = await _audit(maker, "role_revoke", scene.ws)
    assert len(rows) == 1
    assert rows[0].user_id == scene.admin
    assert rows[0].changes["user_id"] == scene.editor
    assert rows[0].changes["removed"]["project_grants"] == 1
    assert rows[0].changes["removed"]["teams"] == 1


async def test_the_last_workspace_admin_cannot_be_removed(scene, client):
    r = await client.delete(f"/api/workspaces/{scene.ws}/users/{scene.admin}", headers=ADMIN)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 3 + 5. API keys are held to their workspace, project and role
# ---------------------------------------------------------------------------

async def test_project_scoped_key_stays_in_its_project(maker, scene, client):
    key = await _key(maker, owner=scene.admin, ws=scene.ws, project=scene.p1)
    assert (await client.get(f"/api/suites/{scene.s1}", headers=key)).status_code == 200
    assert (await client.get(f"/api/cases/{scene.c1}", headers=key)).status_code == 200

    # The other project in the same workspace — the owner can see it, the key not.
    assert (await client.get(f"/api/suites/{scene.s2}", headers=ADMIN)).status_code == 200
    assert (await client.get(f"/api/suites/{scene.s2}", headers=key)).status_code in DENIED
    assert (await client.get(f"/api/cases/{scene.c2}", headers=key)).status_code in DENIED
    assert (await client.get(f"/api/cases?project_id={scene.p2}", headers=key)).status_code in DENIED
    r = await client.put(f"/api/cases/{scene.c2}", json={"name": "pwned"}, headers=key)
    assert r.status_code in DENIED
    r = await client.post(f"/api/suites/{scene.s2}/cases", json={"name": "x", "steps": []}, headers=key)
    assert r.status_code in DENIED
    assert (await client.delete(f"/api/cases/{scene.c2}", headers=key)).status_code in DENIED
    assert (await client.delete(f"/api/suites/{scene.s2}", headers=key)).status_code in DENIED

    r = await client.get("/api/suites", headers=key)
    assert r.status_code == 200
    assert {x["project_id"] for x in r.json()} == {scene.p1}
    r = await client.get("/api/projects", headers=key)
    assert {x["id"] for x in r.json()} == {scene.p1}

    # Workspace-level administration is outside a project-scoped key.
    r = await client.delete(f"/api/workspaces/{scene.ws}/users/{scene.editor}", headers=key)
    assert r.status_code in DENIED


async def test_key_stays_in_its_workspace(maker, scene, client):
    key = await _key(maker, owner=scene.admin, ws=scene.ws)
    assert (await client.get(f"/api/cases/{scene.c2}", headers=key)).status_code == 200
    assert (await client.get(f"/api/cases/{scene.other_case}", headers=ADMIN)).status_code == 200
    assert (await client.get(f"/api/cases/{scene.other_case}", headers=key)).status_code in DENIED
    r = await client.get("/api/suites", headers=key)
    assert scene.other_p not in {x["project_id"] for x in r.json()}


async def test_key_role_is_a_ceiling(maker, scene, client):
    viewer = await _key(maker, owner=scene.admin, ws=scene.ws, role="Project Viewer")
    assert (await client.get(f"/api/cases/{scene.c1}", headers=viewer)).status_code == 200
    r = await client.put(f"/api/cases/{scene.c1}", json={"name": "renamed"}, headers=viewer)
    assert r.status_code == 403
    r = await client.delete(f"/api/workspaces/{scene.ws}/users/{scene.editor}", headers=viewer)
    assert r.status_code == 403

    unroled = await _key(maker, owner=scene.admin, ws=scene.ws)
    r = await client.put(f"/api/cases/{scene.c1}", json={"name": "renamed"}, headers=unroled)
    assert r.status_code == 200, r.text

    # A role never adds anything: an editor's admin-role key is still an editor.
    boosted = await _key(maker, owner=scene.editor, ws=scene.ws, role="Workspace Admin")
    r = await client.delete(f"/api/workspaces/{scene.ws}/users/{scene.admin}", headers=boosted)
    assert r.status_code == 403


async def test_key_creation_refuses_a_project_from_another_workspace(scene, client):
    r = await client.post(f"/api/workspaces/{scene.ws}/api-keys", headers=ADMIN,
                          json={"name": "x", "workspace_id": scene.ws, "project_id": scene.other_p})
    assert r.status_code == 400
    r = await client.post(f"/api/workspaces/{scene.ws}/api-keys", headers=ADMIN,
                          json={"name": "x", "workspace_id": scene.ws, "project_id": scene.p1})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 4. Teams only take members of their workspace
# ---------------------------------------------------------------------------

async def test_team_refuses_people_outside_its_workspace(maker, scene, client):
    r = await client.post(f"/api/teams/{scene.team}/users/invite", headers=ADMIN,
                          json={"email": "out@else.example.com"})
    assert r.status_code == 400
    r = await client.post(f"/api/teams/{scene.team}/users/{scene.outsider}", headers=ADMIN)
    assert r.status_code == 400
    async with maker() as s:
        assert (await s.exec(select(UserTeam).where(UserTeam.user_id == scene.outsider))).all() == []
        assert not await access_service.has_project_access(scene.outsider, scene.p2, s)

    r = await client.post(f"/api/teams/{scene.team}/users/{scene.admin}", headers=ADMIN)
    assert r.status_code == 200, r.text
    # Unknown emails still become invitations (they join the workspace on signup).
    r = await client.post(f"/api/teams/{scene.team}/users/invite", headers=ADMIN,
                          json={"email": "new@acme.example.com"})
    assert r.status_code == 200 and r.json()["status"] == "invited"


# ---------------------------------------------------------------------------
# 6. Changing a member's workspace role
# ---------------------------------------------------------------------------

async def test_workspace_admin_changes_a_members_role(maker, scene, client):
    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.editor}/role",
                         json={"role": "admin"}, headers=_bearer("editor@acme.example.com"))
    assert r.status_code == 403

    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.editor}/role",
                         json={"role": "admin"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "updated", "role": "admin", "previous_role": "Workspace Member"}
    async with maker() as s:
        assert await access_service.get_project_role(scene.editor, scene.p2, s) == "admin"
        assert await rbac_service.has_permission(s, scene.editor, "workspace:manage_users",
                                                 workspace_id=scene.ws)
    rows = await _audit(maker, "role_grant", scene.ws)
    assert rows and rows[-1].changes["role"] == "Workspace Admin"
    assert rows[-1].changes["previous_role"] == "Workspace Member"

    # Now there are two admins, so the original one can be demoted; the last one cannot.
    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.admin}/role",
                         json={"role": "member"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.editor}/role",
                         json={"role": "member"}, headers=_bearer("editor@acme.example.com"))
    assert r.status_code == 409

    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.outsider}/role",
                         json={"role": "member"}, headers=_bearer("editor@acme.example.com"))
    assert r.status_code == 404
    r = await client.put(f"/api/workspaces/{scene.ws}/users/{scene.editor}/role",
                         json={"role": "owner"}, headers=_bearer("editor@acme.example.com"))
    assert r.status_code == 422


async def test_tenant_admin_assign_changes_an_existing_role(maker, scene, client):
    r = await client.post(f"/api/admin/users/{scene.editor}/assignments", headers=ADMIN,
                          json={"workspace_ids": [scene.ws, scene.other_ws], "role": "admin"})
    assert r.status_code == 200, r.text
    by_ws = {x["workspace_id"]: x for x in r.json()["results"]}
    assert by_ws[scene.ws]["status"] == "updated"
    assert by_ws[scene.other_ws]["status"] == "success"
    async with maker() as s:
        rows = (await s.exec(select(UserWorkspace).where(UserWorkspace.user_id == scene.editor))).all()
        assert {r.workspace_id: r.role for r in rows} == {scene.ws: "admin", scene.other_ws: "admin"}


async def test_tenant_users_list_names_workspaces_and_status(maker, scene, client):
    r = await client.get("/api/admin/users", headers=ADMIN)
    assert r.status_code == 200, r.text
    by_email = {u["email"]: u for u in r.json()}
    assert by_email["admin@acme.example.com"]["workspace"] == "acme second ws, acme ws"
    assert by_email["editor@acme.example.com"]["workspace"] == "acme ws"
    assert by_email["editor@acme.example.com"]["status"] == "active"


async def test_me_reports_mfa_state_only_for_the_caller(maker, scene, client):
    async with maker() as s:
        u = await s.get(User, scene.admin)
        u.mfa_enabled = True
        s.add(u)
        await s.commit()
    r = await client.get("/api/auth/me", headers=ADMIN)
    assert r.status_code == 200 and r.json()["mfa_enabled"] is True


# ---------------------------------------------------------------------------
# 6. Endpoints that check workspace membership with their own query
#    (access_service.workspace_membership / is_workspace_admin) are held to
#    the key's workspace, project scope and role too.
# ---------------------------------------------------------------------------

# Workspace-level reads, one (or more) per module that used to query
# UserWorkspace itself. The owner (admin) is a member/admin of BOTH workspaces.
_WS_READS = [
    "/api/workspaces/{ws}/users",                 # workspaces
    "/api/workspaces/{ws}/members/detailed",      # workspaces
    "/api/workspaces/{ws}/teams",                 # workspaces
    "/api/workspaces/{ws}/proposal-policy",       # agent_ownership
    "/api/workspaces/{ws}/audit",                 # audit
    "/api/workspaces/{ws}/ai-policy",             # policies
    "/api/workspaces/{ws}/policy-decisions",      # policies (admin)
    "/api/workspaces/{ws}/portfolio",             # reports
    "/api/workspaces/{ws}/executive-summary",     # reports
    "/api/workspaces/{ws}/api-keys",              # api_keys
    "/api/workspaces/{ws}/billing",               # billing
    "/api/workspaces/{ws}/llm-usage",             # llm_usage
    "/api/workspaces/{ws}/personas",              # personas
    "/api/workspaces/{ws}/issue-trackers",        # issue_trackers
    "/api/workspaces/{ws}/security",              # security
    "/api/workspaces/{ws}/finding-sla",           # security
    "/api/workspaces/{ws}/compliance/members",    # compliance
    "/api/costs/workspaces/{ws}",                 # costs (admin view)
]


async def test_workspace_reads_stay_in_the_keys_workspace(maker, scene, client):
    key = await _key(maker, owner=scene.admin, ws=scene.ws)
    for path in _WS_READS:
        own = await client.get(path.format(ws=scene.ws), headers=key)
        assert own.status_code == 200, (path, own.status_code, own.text)
        # The owner reaches the other workspace; the key minted for ws does not.
        assert (await client.get(path.format(ws=scene.other_ws), headers=ADMIN)).status_code == 200, path
        other = await client.get(path.format(ws=scene.other_ws), headers=key)
        assert other.status_code in DENIED, (path, other.status_code)

    r = await client.get("/api/workspaces", headers=key)
    assert [w["id"] for w in r.json()] == [scene.ws]
    r = await client.get("/api/workspaces", headers=ADMIN)
    assert {w["id"] for w in r.json()} == {scene.ws, scene.other_ws}


async def test_project_scoped_key_fails_workspace_wide_checks(maker, scene, client):
    key = await _key(maker, owner=scene.admin, ws=scene.ws, project=scene.p1)
    for path in ("/api/workspaces/{ws}/users", "/api/workspaces/{ws}/proposal-policy",
                 "/api/workspaces/{ws}/audit", "/api/workspaces/{ws}/policy-decisions",
                 "/api/workspaces/{ws}/api-keys", "/api/workspaces/{ws}/billing",
                 "/api/workspaces/{ws}/personas", "/api/workspaces/{ws}/issue-trackers",
                 "/api/workspaces/{ws}/security", "/api/workspaces/{ws}/llm-usage"):
        r = await client.get(path.format(ws=scene.ws), headers=key)
        assert r.status_code == 403, (path, r.status_code)
    # Not an admin view: the cost report falls back to the key's own project.
    r = await client.get(f"/api/costs/workspaces/{scene.ws}", headers=key)
    assert r.status_code == 200
    # Reports are narrowed per project, so the key sees its own project only.
    r = await client.get(f"/api/workspaces/{scene.ws}/portfolio", headers=key)
    assert r.status_code == 200, r.text
    assert {p["project_id"] for p in r.json()["projects"]} == {scene.p1}
    # Creating a project / the sample project is workspace-wide.
    r = await client.post("/api/projects", headers=key,
                          json={"name": "new", "workspace_id": scene.ws})
    assert r.status_code == 403
    r = await client.post("/api/onboarding/sample-project", headers=key,
                          json={"workspace_id": scene.ws})
    assert r.status_code == 403


async def test_workspace_writes_respect_the_keys_workspace_and_role(maker, scene, client):
    key = await _key(maker, owner=scene.admin, ws=scene.ws)
    viewer = await _key(maker, owner=scene.admin, ws=scene.ws, role="Project Viewer")

    # projects / onboarding: creating in another workspace, or as a viewer key.
    r = await client.post("/api/projects", headers=key,
                          json={"name": "elsewhere 2", "workspace_id": scene.other_ws})
    assert r.status_code == 403
    r = await client.post("/api/projects", headers=viewer,
                          json={"name": "viewer made", "workspace_id": scene.ws})
    assert r.status_code == 403
    r = await client.post("/api/projects", headers=key,
                          json={"name": "made by key", "workspace_id": scene.ws})
    assert r.status_code == 200, r.text
    r = await client.post("/api/onboarding/sample-project", headers=key,
                          json={"workspace_id": scene.other_ws})
    assert r.status_code == 403
    r = await client.post("/api/onboarding/sample-project", headers=viewer,
                          json={"workspace_id": scene.ws})
    assert r.status_code == 403

    # security: workspace admin settings, admin-only — not for a viewer key.
    r = await client.get(f"/api/workspaces/{scene.ws}/security", headers=viewer)
    assert r.status_code == 200 and r.json()["can_edit"] is False
    r = await client.put(f"/api/workspaces/{scene.ws}/security", headers=viewer,
                         json={"active_scan_enabled": True})
    assert r.status_code == 403
    r = await client.put(f"/api/workspaces/{scene.ws}/finding-sla", headers=viewer,
                         json={"sla_days": {"high": 5}})
    assert r.status_code == 403
    r = await client.put(f"/api/workspaces/{scene.other_ws}/security", headers=key,
                         json={"active_scan_enabled": True})
    assert r.status_code == 403
    # policies: the admin-only decision log refuses the viewer key (role ceiling).
    r = await client.get(f"/api/workspaces/{scene.ws}/policy-decisions", headers=viewer)
    assert r.status_code == 403
    # personas: editor+ (session state is a credential) — a viewer key is not.
    r = await client.post(f"/api/workspaces/{scene.ws}/personas", headers=viewer,
                          json={"workspace_id": scene.ws, "name": "p"})
    assert r.status_code == 403

    # projects listing reports the key's level, not the owner's.
    r = await client.get("/api/projects", headers=viewer)
    assert r.status_code == 200
    assert r.json() and {p["access_level"] for p in r.json()} == {"viewer"}

    # workspaces: leaving a team yourself is a write in that workspace.
    ed_viewer = await _key(maker, owner=scene.editor, ws=scene.ws, role="Project Viewer")
    r = await client.delete(f"/api/teams/{scene.team}/users/{scene.editor}", headers=ed_viewer)
    assert r.status_code == 403
    async with maker() as s:
        assert (await s.exec(select(UserTeam).where(UserTeam.user_id == scene.editor))).first()


async def test_project_gated_tracker_paths_stay_in_the_keys_workspace(maker, scene, client):
    """requirements + triage: the tracker is wielded on behalf of a project, so
    a key for ws cannot reach the other workspace's project."""
    from app.models import FailureCluster
    async with maker() as s:
        cluster = FailureCluster(project_id=scene.other_p, signature="sig", title="t",
                                 category="other")
        s.add(cluster)
        await s.commit()
        cluster_id = cluster.id
    key = await _key(maker, owner=scene.admin, ws=scene.ws)
    r = await client.post(f"/api/failure-clusters/{cluster_id}/ticket", headers=key,
                          json={"config_id": 1})
    assert r.status_code in DENIED
    r = await client.post(f"/api/projects/{scene.other_p}/requirements/search", headers=key,
                          json={"config_id": 1, "query": "x"})
    assert r.status_code in DENIED
