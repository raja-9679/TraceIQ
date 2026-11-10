"""Comments, assignment and notifications on a run's results, through the API.

    ./run-tests-live.sh tests/integration/test_failure_discussion_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

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
from app.core.config import db_url_for, settings                 # noqa: E402
from app.models import (                                          # noqa: E402
    ApiKey, AuditLog, Notification, Project, ResultAssignment, ResultComment, Tenant,
    TestCase, TestCaseResult, TestRun, TestStatus, TestSuite, User, UserProjectAccess,
    UserWorkspace, Workspace,
)
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://collab.test") as c:
        yield c
    await engine.dispose()


def _auth(email: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


async def _run(s, project_id, suite_id, case_id, *, failed=True):
    run = TestRun(project_id=project_id, test_suite_id=suite_id,
                  status=TestStatus.FAILED if failed else TestStatus.PASSED)
    s.add(run)
    await s.flush()
    bad = TestCaseResult(test_run_id=run.id, test_case_id=case_id, test_name="Sign in",
                         status=TestStatus.FAILED if failed else TestStatus.PASSED, duration_ms=10)
    good = TestCaseResult(test_run_id=run.id, test_name="Home loads", status=TestStatus.PASSED, duration_ms=5)
    s.add(bad); s.add(good)
    await s.flush()
    return run.id, bad.id, good.id


async def _scene(maker):
    async with maker() as s:
        users = {name: User(email=f"{name}@collab.test", hashed_password="x", full_name=name.title())
                 for name in ("admin", "sam", "eve", "vee", "out", "gone")}
        users["gone"].is_active = False
        for u in users.values():
            s.add(u)
        await s.flush()
        tenant = Tenant(name="Collab", owner_id=users["admin"].id)
        s.add(tenant)
        await s.flush()
        ws = Workspace(name="WS", tenant_id=tenant.id)
        s.add(ws)
        await s.flush()
        admin_role = await rbac_service.get_role_by_name(s, "Workspace Admin")
        member_role = await rbac_service.get_role_by_name(s, "Workspace Member")
        s.add(UserWorkspace(user_id=users["admin"].id, workspace_id=ws.id, role="admin", role_id=admin_role.id))
        for name in ("sam", "eve", "vee", "gone"):
            s.add(UserWorkspace(user_id=users[name].id, workspace_id=ws.id, role="member", role_id=member_role.id))
        project = Project(name="Shop", workspace_id=ws.id)
        s.add(project)
        await s.flush()
        for name, level in (("sam", "editor"), ("eve", "editor"), ("vee", "viewer"), ("gone", "editor")):
            s.add(UserProjectAccess(user_id=users[name].id, project_id=project.id, access_level=level))
        suite = TestSuite(name="Smoke", project_id=project.id)
        s.add(suite)
        await s.flush()
        case = TestCase(name="Sign in", test_suite_id=suite.id, project_id=project.id, steps=[])
        s.add(case)
        await s.flush()
        run_id, bad_id, good_id = await _run(s, project.id, suite.id, case.id)
        await s.commit()
        return {
            "ws": ws.id, "project": project.id, "suite": suite.id, "case": case.id,
            "run": run_id, "bad": bad_id, "good": good_id,
            "ids": {n: u.id for n, u in users.items()},
            **{n.upper(): _auth(u.email) for n, u in users.items()},
        }


def _base(sc, result="bad"):
    return f"/api/runs/{sc['run']}/results/{sc[result]}"


async def _bell(client, who):
    r = await client.get("/api/notifications", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def test_comments_are_plain_text_and_role_checked(maker, client):
    sc = await _scene(maker)
    url = _base(sc)
    assert (await client.post(f"{url}/comments", headers=sc["VEE"], json={"body": "hi"})).status_code == 403
    assert (await client.post(f"{url}/comments", headers=sc["OUT"], json={"body": "hi"})).status_code == 403
    assert (await client.post(f"{url}/comments", headers=sc["SAM"], json={"body": "  \x00\x07 "})).status_code == 422

    r = await client.post(f"{url}/comments", headers=sc["SAM"],
                          json={"body": "  <script>alert(1)</script>\x00 flaky on staging\x1b[31m  "})
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "<script>alert(1)</script> flaky on staging[31m"   # stored as text, shown escaped

    seen = (await client.get(f"{url}/discussion", headers=sc["VEE"])).json()
    assert [c["author"]["name"] for c in seen["comments"]] == ["Sam"]
    assert seen["can_write"] is False and seen["comments"][0]["can_delete"] is False
    assert (await client.get(f"{url}/discussion", headers=sc["OUT"])).status_code == 403

    cid = r.json()["id"]
    # Another editor cannot delete it; the author and a project admin can.
    assert (await client.delete(f"{url}/comments/{cid}", headers=sc["EVE"])).status_code == 403
    assert (await client.delete(f"{_base(sc, 'good')}/comments/{cid}", headers=sc["SAM"])).status_code == 404
    assert (await client.delete(f"{url}/comments/{cid}", headers=sc["SAM"])).status_code == 200
    r2 = await client.post(f"{url}/comments", headers=sc["SAM"], json={"body": "second"})
    assert (await client.delete(f"{url}/comments/{r2.json()['id']}", headers=sc["ADMIN"])).status_code == 200

    async with maker() as s:
        # The audit log is append-only and survives the wipe between tests.
        rows = (await s.exec(select(AuditLog).where(AuditLog.entity_type == "result_comment",
                                                    AuditLog.workspace_id == sc["ws"]))).all()
        assert sorted(a.action for a in rows) == ["result_comment.create"] * 2 + ["result_comment.delete"] * 2
        assert all("flaky on staging" not in str(a.changes) for a in rows)   # where and how long, not what


async def test_assign_comment_done_reach_the_right_people_once(maker, client):
    sc = await _scene(maker)
    url = _base(sc)
    r = await client.put(f"{url}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["sam"]})
    assert r.status_code == 200, r.text
    assert r.json()["assignee"]["name"] == "Sam" and r.json()["status"] == "open" and r.json()["carried"] is False

    bell = await _bell(client, sc["SAM"])
    assert bell["unread"] == 1
    item = bell["items"][0]
    assert (item["kind"], item["actor"]["name"], item["test_name"], item["project_name"]) == ("assigned", "Admin", "Sign in", "Shop")
    assert (item["run_id"], item["result_id"]) == (sc["run"], sc["bad"])
    assert (await _bell(client, sc["ADMIN"]))["items"] == []     # not told about your own action

    # Two comments while the first notification is unread: one notification.
    for body in ("can you look?", "it fails on the password step"):
        assert (await client.post(f"{url}/comments", headers=sc["ADMIN"], json={"body": body})).status_code == 200
    kinds = [i["kind"] for i in (await _bell(client, sc["SAM"]))["items"]]
    assert sorted(kinds) == ["assigned", "comment"]

    # Sam answers: the earlier commenter hears, Sam does not notify himself.
    await client.post(f"{url}/comments", headers=sc["SAM"], json={"body": "on it"})
    assert [i["kind"] for i in (await _bell(client, sc["ADMIN"]))["items"]] == ["comment"]
    assert len((await _bell(client, sc["SAM"]))["items"]) == 2

    # Reading, and only your own.
    ids = [i["id"] for i in (await _bell(client, sc["SAM"]))["items"]]
    assert (await client.post("/api/notifications/read", headers=sc["ADMIN"], json={"ids": ids})).json() == {"marked": 0}
    assert (await _bell(client, sc["SAM"]))["unread"] == 2
    assert (await client.post("/api/notifications/read", headers=sc["SAM"], json={"ids": ids[:1]})).json() == {"marked": 1}
    assert (await client.get("/api/notifications/unread-count", headers=sc["SAM"])).json() == {"unread": 1}
    assert (await client.post("/api/notifications/read", headers=sc["SAM"], json={"all": True})).json() == {"marked": 1}
    # Read ones no longer hold back a new notification.
    await client.post(f"{url}/comments", headers=sc["ADMIN"], json={"body": "any news?"})
    assert (await client.get("/api/notifications/unread-count", headers=sc["SAM"])).json() == {"unread": 1}

    # Sam closes it: whoever assigned it hears.
    mine = (await client.get("/api/assignments/mine", headers=sc["SAM"])).json()
    assert [(m["test_name"], m["status"], m["assigned_by"]["name"]) for m in mine] == [("Sign in", "open", "Admin")]
    r = await client.post(f"{url}/assignment/done", headers=sc["SAM"], json={"done": True})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert "assignment_done" in [i["kind"] for i in (await _bell(client, sc["ADMIN"]))["items"]]
    assert (await client.get("/api/assignments/mine", headers=sc["SAM"])).json() == []
    # Doing it twice says nothing new.
    before = len((await _bell(client, sc["ADMIN"]))["items"])
    await client.post(f"{url}/assignment/done", headers=sc["SAM"], json={"done": True})
    assert len((await _bell(client, sc["ADMIN"]))["items"]) == before


async def test_only_people_who_can_edit_the_project_can_be_assigned(maker, client):
    sc = await _scene(maker)
    url = _base(sc)
    answers = set()
    for who in ("vee", "out", "gone"):
        r = await client.put(f"{url}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"][who]})
        assert r.status_code == 400
        answers.add(r.json()["detail"])
    r = await client.put(f"{url}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": 987654})
    answers.add(r.json()["detail"])
    assert len(answers) == 1          # one answer: no hint about which accounts exist
    assert (await client.put(f"{url}/assignment", headers=sc["VEE"], json={"assignee_user_id": sc["ids"]["sam"]})).status_code == 403

    names = [p["name"] for p in (await client.get(f"/api/projects/{sc['project']}/assignee-candidates", headers=sc["VEE"])).json()]
    assert names == ["Admin", "Eve", "Sam"]
    assert (await client.get(f"/api/projects/{sc['project']}/assignee-candidates", headers=sc["OUT"])).status_code == 403

    # Reassigning tells the new person; unassigning is audited.
    await client.put(f"{url}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["sam"]})
    await client.put(f"{url}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["eve"]})
    assert [i["kind"] for i in (await _bell(client, sc["EVE"]))["items"]] == ["assigned"]
    assert (await client.delete(f"{url}/assignment", headers=sc["EVE"])).status_code == 200
    assert (await client.get(f"{url}/discussion", headers=sc["EVE"])).json()["assignment"] is None
    async with maker() as s:
        acts = [a.action for a in (await s.exec(select(AuditLog).where(
            AuditLog.entity_type == "result_assignment", AuditLog.workspace_id == sc["ws"]).order_by(AuditLog.id))).all()]
        assert acts == ["result_assignment.set", "result_assignment.set", "result_assignment.clear"]


async def test_an_open_assignment_follows_the_test_into_the_next_failure(maker, client):
    sc = await _scene(maker)
    await client.put(f"{_base(sc)}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["sam"]})
    async with maker() as s:
        fail_run, fail_bad, _ = await _run(s, sc["project"], sc["suite"], sc["case"])
        pass_run, pass_ok, _ = await _run(s, sc["project"], sc["suite"], sc["case"], failed=False)
        await s.commit()

    d = (await client.get(f"/api/runs/{fail_run}/results/{fail_bad}/discussion", headers=sc["EVE"])).json()
    assert d["assignment"]["carried"] is True and d["assignment"]["assignee"]["name"] == "Sam"
    assert (d["assignment"]["run_id"], d["assignment"]["result_id"]) == (sc["run"], sc["bad"])
    summary = (await client.get(f"/api/runs/{fail_run}/discussion", headers=sc["EVE"])).json()["results"]
    assert summary[str(fail_bad)] == {"comments": 0, "assignee": {"id": sc["ids"]["sam"], "name": "Sam"},
                                      "status": "open", "carried": True}
    # A green result of the same test does not look "assigned".
    d = (await client.get(f"/api/runs/{pass_run}/results/{pass_ok}/discussion", headers=sc["EVE"])).json()
    assert d["assignment"] is None
    assert (await client.get(f"/api/runs/{pass_run}/discussion", headers=sc["EVE"])).json()["results"] == {}

    # A comment on the later failure reaches the person carrying it.
    await client.post(f"/api/runs/{fail_run}/results/{fail_bad}/comments", headers=sc["EVE"], json={"body": "again"})
    assert "comment" in [i["kind"] for i in (await _bell(client, sc["SAM"]))["items"]]

    # Closing the original closes it everywhere.
    await client.post(f"{_base(sc)}/assignment/done", headers=sc["SAM"], json={"done": True})
    d = (await client.get(f"/api/runs/{fail_run}/results/{fail_bad}/discussion", headers=sc["EVE"])).json()
    assert d["assignment"] is None


async def test_losing_access_hides_notifications_and_api_keys_have_none(maker, client):
    sc = await _scene(maker)
    await client.put(f"{_base(sc)}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["sam"]})
    assert (await _bell(client, sc["SAM"]))["unread"] == 1

    raw, prefix, hashed = generate_api_key()
    async with maker() as s:
        s.add(ApiKey(workspace_id=sc["ws"], name="k", prefix=prefix, hashed_key=hashed, created_by_id=sc["ids"]["sam"]))
        await s.commit()
    key = {"X-API-Key": raw}
    for path in ("/api/notifications", "/api/notifications/unread-count", "/api/assignments/mine"):
        assert (await client.get(path, headers=key)).status_code == 403
    assert (await client.post("/api/notifications/read", headers=key, json={"all": True})).status_code == 403

    async with maker() as s:
        grant = (await s.exec(select(UserProjectAccess).where(UserProjectAccess.user_id == sc["ids"]["sam"]))).one()
        await s.delete(grant)
        await s.commit()
    bell = await _bell(client, sc["SAM"])
    assert bell == {"items": [], "unread": 0}
    assert (await client.get("/api/notifications/unread-count", headers=sc["SAM"])).json() == {"unread": 0}
    assert (await client.get("/api/assignments/mine", headers=sc["SAM"])).json() == []
    assert (await client.get(f"{_base(sc)}/discussion", headers=sc["SAM"])).status_code == 403


async def test_results_must_belong_to_the_run_and_the_thread_goes_with_the_run(maker, client):
    sc = await _scene(maker)
    async with maker() as s:
        other_run, other_bad, _ = await _run(s, sc["project"], sc["suite"], sc["case"])
        await s.commit()
    assert (await client.get(f"/api/runs/{sc['run']}/results/{other_bad}/discussion", headers=sc["ADMIN"])).status_code == 404
    assert (await client.post(f"/api/runs/{sc['run']}/results/{other_bad}/comments", headers=sc["ADMIN"], json={"body": "x"})).status_code == 404
    assert (await client.get("/api/runs/999999/discussion", headers=sc["ADMIN"])).status_code == 404

    await client.post(f"{_base(sc)}/comments", headers=sc["ADMIN"], json={"body": "note"})
    await client.put(f"{_base(sc)}/assignment", headers=sc["ADMIN"], json={"assignee_user_id": sc["ids"]["sam"]})
    assert (await client.delete(f"/api/runs/{sc['run']}", headers=sc["ADMIN"])).status_code in (200, 204)
    async with maker() as s:
        assert (await s.exec(select(ResultComment))).all() == []
        assert (await s.exec(select(ResultAssignment))).all() == []
    # The notification pointed at a result that no longer exists: not shown.
    assert (await _bell(client, sc["SAM"]))["items"] == []


async def test_old_notifications_are_swept(maker):
    sc = await _scene(maker)
    now = datetime.utcnow()
    async with maker() as s:
        def note(**kw):
            return Notification(user_id=sc["ids"]["sam"], project_id=sc["project"], kind="comment",
                                run_id=sc["run"], result_id=sc["bad"], **kw)
        s.add(note(created_at=now - timedelta(days=40), read_at=now - timedelta(days=31)))   # read long ago
        s.add(note(created_at=now - timedelta(days=100)))                                    # never read, too old
        s.add(note(created_at=now - timedelta(days=40)))                                     # unread, kept
        s.add(note(created_at=now - timedelta(days=40), read_at=now - timedelta(days=2)))    # read recently, kept
        await s.commit()
    from app.tasks.cleanup_tasks import purge_notifications
    assert purge_notifications() == 2
    async with maker() as s:
        assert len((await s.exec(select(Notification))).all()) == 2
