"""WP6 inbound integrations against a real Postgres: requirement import,
generation, coverage, incidents (webhook, recurrence, escaped defects) and the
access boundaries around all of them. Tracker HTTP is mocked.

    ./run-tests-live.sh tests/integration/test_inbound_integrations_db.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlmodel import select
from starlette.requests import Request

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import agent_ownership, incidents, requirements   # noqa: E402
from app.core.auth import AuthPrincipal                         # noqa: E402
from app.core.secrets import encrypt_secret                     # noqa: E402
from app.models import (                                        # noqa: E402
    ApiKey, AuditLog, CaseProposal, Incident, IncidentWebhook, IssueTrackerConfig, Project,
    Requirement, RequirementLink, Tenant, TestCase, TestCaseResult, TestRun, TestStatus,
    User, UserProjectAccess, Workspace,
)
from app.services import tracker_import                          # noqa: E402
from app.services.entity_delete import cascade_delete             # noqa: E402

from tests.integration.test_targeted_runs_db import scene, session  # noqa: E402,F401
from tests.integration.denied import DENIED  # noqa: E402


DESC = """As a shopper I want guest checkout.

## Acceptance Criteria
- Guests can pay without creating an account
- The checkout page is fast
Scenario: card declined
  Given a guest with a cart
  When the card is declined
  Then the error "Card declined" is displayed
"""


def _issue(key="SHOP-12", desc=DESC, title="Guest checkout"):
    return {"id": "1", "key": key, "fields": {
        "summary": title, "description": desc, "status": {"name": "To Do"},
        "updated": "2026-10-01T10:00:00.000+0000", "labels": []}}


@pytest.fixture(autouse=True)
def jira(monkeypatch):
    """Mock Jira: `jira.issues` is what the next search returns."""
    state = type("J", (), {"issues": [_issue()], "calls": []})()

    async def validate(url, **kw):
        return url

    def handler(req: httpx.Request):
        state.calls.append(req)
        return httpx.Response(200, json={"issues": state.issues})
    monkeypatch.setattr(tracker_import, "validate_outbound_url", validate)
    monkeypatch.setattr(tracker_import, "_TRANSPORT", httpx.MockTransport(handler))
    return state


@pytest.fixture(autouse=True)
def background_sessions(monkeypatch):
    """Webhook proposals run after the response on the app's own session
    factory; bind it to this test's event loop."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core import database
    from app.core.config import db_url_for, settings
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    monkeypatch.setattr(database, "async_session_factory",
                        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    yield


def me(scene, api_key=None):
    return AuthPrincipal(user=scene.user, api_key=api_key)


async def _tracker(session, workspace_id, provider="jira") -> IssueTrackerConfig:
    cfg = IssueTrackerConfig(workspace_id=workspace_id, provider=provider, name=provider,
                             base_url="https://acme.atlassian.net", auth_user="me@acme.io",
                             auth_secret_encrypted=encrypt_secret("tok"))
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


async def _ws(session, scene) -> int:
    return (await session.get(Project, scene.project_id)).workspace_id


async def _user(session, email, project_id=None, level=None) -> User:
    u = User(email=email, full_name=email, hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if project_id:
        session.add(UserProjectAccess(user_id=u.id, project_id=project_id, access_level=level))
    await session.commit()
    return u


async def _import(session, scene, cfg, keys=("SHOP-12",)):
    return await requirements.import_requirements(
        scene.project_id, requirements.ImportBody(config_id=cfg.id, keys=list(keys)),
        principal=me(scene), session=session)


# --- import / sync ------------------------------------------------------------

async def test_import_parses_criteria_and_resync_keeps_proposals(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    out = await _import(session, scene, cfg)
    assert out["created"] == 1
    req = (await session.exec(select(Requirement))).one()
    crit = req.acceptance_criteria
    assert [c["kind"] for c in crit] == ["list", "list", "gherkin"]
    assert [c["ambiguous"] for c in crit] == [False, True, False]
    assert req.url == "https://acme.atlassian.net/browse/SHOP-12"
    assert jira.calls[0].url.params["jql"] == "key in (SHOP-12)"

    gen = await requirements.generate_for_requirement(
        req.id, requirements.GenerateBody(test_suite_id=scene.root_id),
        principal=me(scene), session=session)
    assert [g["index"] for g in gen["generated"]] == [0, 2]
    assert [a["index"] for a in gen["ambiguous"]] == [1]
    assert all(g["needs_authoring"] for g in gen["generated"])   # null provider → skeletons

    # Re-sync with one criterion reworded: the unchanged ones keep their proposal.
    jira.issues = [_issue(desc=DESC.replace("without creating an account", "as a guest"))]
    await requirements.sync_requirement(req.id, principal=me(scene), session=session)
    await session.refresh(req)
    by_text = {c["text"]: c.get("proposal_id") for c in req.acceptance_criteria}
    assert by_text["Guests can pay as a guest"] is None
    assert any(k.startswith("Scenario: card declined") and v for k, v in by_text.items())

    again = await _import(session, scene, cfg)
    assert (again["created"], again["updated"]) == (0, 1)
    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.action == "requirements_import"))).all()
    assert len(actions) == 2


async def test_title_only_issue_becomes_one_criterion(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    jira.issues = [_issue(key="SHOP-3", desc=None, title="Users can download invoices as PDF")]
    await _import(session, scene, cfg, ["SHOP-3"])
    req = (await session.exec(select(Requirement))).one()
    assert req.acceptance_criteria[0]["kind"] == "title"
    assert req.acceptance_criteria[0]["ambiguous"] is False


async def test_another_workspaces_tracker_is_not_usable(session, scene, jira):
    other_tenant = Tenant(name="Evil", owner_id=scene.user.id)
    session.add(other_tenant)
    await session.flush()
    other_ws = Workspace(name="Evil ws", tenant_id=other_tenant.id)
    session.add(other_ws)
    await session.commit()
    foreign = await _tracker(session, other_ws.id)
    with pytest.raises(HTTPException) as exc:
        await _import(session, scene, foreign)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await requirements.search_tracker(
            scene.project_id, requirements.SearchBody(config_id=foreign.id, jql="project = X"),
            principal=me(scene), session=session)
    assert exc.value.status_code == 404
    assert jira.calls == []


async def test_viewer_reads_but_cannot_import_and_outsider_sees_nothing(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    await _import(session, scene, cfg)
    viewer = await _user(session, "viewer@corp.example.com", scene.project_id, "viewer")
    outsider = await _user(session, "out@else.example.com")
    v = AuthPrincipal(user=viewer, api_key=None)
    listed = await requirements.list_imported(scene.project_id, principal=v, session=session)
    assert len(listed["requirements"]) == 1
    with pytest.raises(HTTPException) as exc:
        await requirements.import_requirements(
            scene.project_id, requirements.ImportBody(config_id=cfg.id, keys=["SHOP-12"]),
            principal=v, session=session)
    assert exc.value.status_code == 403
    req_id = listed["requirements"][0]["id"]
    for call in (
        lambda p: requirements.list_imported(scene.project_id, principal=p, session=session),
        lambda p: requirements.get_requirement(req_id, principal=p, session=session),
        lambda p: incidents.list_incidents(scene.project_id, principal=p, session=session),
    ):
        with pytest.raises(HTTPException) as exc:
            await call(AuthPrincipal(user=outsider, api_key=None))
        assert exc.value.status_code in DENIED


async def test_project_scoped_key_cannot_reach_another_project(session, scene, jira):
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id,
                 prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await requirements.list_imported(scene.project_id, principal=me(scene, key), session=session)
    assert exc.value.status_code in DENIED
    with pytest.raises(HTTPException) as exc:
        await incidents.create_incident(scene.project_id, incidents.IncidentCreate(title="x"),
                                        principal=me(scene, key), session=session)
    assert exc.value.status_code == 403


# --- generation → accept → coverage ------------------------------------------

async def _accept(session, scene, proposal_id, user=None):
    return await agent_ownership.accept_proposal(
        proposal_id, note=None, principal=AuthPrincipal(user=user or scene.user, api_key=None),
        session=session)


async def _result(session, scene, case: TestCase, status: TestStatus):
    run = TestRun(project_id=scene.project_id, test_suite_id=case.test_suite_id,
                  test_case_id=case.id, status=status)
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name=case.name, status=status,
                               duration_ms=1.0, test_case_id=case.id))
    await session.commit()


async def test_accepting_a_generated_proposal_links_and_drives_coverage(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    await _import(session, scene, cfg)
    req = (await session.exec(select(Requirement))).one()
    listed = await requirements.list_imported(scene.project_id, principal=me(scene), session=session)
    assert listed["requirements"][0]["coverage"]["status"] == "not_covered"

    gen = await requirements.generate_for_requirement(
        req.id, requirements.GenerateBody(test_suite_id=scene.root_id, criteria=[0]),
        principal=me(scene), session=session)
    pid = gen["generated"][0]["proposal_id"]
    proposal = await session.get(CaseProposal, pid)
    assert proposal.payload["requirement_id"] == req.id
    assert proposal.ai_confidence == 0.0 and "needs-authoring" in proposal.payload["tags"]

    # Second generate of the same criterion: pending, so skipped.
    again = await requirements.generate_for_requirement(
        req.id, requirements.GenerateBody(test_suite_id=scene.root_id, criteria=[0]),
        principal=me(scene), session=session)
    assert again["skipped"] and not again["generated"]

    await _accept(session, scene, pid)
    link = (await session.exec(select(RequirementLink))).one()
    assert (link.ref, link.source, link.project_id) == ("SHOP-12", "jira", scene.project_id)
    case = await session.get(TestCase, link.test_case_id)

    detail = await requirements.get_requirement(req.id, principal=me(scene), session=session)
    assert detail["coverage"]["status"] == "untested"
    assert detail["acceptance_criteria"][0]["proposal_status"] == "accepted"

    await _result(session, scene, case, TestStatus.PASSED)
    detail = await requirements.get_requirement(req.id, principal=me(scene), session=session)
    assert detail["coverage"]["status"] == "covered"
    await _result(session, scene, case, TestStatus.FAILED)
    listed = await requirements.list_imported(scene.project_id, principal=me(scene), session=session)
    assert listed["requirements"][0]["coverage"]["status"] == "failing"
    assert listed["totals"]["failing"] == 1


async def test_generate_refuses_a_suite_from_another_project(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    await _import(session, scene, cfg)
    req = (await session.exec(select(Requirement))).one()
    foreign_suite = (await session.get(TestCase, scene.foreign_case)).test_suite_id
    with pytest.raises(HTTPException) as exc:
        await requirements.generate_for_requirement(
            req.id, requirements.GenerateBody(test_suite_id=foreign_suite),
            principal=me(scene), session=session)
    assert exc.value.status_code == 404


async def test_forged_origin_in_a_proposal_does_not_link_across_projects(session, scene, jira):
    """POST /case-proposals takes any payload: a requirement id from another
    project must not get a link (or an incident a regression case) on accept."""
    foreign_req = Requirement(project_id=scene.other_project_id, source="jira",
                              external_key="HR-1", title="secret")
    foreign_inc = Incident(project_id=scene.other_project_id, title="other", fingerprint="f")
    session.add_all([foreign_req, foreign_inc])
    await session.commit()
    p = CaseProposal(project_id=scene.project_id, test_suite_id=scene.root_id, action="create",
                     payload={"name": "x", "steps": [], "requirement_id": foreign_req.id,
                              "incident_id": foreign_inc.id})
    session.add(p)
    await session.commit()
    await _accept(session, scene, p.id)
    assert (await session.exec(select(RequirementLink))).all() == []
    await session.refresh(foreign_inc)
    assert foreign_inc.regression_case_id is None


# --- incidents ----------------------------------------------------------------

def _request(body: bytes, headers: dict) -> Request:
    scope = {"type": "http", "method": "POST", "path": "/", "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


async def _deliver(session, project_id, payload, secret, source=None, header="X-TraceIQ-Signature"):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    value = f"sha256={sig}" if header == "X-TraceIQ-Signature" else f"v1={sig}"
    bg = BackgroundTasks()
    out = await incidents.receive_incident_webhook(
        project_id, _request(body, {header: value, "content-type": "application/json"}), bg,
        source=source, session=session)
    await bg()      # what Starlette does after the response
    return out


async def _hook(session, scene) -> str:
    out = await incidents.rotate_webhook_secret(scene.project_id, principal=me(scene), session=session)
    return out["secret"]


PD = {"event": {"id": "e1", "event_type": "incident.triggered", "occurred_at": "2026-10-10T10:00:05Z",
                "data": {"id": "PD1", "type": "incident", "title": "Checkout cart returning 503",
                         "status": "triggered", "urgency": "high",
                         "html_url": "https://acme.pagerduty.com/incidents/PD1",
                         "service": {"summary": "checkout"}, "created_at": "2026-10-10T10:00:00Z"}}}


async def test_webhook_secret_is_shown_once_and_auth_is_enforced(session, scene):
    with pytest.raises(HTTPException) as exc:            # nothing configured yet
        await _deliver(session, scene.project_id, {"title": "x"}, "whatever")
    assert exc.value.status_code == 404
    secret = await _hook(session, scene)
    cfg = await incidents.get_webhook_config(scene.project_id, principal=me(scene), session=session)
    assert "secret" not in cfg and cfg["configured"]
    stored = (await session.exec(select(IncidentWebhook))).one()
    assert secret not in stored.secret_encrypted
    with pytest.raises(HTTPException) as exc:
        await _deliver(session, scene.project_id, {"title": "x"}, "wrong-secret")
    assert exc.value.status_code == 401
    # Rotation invalidates the old secret.
    new = await _hook(session, scene)
    with pytest.raises(HTTPException):
        await _deliver(session, scene.project_id, {"title": "x"}, secret)
    out = await _deliver(session, scene.project_id, {"title": "x happened"}, new)
    assert out["created"]


async def test_webhook_secret_needs_project_admin_and_a_human(session, scene):
    editor = await _user(session, "ed@corp.example.com", scene.project_id, "editor")
    with pytest.raises(HTTPException) as exc:
        await incidents.rotate_webhook_secret(scene.project_id,
                                              principal=AuthPrincipal(user=editor), session=session)
    assert exc.value.status_code == 403
    key = ApiKey(name="ci", workspace_id=1, project_id=None, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await incidents.rotate_webhook_secret(scene.project_id, principal=me(scene, key), session=session)
    assert exc.value.status_code == 403


async def test_pagerduty_lifecycle_updates_one_incident_and_proposes_once(session, scene):
    secret = await _hook(session, scene)
    first = await _deliver(session, scene.project_id, PD, secret, header="X-PagerDuty-Signature")
    assert first["created"]
    resolved = json.loads(json.dumps(PD))
    resolved["event"]["event_type"] = "incident.resolved"
    resolved["event"]["data"]["status"] = "resolved"
    second = await _deliver(session, scene.project_id, resolved, secret, header="X-PagerDuty-Signature")
    assert not second["created"] and second["incident_id"] == first["incident_id"]
    inc = (await session.exec(select(Incident))).one()
    await session.refresh(inc)
    assert (inc.status, inc.source, inc.severity, inc.service) == ("resolved", "pagerduty", "high", "checkout")
    assert inc.resolved_at is not None
    # The background proposal ran exactly once, to the first suite, as a skeleton.
    proposals = (await session.exec(select(CaseProposal))).all()
    assert len(proposals) == 1 and proposals[0].payload["incident_id"] == inc.id
    assert inc.regression_proposal_id == proposals[0].id
    assert inc.regression_status == "needs_authoring"
    assert proposals[0].created_by_id is None       # no human filed it
    ping = await _deliver(session, scene.project_id,
                          {"event": {"event_type": "pagey.ping", "data": {}}}, secret)
    assert ping["status"] == "ignored"


async def test_recurrence_links_to_the_previous_incident(session, scene):
    secret = await _hook(session, scene)
    a = await _deliver(session, scene.project_id,
                       {"title": "Checkout API 503 for 12% of requests", "service": "checkout",
                        "started_at": "2026-10-01T10:00:00Z"}, secret)
    b = await _deliver(session, scene.project_id,
                       {"title": "checkout api 502 for 40% of requests", "service": "Checkout",
                        "started_at": "2026-10-08T10:00:00Z"}, secret)
    c = await _deliver(session, scene.project_id,
                       {"title": "Checkout API 503 for 1% of requests", "service": "checkout"}, secret)
    assert b["previous_incident_id"] == a["incident_id"] and b["recurrence_count"] == 1
    assert c["previous_incident_id"] == b["incident_id"] and c["recurrence_count"] == 2
    detail = await incidents.get_incident(c["incident_id"], principal=me(scene), session=session)
    assert {r["id"] for r in detail["recurrences"]} == {a["incident_id"], b["incident_id"]}
    other = await _deliver(session, scene.project_id, {"title": "Search is down", "service": "search"}, secret)
    assert other["recurrence_count"] == 0


async def test_manual_incident_matches_case_proposes_and_accept_links(session, scene):
    case = await session.get(TestCase, scene.root_cases[0])    # "cart"
    case.steps = [{"id": "s1", "type": "goto", "value": "https://shop.example.com/cart"}]
    session.add(case)
    await session.commit()
    out = await incidents.create_incident(
        scene.project_id,
        incidents.IncidentCreate(title="Cart empties on refresh", severity="P2",
                                 affected_url="https://shop.example.com/cart/", service="web",
                                 git_commit="abc123"),
        principal=me(scene), session=session)
    assert out["matched_case_id"] == case.id and out["severity"] == "high"
    assert out["regression"]["status"] == "needs_authoring"
    proposal = await session.get(CaseProposal, out["regression"]["proposal_id"])
    assert proposal.test_suite_id == case.test_suite_id        # matched case's suite
    assert proposal.payload["steps"][0] == {**proposal.payload["steps"][0], "type": "goto",
                                            "value": "https://shop.example.com/cart/"}
    with pytest.raises(HTTPException) as exc:                 # still pending
        await incidents.repropose(out["id"], incidents.ProposeBody(), principal=me(scene), session=session)
    assert exc.value.status_code == 409

    await _accept(session, scene, proposal.id)
    inc = await session.get(Incident, out["id"])
    await session.refresh(inc)
    assert inc.regression_status == "accepted" and inc.regression_case_id is not None


async def test_escaped_defects_report_and_case_scoping(session, scene):
    i1 = await incidents.create_incident(scene.project_id, incidents.IncidentCreate(
        title="Coupon applied twice", propose_regression=False), principal=me(scene), session=session)
    i2 = await incidents.create_incident(scene.project_id, incidents.IncidentCreate(
        title="Invoice total wrong", propose_regression=True), principal=me(scene), session=session)
    with pytest.raises(HTTPException) as exc:                       # foreign case
        await incidents.mark_escaped(i1["id"], incidents.EscapedBody(case_id=scene.foreign_case),
                                     principal=me(scene), session=session)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:                       # must say which
        await incidents.mark_escaped(i1["id"], incidents.EscapedBody(), principal=me(scene), session=session)
    assert exc.value.status_code == 400
    await incidents.mark_escaped(i1["id"], incidents.EscapedBody(case_id=scene.root_cases[1], note="assertion too weak"),
                                 principal=me(scene), session=session)
    await incidents.mark_escaped(i2["id"], incidents.EscapedBody(no_test_existed=True),
                                 principal=me(scene), session=session)
    await _accept(session, scene, i2["regression"]["proposal_id"])

    report = await incidents.escaped_report(scene.project_id, days=30, period="month",
                                            principal=me(scene), session=session)
    t = report["totals"]
    assert (t["escaped"], t["missed_by_existing_test"], t["no_test_existed"], t["test_added_after"]) == (2, 1, 1, 1)
    assert t["pct_test_added_after"] == 50.0
    escaped_only = await incidents.list_incidents(scene.project_id, escaped=True, limit=200,
                                                  principal=me(scene), session=session)
    assert {r["id"] for r in escaped_only} == {i1["id"], i2["id"]}
    assert any(r["escaped_case_name"] == "address" for r in escaped_only)
    marks = (await session.exec(select(AuditLog.action).where(AuditLog.entity_type == "incident",
                                                             AuditLog.action == "mark_escaped"))).all()
    assert len(marks) == 2


async def test_deleting_a_case_or_project_handles_the_new_tables(session, scene):
    secret = await _hook(session, scene)
    await incidents.update_webhook_config(
        scene.project_id, incidents.WebhookConfigBody(default_suite_id=scene.sibling_id),
        principal=me(scene), session=session)
    inc = Incident(project_id=scene.project_id, title="t", fingerprint="f",
                   matched_case_id=scene.root_cases[0], escaped_case_id=scene.root_cases[0],
                   escaped_defect=True)
    req = Requirement(project_id=scene.project_id, source="jira", external_key="SHOP-9", title="r")
    session.add_all([inc, req])
    await session.commit()

    await cascade_delete(session, "testcase", [scene.root_cases[0]])
    await session.commit()
    await session.refresh(inc)
    assert inc.matched_case_id is None and inc.escaped_case_id is None
    await cascade_delete(session, "testsuite", [scene.sibling_id])
    await session.commit()
    hook = (await session.exec(select(IncidentWebhook))).one()
    await session.refresh(hook)
    assert hook.default_suite_id is None and secret

    await cascade_delete(session, "project", [scene.project_id])
    await session.commit()
    assert (await session.exec(select(Incident))).all() == []
    assert (await session.exec(select(Requirement))).all() == []
    assert (await session.exec(select(IncidentWebhook))).all() == []


async def test_tracker_delete_keeps_requirements(session, scene, jira):
    cfg = await _tracker(session, await _ws(session, scene))
    await _import(session, scene, cfg)
    from app.api import issue_trackers
    await issue_trackers.delete_tracker(cfg.workspace_id, cfg.id, session=session, principal=me(scene))
    req = (await session.exec(select(Requirement))).one()
    await session.refresh(req)
    assert req.tracker_config_id is None
    with pytest.raises(HTTPException) as exc:
        await requirements.sync_requirement(req.id, principal=me(scene), session=session)
    assert exc.value.status_code == 502 and "re-import" in exc.value.detail


async def test_project_editor_outside_the_workspace_cannot_use_its_trackers(session, scene, jira):
    # A direct project grant can outlive workspace membership; the workspace's
    # tracker credentials (arbitrary JQL) must not be usable through it.
    cfg = await _tracker(session, await _ws(session, scene))
    editor = await _user(session, "ex-member@corp.example.com", scene.project_id, "editor")
    p = AuthPrincipal(user=editor, api_key=None)
    with pytest.raises(HTTPException) as exc:
        await requirements.search_tracker(
            scene.project_id, requirements.SearchBody(config_id=cfg.id, jql="project = HR"),
            principal=p, session=session)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await requirements.import_requirements(
            scene.project_id, requirements.ImportBody(config_id=cfg.id, keys=["SHOP-12"]),
            principal=p, session=session)
    assert exc.value.status_code == 403
