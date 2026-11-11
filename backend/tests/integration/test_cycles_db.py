"""Test cycles through the API, against a real Postgres.

    ./run-tests-live.sh tests/integration/test_cycles_db.py -q
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
    ApiKey, AuditLog, ExecutorType, Project, ReleaseSignoff, Tenant, TestCase, TestCaseResult,
    TestCycle, TestCycleItem, TestRun, TestStatus, TestSuite, User, UserProjectAccess,
    UserWorkspace, Workspace,
)
from app.services.rbac_service import rbac_service                # noqa: E402

_WIPE = "TRUNCATE users, tenant, workspace CASCADE"
_MANUAL_STEPS = [{"id": "s1", "type": "manual-step", "value": "Open the invoice",
                  "params": {"expected": "VAT shown"}}]


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://cycles.test") as c:
        yield c
    await engine.dispose()


def _auth(email: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


async def _scene(maker):
    """Project "Shop": suites Checkout (2 automated + 1 manual) and Billing
    (child Invoices: 1 manual). A second project in another tenant."""
    async with maker() as s:
        users = {n: User(email=f"{n}@cycles.test", hashed_password="x", full_name=n.title())
                 for n in ("admin", "sam", "eve", "vee", "out", "gone")}
        users["gone"].is_active = False
        for u in users.values():
            s.add(u)
        await s.flush()
        tenant = Tenant(name="Cycles", owner_id=users["admin"].id)
        other_tenant = Tenant(name="Other", owner_id=users["out"].id)
        s.add(tenant); s.add(other_tenant)
        await s.flush()
        ws = Workspace(name="WS", tenant_id=tenant.id)
        other_ws = Workspace(name="Other WS", tenant_id=other_tenant.id)
        s.add(ws); s.add(other_ws)
        await s.flush()
        admin_role = await rbac_service.get_role_by_name(s, "Workspace Admin")
        member_role = await rbac_service.get_role_by_name(s, "Workspace Member")
        s.add(UserWorkspace(user_id=users["admin"].id, workspace_id=ws.id, role="admin", role_id=admin_role.id))
        s.add(UserWorkspace(user_id=users["out"].id, workspace_id=other_ws.id, role="admin", role_id=admin_role.id))
        for n in ("sam", "eve", "vee", "gone"):
            s.add(UserWorkspace(user_id=users[n].id, workspace_id=ws.id, role="member", role_id=member_role.id))
        project = Project(name="Shop", workspace_id=ws.id)
        other = Project(name="Elsewhere", workspace_id=other_ws.id)
        s.add(project); s.add(other)
        await s.flush()
        for n, level in (("sam", "editor"), ("eve", "editor"), ("vee", "viewer"), ("gone", "editor")):
            s.add(UserProjectAccess(user_id=users[n].id, project_id=project.id, access_level=level))
        checkout = TestSuite(name="Checkout", project_id=project.id)
        billing = TestSuite(name="Billing", project_id=project.id)
        foreign_suite = TestSuite(name="Theirs", project_id=other.id)
        s.add(checkout); s.add(billing); s.add(foreign_suite)
        await s.flush()
        invoices = TestSuite(name="Invoices", project_id=project.id, parent_id=billing.id)
        s.add(invoices)
        await s.flush()

        def case(name, suite, manual=False, pid=project.id):
            c = TestCase(name=name, test_suite_id=suite.id, project_id=pid,
                         steps=_MANUAL_STEPS if manual else [],
                         executor=ExecutorType.MANUAL if manual else ExecutorType.UI_PLAYWRIGHT)
            s.add(c)
            return c
        pay, cart = case("Pay by card", checkout), case("Add to cart", checkout)
        receipt = case("Receipt looks right", checkout, manual=True)
        vat = case("VAT on invoice", invoices, manual=True)
        foreign = case("Not ours", foreign_suite, pid=other.id)
        await s.flush()
        await s.commit()
        return {
            "ws": ws.id, "project": project.id, "other_project": other.id,
            "checkout": checkout.id, "billing": billing.id, "invoices": invoices.id,
            "pay": pay.id, "cart": cart.id, "receipt": receipt.id, "vat": vat.id,
            "foreign": foreign.id, "foreign_suite": foreign_suite.id, "ids": {n: u.id for n, u in users.items()},
            **{n.upper(): _auth(u.email) for n, u in users.items()},
        }


async def _auto_run(maker, sc, cycle_id, results, *, browser="chromium", hours_ago=1,
                    status=TestStatus.PASSED, validation=False, project=None):
    """An automated run in the cycle with {case_id: TestStatus} results."""
    async with maker() as s:
        at = datetime.utcnow() - timedelta(hours=hours_ago)
        run = TestRun(project_id=project or sc["project"], test_suite_id=sc["checkout"],
                      status=status, browser=browser, cycle_id=cycle_id, created_at=at,
                      finalized_at=at, is_validation=validation)
        s.add(run)
        await s.flush()
        for case_id, st in results.items():
            s.add(TestCaseResult(test_run_id=run.id, test_case_id=case_id, test_name="x",
                                 status=st, duration_ms=5))
        await s.commit()
        return run.id


async def _new_cycle(client, sc, who="SAM", **body):
    body = {"name": "Release 2.4", **body}
    r = await client.post(f"/api/projects/{sc['project']}/test-cycles", headers=sc[who], json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _by_case(detail):
    return {i["case_id"]: i for i in detail["items"]}


async def test_create_reads_and_roles(maker, client):
    sc = await _scene(maker)
    url = f"/api/projects/{sc['project']}/test-cycles"
    assert (await client.post(url, headers=sc["VEE"], json={"name": "x"})).status_code == 403
    assert (await client.post(url, headers=sc["OUT"], json={"name": "x"})).status_code == 403
    assert (await client.post(url, headers=sc["SAM"], json={"name": "   "})).status_code == 400
    assert (await client.post(url, headers=sc["SAM"], json={
        "name": "x", "start_date": "2026-11-10", "due_date": "2026-11-01"})).status_code == 400
    # A case or a suite of another project is "not found", and nothing is created.
    assert (await client.post(url, headers=sc["SAM"], json={
        "name": "x", "case_ids": [sc["pay"], sc["foreign"]]})).status_code == 400
    assert (await client.post(url, headers=sc["SAM"], json={
        "name": "x", "suite_id": sc["foreign_suite"]})).status_code == 400

    c = await _new_cycle(client, sc, suite_id=sc["checkout"], case_ids=[sc["vat"]],
                         release_label="2.4.0", due_date="2020-01-01")
    assert c["status"] == "open" and c["created_by"]["name"] == "Sam" and c["can_edit"] is True
    items = _by_case(c)
    assert set(items) == {sc["pay"], sc["cart"], sc["receipt"], sc["vat"]}
    assert items[sc["vat"]]["kind"] == "manual" and items[sc["pay"]]["kind"] == "automated"
    # The top-level suite, not the nested one: runs are started per top-level suite.
    assert items[sc["vat"]]["root_suite_id"] == sc["billing"]
    assert c["progress"]["total"] == 4 and c["progress"]["percent_executed"] == 0
    assert c["manual_cases"] == 2 and c["automated_cases"] == 2
    assert c["overdue"] is True

    seen = (await client.get(f"/api/test-cycles/{c['id']}", headers=sc["VEE"])).json()
    assert seen["can_edit"] is False and len(seen["items"]) == 4
    assert (await client.get(f"/api/test-cycles/{c['id']}", headers=sc["OUT"])).status_code == 403
    listed = (await client.get(url, headers=sc["VEE"])).json()
    assert [x["name"] for x in listed] == ["Release 2.4"] and listed[0]["progress"]["total"] == 4

    async with maker() as s:
        rows = (await s.exec(select(AuditLog).where(AuditLog.workspace_id == sc["ws"],
                                                    AuditLog.entity_type == "test_cycle"))).all()
        assert [r.action for r in rows] == ["create"]


async def test_progress_is_read_from_the_cycles_own_runs(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, suite_id=sc["checkout"])
    cid = c["id"]
    # Older red, newer green on chromium; firefox still red for "cart".
    await _auto_run(maker, sc, cid, {sc["pay"]: TestStatus.FAILED, sc["cart"]: TestStatus.PASSED}, hours_ago=5)
    green = await _auto_run(maker, sc, cid, {sc["pay"]: TestStatus.PASSED}, hours_ago=2)
    red_ff = await _auto_run(maker, sc, cid, {sc["cart"]: TestStatus.FAILED}, browser="firefox", hours_ago=3)
    # None of these may count: a run of no cycle, a validation run, a cancelled result.
    await _auto_run(maker, sc, None, {sc["pay"]: TestStatus.FAILED}, hours_ago=0)
    await _auto_run(maker, sc, cid, {sc["pay"]: TestStatus.FAILED}, hours_ago=0, validation=True)
    await _auto_run(maker, sc, cid, {sc["pay"]: TestStatus.CANCELLED}, hours_ago=0,
                    status=TestStatus.CANCELLED)

    d = (await client.get(f"/api/test-cycles/{cid}", headers=sc["VEE"])).json()
    items = _by_case(d)
    assert items[sc["pay"]]["status"] == "passed" and items[sc["pay"]]["run_id"] == green
    assert items[sc["cart"]]["status"] == "failed" and items[sc["cart"]]["run_id"] == red_ff
    assert items[sc["cart"]]["configurations"] == 2
    assert items[sc["receipt"]]["status"] == "not_run"
    assert d["progress"]["counts"] == {"passed": 1, "failed": 1, "blocked": 0, "skipped": 0, "not_run": 1}
    assert d["progress"]["percent_executed"] == 67
    # Validation runs are not listed as the cycle's runs either.
    assert d["runs_total"] == 4

    # A run may only join an open cycle of its own project.
    r = await client.post(f"/api/runs?suite_id={sc['checkout']}", headers=sc["SAM"],
                          json={"cycle_id": 999999})
    assert r.status_code == 400 and "not found" in r.json()["detail"]


async def test_assigning_and_my_work(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, suite_id=sc["checkout"], case_ids=[sc["vat"]])
    url = f"/api/test-cycles/{c['id']}"
    body = {"case_ids": [sc["receipt"], sc["vat"]], "assignee_user_id": sc["ids"]["eve"]}
    assert (await client.put(f"{url}/assignments", headers=sc["VEE"], json=body)).status_code == 403
    # One answer for a viewer, a deactivated account, an outsider and nobody at all.
    details = set()
    for uid in (sc["ids"]["vee"], sc["ids"]["gone"], sc["ids"]["out"], 987654):
        r = await client.put(f"{url}/assignments", headers=sc["SAM"],
                             json={"case_ids": [sc["vat"]], "assignee_user_id": uid})
        assert r.status_code == 400
        details.add(r.json()["detail"])
    assert len(details) == 1

    d = (await client.put(f"{url}/assignments", headers=sc["SAM"], json=body)).json()
    items = _by_case(d)
    assert items[sc["vat"]]["assignee"]["name"] == "Eve" and items[sc["pay"]]["assignee"] is None
    assert [(g["assignee"] or {}).get("name") for g in d["assignees"]] == ["Eve", None]
    assert d["assignees"][0]["total"] == 2

    mine = (await client.get("/api/test-cycles/assigned-to-me", headers=sc["EVE"])).json()
    assert mine == [{"cycle_id": c["id"], "cycle_name": "Release 2.4", "project_id": sc["project"],
                     "project_name": "Shop", "due_date": None, "assigned": 2, "remaining": 2}]
    assert (await client.get("/api/test-cycles/assigned-to-me", headers=sc["SAM"])).json() == []
    # Losing project access hides it.
    async with maker() as s:
        await s.exec(text("DELETE FROM userprojectaccess WHERE user_id = :u"), params={"u": sc["ids"]["eve"]})
        await s.commit()
    assert (await client.get("/api/test-cycles/assigned-to-me", headers=sc["EVE"])).json() == []

    cleared = (await client.put(f"{url}/assignments", headers=sc["SAM"],
                                json={"case_ids": [sc["vat"]], "assignee_user_id": None})).json()
    assert _by_case(cleared)[sc["vat"]]["assignee"] is None


async def test_manual_testing_from_a_cycle(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, suite_id=sc["checkout"], case_ids=[sc["vat"]], release_label="2.4.0")
    url = f"/api/test-cycles/{c['id']}"
    await client.put(f"{url}/assignments", headers=sc["SAM"],
                     json={"case_ids": [sc["vat"]], "assignee_user_id": sc["ids"]["eve"]})
    assert (await client.post(f"{url}/manual-runs", headers=sc["VEE"], json={})).status_code == 403

    # Eve starts only her own case: one run, under the top-level suite.
    r = await client.post(f"{url}/manual-runs", headers=sc["EVE"], json={"mine_only": True})
    assert r.status_code == 200, r.text
    started = r.json()
    assert [(x["suite_name"], x["cases"]) for x in started["runs"]] == [("Billing", 1)]
    eve_run = started["runs"][0]["id"]
    run = (await client.get(f"/api/manual-runs/{eve_run}", headers=sc["EVE"])).json()
    assert run["cycle_id"] == c["id"] and run["release_label"] == "2.4.0"

    # Sam starts the rest: the case Eve holds is left with her.
    again = (await client.post(f"{url}/manual-runs", headers=sc["SAM"], json={})).json()
    assert [(x["suite_name"], x["cases"]) for x in again["runs"]] == [("Checkout", 1)]
    assert again["already_open"] == 1 and again["open_run_ids"] == [eve_run]
    # Pressing it once more starts nothing.
    third = (await client.post(f"{url}/manual-runs", headers=sc["SAM"], json={})).json()
    assert third["runs"] == [] and third["already_open"] == 2

    result_id = run["results"][0]["id"]
    rec = await client.put(f"/api/manual-runs/{eve_run}/results/{result_id}", headers=sc["EVE"],
                           json={"outcome": "blocked", "notes": "No test invoice"})
    assert rec.status_code == 200, rec.text
    d = (await client.get(url, headers=sc["VEE"])).json()
    vat = _by_case(d)[sc["vat"]]
    assert vat["status"] == "blocked" and vat["tested_by"]["name"] == "Eve" and vat["run_id"] == eve_run
    assert _by_case(d)[sc["receipt"]]["status"] == "not_run"       # started, nothing recorded
    assert {x["kind"] for x in d["runs"]} == {"manual"} and all(x["live"] for x in d["runs"])

    # Recorded but not passed: it is offered again once its run is completed.
    assert (await client.post(f"/api/manual-runs/{eve_run}/complete", headers=sc["EVE"], json={})).status_code == 200
    retry = (await client.post(f"{url}/manual-runs", headers=sc["EVE"], json={"mine_only": True})).json()
    assert [x["cases"] for x in retry["runs"]] == [1]

    # A manual run started from the suite can join the cycle; not another project's.
    r = await client.post("/api/manual-runs", headers=sc["SAM"],
                          json={"suite_id": sc["checkout"], "cycle_id": c["id"]})
    assert r.status_code == 200 and r.json()["cycle_id"] == c["id"]


async def test_close_reopen_edit_and_delete(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, case_ids=[sc["pay"]])
    url = f"/api/test-cycles/{c['id']}"
    run_id = await _auto_run(maker, sc, c["id"], {sc["pay"]: TestStatus.PASSED})

    d = (await client.patch(url, headers=sc["SAM"], json={"name": "  Release  2.5 ", "due_date": "2027-01-31"})).json()
    assert d["name"] == "Release 2.5" and d["due_date"] == "2027-01-31" and d["release_label"] is None
    assert (await client.patch(url, headers=sc["VEE"], json={"name": "x"})).status_code == 403

    d = (await client.post(f"{url}/items", headers=sc["SAM"], json={"suite_id": sc["billing"]})).json()
    assert set(_by_case(d)) == {sc["pay"], sc["vat"]}
    assert (await client.post(f"{url}/items", headers=sc["SAM"], json={"case_ids": [sc["foreign"]]})).status_code == 400
    d = (await client.post(f"{url}/items/remove", headers=sc["SAM"], json={"case_ids": [sc["vat"], 4242]})).json()
    assert set(_by_case(d)) == {sc["pay"]}

    assert (await client.post(f"{url}/close", headers=sc["VEE"])).status_code == 403
    closed = (await client.post(f"{url}/close", headers=sc["EVE"])).json()
    assert closed["status"] == "closed" and closed["closed_by"]["name"] == "Eve"
    assert (await client.post(f"{url}/close", headers=sc["EVE"])).status_code == 409
    # Closed: nothing about it changes and no run can join.
    for method, path, body in (("patch", url, {"name": "y"}),
                               ("post", f"{url}/items", {"case_ids": [sc["cart"]]}),
                               ("post", f"{url}/items/remove", {"case_ids": [sc["pay"]]}),
                               ("put", f"{url}/assignments", {"case_ids": [sc["pay"]], "assignee_user_id": None}),
                               ("post", f"{url}/manual-runs", {})):
        assert (await getattr(client, method)(path, headers=sc["SAM"], json=body)).status_code == 409, path
    r = await client.post("/api/manual-runs", headers=sc["SAM"],
                          json={"suite_id": sc["checkout"], "cycle_id": c["id"]})
    assert r.status_code == 409
    assert (await client.post(f"{url}/reopen", headers=sc["SAM"])).json()["status"] == "open"

    # Delete: project admin only; the run stays, unlinked.
    assert (await client.delete(url, headers=sc["SAM"])).status_code == 403
    assert (await client.delete(url, headers=sc["ADMIN"])).status_code == 200
    assert (await client.get(url, headers=sc["ADMIN"])).status_code == 404
    async with maker() as s:
        assert (await s.get(TestRun, run_id)).cycle_id is None
        assert (await s.exec(select(TestCycleItem))).all() == []
        actions = [r.action for r in (await s.exec(
            select(AuditLog).where(AuditLog.workspace_id == sc["ws"],
                                   AuditLog.entity_type == "test_cycle").order_by(AuditLog.id))).all()]
        assert actions == ["create", "update", "items_added", "items_removed", "close", "reopen", "delete"]


async def test_sign_off_from_a_cycle(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, case_ids=[sc["pay"], sc["cart"]], release_label="2.4.0")
    url = f"/api/projects/{sc['project']}/signoffs"
    body = {"release_label": "2.4.0", "verdict": "approve", "cycle_id": c["id"]}
    # Nothing has run: there is nothing to sign.
    assert (await client.post(url, headers=sc["SAM"], json=body)).status_code == 409
    old = await _auto_run(maker, sc, c["id"], {sc["pay"]: TestStatus.FAILED}, hours_ago=5)
    new = await _auto_run(maker, sc, c["id"], {sc["pay"]: TestStatus.PASSED, sc["cart"]: TestStatus.PASSED})
    r = await client.post(url, headers=sc["SAM"], json=body)
    assert r.status_code == 200, r.text
    # Only the run that holds the current results, not the superseded one.
    assert r.json()["run_ids"] == [new] and r.json()["cycle_id"] == c["id"] and old != new
    d = (await client.get(f"/api/test-cycles/{c['id']}", headers=sc["VEE"])).json()
    assert d["signoff"]["verdict"] == "approve" and d["signoff"]["signed_by"]["name"] == "Sam"
    # Without a cycle run_ids is still required; another project's cycle is "not found".
    assert (await client.post(url, headers=sc["SAM"], json={"release_label": "x", "verdict": "approve"})).status_code == 400
    async with maker() as s:
        foreign = TestCycle(project_id=sc["other_project"], name="Theirs")
        s.add(foreign)
        await s.commit()
        fid = foreign.id
    assert (await client.post(url, headers=sc["SAM"], json={**body, "cycle_id": fid})).status_code == 400
    assert (await client.get(f"/api/test-cycles/{fid}", headers=sc["SAM"])).status_code == 403
    # The sign-off outlives the cycle.
    assert (await client.delete(f"/api/test-cycles/{c['id']}", headers=sc["ADMIN"])).status_code == 200
    async with maker() as s:
        assert len((await s.exec(select(ReleaseSignoff).where(ReleaseSignoff.cycle_id == c["id"]))).all()) == 1


async def test_project_scoped_key_and_project_delete(maker, client):
    sc = await _scene(maker)
    c = await _new_cycle(client, sc, suite_id=sc["checkout"])
    await _auto_run(maker, sc, c["id"], {sc["pay"]: TestStatus.PASSED})
    raw, prefix, hashed = generate_api_key()
    async with maker() as s:
        second = Project(name="Second", workspace_id=sc["ws"])
        s.add(second)
        await s.flush()
        s.add(ApiKey(workspace_id=sc["ws"], name="k", prefix=prefix, hashed_key=hashed,
                     created_by_id=sc["ids"]["admin"], project_id=second.id))
        await s.commit()
    key = {"X-API-Key": raw}
    assert (await client.get(f"/api/test-cycles/{c['id']}", headers=key)).status_code == 403
    assert (await client.get(f"/api/projects/{sc['project']}/test-cycles", headers=key)).status_code == 403
    assert (await client.get("/api/test-cycles/assigned-to-me", headers=key)).status_code == 403

    # Deleting a case drops it from the cycle; deleting the project takes the cycle.
    assert (await client.delete(f"/api/cases/{sc['cart']}", headers=sc["ADMIN"])).status_code in (200, 204)
    d = (await client.get(f"/api/test-cycles/{c['id']}", headers=sc["ADMIN"])).json()
    assert sc["cart"] not in _by_case(d)
    r = await client.delete(f"/api/projects/{sc['project']}", headers=sc["ADMIN"])
    assert r.status_code in (200, 204), r.text
    async with maker() as s:
        assert (await s.exec(select(TestCycle).where(TestCycle.project_id == sc["project"]))).all() == []
        assert (await s.exec(select(TestCycleItem))).all() == []
