"""WP12a reporting against a real Postgres: digest recipients and channels,
the snapshot numbers, share-link lifecycle and the portfolio's project set.

    ./run-tests-live.sh tests/integration/test_reporting_db.py -q
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from starlette.requests import Request

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import reports as reports_api  # noqa: E402
from app.api import share_links as share_api  # noqa: E402
from app.core.auth import AuthPrincipal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, FailureCluster, Project, ProjectNotificationPref, ReportSchedule, ShareLink,
    TestCaseResult, TestRun, TestStatus, User, UserProjectAccess, UserWorkspace, Workspace,
)
from app.services import reporting  # noqa: E402
from app.tasks import report_tasks  # noqa: E402

from tests.integration.test_targeted_runs_db import (  # noqa: E402,F401
    no_queue, scene, session,
)

SLACK = "https://hooks.slack.com/services/T000/B000/projectsecret"
ERROR = "Timeout 30000ms waiting for #pay-button"
TRACE = "https://objects.internal/traces/run-1.zip"


def _req():
    return Request({"type": "http", "method": "GET", "path": "/api/x", "query_string": b"",
                    "headers": [], "client": ("203.0.113.9", 1)})


def _me(user):
    return AuthPrincipal(user=user, api_key=None)


async def _user(session, email, workspace_id=None, project_id=None, level="viewer"):
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if workspace_id:
        session.add(UserWorkspace(user_id=u.id, workspace_id=workspace_id, role="member"))
    if project_id:
        session.add(UserProjectAccess(user_id=u.id, project_id=project_id, access_level=level))
    await session.commit()
    return u


async def _ws(session, scene):
    return (await session.get(Project, scene.project_id)).workspace_id


async def _run(session, scene, status, at, results=(), project_id=None):
    run = TestRun(test_suite_id=scene.root_id, project_id=project_id or scene.project_id,
                  user_id=scene.user.id, status=status, created_at=at, suite_name="Checkout",
                  total_tests=len(results), passed_tests=sum(1 for _n, s in results if s == TestStatus.PASSED),
                  failed_tests=sum(1 for _n, s in results if s != TestStatus.PASSED),
                  duration_ms=1000.0, trace_url=TRACE, error_message=ERROR)
    session.add(run)
    await session.flush()
    for name, st in results:
        session.add(TestCaseResult(test_run_id=run.id, test_name=name, status=st, duration_ms=400.0,
                                   error_message=ERROR if st != TestStatus.PASSED else None,
                                   trace_url=TRACE, request_url="https://shop.internal/api/cart"))
    await session.commit()
    return run


# --- snapshot numbers -------------------------------------------------------------

async def test_snapshot_trend_against_the_previous_window(session, scene):
    now = datetime.utcnow()
    P, F = TestStatus.PASSED, TestStatus.FAILED
    # This week: 3 of 4 runs passed, 2 failing results.
    for _ in range(3):
        await _run(session, scene, P, now - timedelta(days=1), [("cart", P)])
    await _run(session, scene, F, now - timedelta(days=2), [("cart", F), ("pay", F)])
    # The week before: 1 of 2 passed, 3 failing results.
    await _run(session, scene, P, now - timedelta(days=9), [("cart", P)])
    await _run(session, scene, F, now - timedelta(days=10), [("pay", F), ("pay", F), ("cart", F)])
    # Another project's runs never count.
    await _run(session, scene, F, now - timedelta(days=1), [("x", F)], project_id=scene.other_project_id)
    session.add(FailureCluster(project_id=scene.project_id, signature="s1", title=ERROR,
                               category="timeout", occurrence_count=4))
    await session.commit()

    snap = await reporting.project_snapshot(session, await session.get(Project, scene.project_id), 7, now)
    assert snap["current"]["runs"] == 4 and snap["current"]["pass_rate"] == 75.0
    assert snap["current"]["failures"] == 2
    assert snap["previous"]["pass_rate"] == 50.0 and snap["previous"]["failures"] == 3
    assert snap["trend"] == {"pass_rate": 25.0, "runs": 2, "failures": -1}
    assert snap["top_failing"] == [{"test_name": "cart", "failures": 1},
                                   {"test_name": "pay", "failures": 1}]
    assert snap["open_clusters"] == 1
    # The window's first (partial) day through today: 8 points for 7 days,
    # and the last one is today (it used to stop at yesterday).
    from datetime import datetime as _dt
    assert len(snap["daily"]) == 8 and sum(d["runs"] for d in snap["daily"]) == 4
    assert snap["daily"][-1]["date"] == _dt.utcnow().date().isoformat()
    assert snap["readiness"]["verdict"] in ("ready", "not_ready", "insufficient_data")
    assert snap["headline"].startswith("75% of 4 runs passed this week, up 25 points")


# --- digest recipients and channels ----------------------------------------------------

async def _schedule(session, scene, **kw):
    sched = ReportSchedule(project_id=scene.project_id, name="Weekly", cron_expression="0 9 * * 1",
                           created_by_id=scene.user.id, **kw)
    session.add(sched)
    await session.commit()
    return sched


def _recipients(schedule_id):
    with Session(report_tasks.sync_engine) as s:
        sch = s.get(ReportSchedule, schedule_id)
        return report_tasks.digest_recipients(s, sch, s.get(Project, sch.project_id))


async def test_digest_recipients_are_rechecked_for_access(session, scene):
    ws = await _ws(session, scene)
    member = await _user(session, "member@corp.example.com", ws, scene.project_id)
    no_grant = await _user(session, "nogrant@corp.example.com", ws)      # same workspace, no project
    other_ws = Workspace(name="Elsewhere", tenant_id=(await session.get(Workspace, ws)).tenant_id)
    session.add(other_ws)
    await session.flush()
    stranger = await _user(session, "stranger@other.example.com", other_ws.id)
    opted_out = await _user(session, "quiet@corp.example.com", ws, scene.project_id)
    session.add(ProjectNotificationPref(user_id=opted_out.id, project_id=scene.project_id, mode="off"))
    await session.commit()
    # A row written by the old API: free-text addresses, one of them garbage.
    sched = await _schedule(session, scene, channels=["email"],
                            recipient_user_ids=[member.id, no_grant.id, stranger.id, opted_out.id],
                            recipients=["list@corp.example.com", "a@b.com, c@d.com"])
    to, skipped = _recipients(sched.id)
    assert to == ["member@corp.example.com", "list@corp.example.com"]
    assert skipped == {"no_access": 2, "opted_out": 1, "invalid_address": 1}

    # Access removed after the schedule was saved: no more digest.
    grant = (await session.exec(select(UserProjectAccess).where(
        UserProjectAccess.user_id == member.id))).first()
    await session.delete(grant)
    await session.commit()
    assert _recipients(sched.id)[0] == ["list@corp.example.com"]


async def test_api_refuses_outsiders_and_bad_addresses_and_needs_editor(session, scene):
    ws = await _ws(session, scene)
    viewer = await _user(session, "viewer@corp.example.com", ws, scene.project_id)
    other_ws = Workspace(name="Elsewhere", tenant_id=(await session.get(Workspace, ws)).tenant_id)
    session.add(other_ws)
    await session.flush()
    stranger = await _user(session, "stranger@other.example.com", other_ws.id)
    from app.models import ReportScheduleCreate
    for body in (ReportScheduleCreate(name="d", cron_expression="0 9 * * 1", recipient_user_ids=[stranger.id]),
                 ReportScheduleCreate(name="d", cron_expression="0 9 * * 1", recipients=["x@y.com, z@w.com"]),
                 ReportScheduleCreate(name="d", cron_expression="0 9 * * 1", channels=["fax"])):
        with pytest.raises(HTTPException) as exc:
            await reports_api.create_report_schedule(scene.project_id, body, _req(), session=session,
                                                     principal=_me(scene.user))
        assert exc.value.status_code == 422
    ok = await reports_api.create_report_schedule(
        scene.project_id, ReportScheduleCreate(name="d", cron_expression="0 9 * * 1",
                                               recipient_user_ids=[viewer.id]),
        _req(), session=session, principal=_me(scene.user))
    assert ok.recipient_user_ids == [viewer.id] and ok.channels == ["email"] and ok.window_days == 7
    with pytest.raises(HTTPException) as exc:
        await reports_api.send_report_now_endpoint(ok.id, session=session, principal=_me(viewer))
    assert exc.value.status_code == 403


async def test_chat_goes_only_to_the_projects_own_webhook(session, scene, monkeypatch):
    from app.services import project_notifications as pn
    project = await session.get(Project, scene.project_id)
    project.notification_settings = pn.apply_patch({}, {"slack_webhook": SLACK})
    session.add(project)
    await session.commit()
    sched = await _schedule(session, scene, channels=["slack", "teams", "email"],
                            recipients=["list@corp.example.com"])
    posted, mailed = [], []
    monkeypatch.setattr(report_tasks, "_post", lambda url, payload: posted.append((url, payload)))
    monkeypatch.setattr(report_tasks, "_send_emails",
                        lambda subj, text, html, to: mailed.append(to) or len(to))
    monkeypatch.setattr(report_tasks.insvc, "effective", lambda key: True)
    # The instance-wide webhook must never be used for a project digest.
    monkeypatch.setattr(report_tasks.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/instance", raising=False)
    snap = await reporting.project_snapshot(session, project, 7)
    with Session(report_tasks.sync_engine) as s:
        result = report_tasks._deliver(s.get(ReportSchedule, sched.id), s, snapshot=snap)
    assert [u for u, _p in posted] == [SLACK]
    assert result["channels_sent"] == ["slack", "email"]
    assert "no teams webhook set for this project" in result["errors"]
    assert mailed == [["list@corp.example.com"]]
    assert SLACK not in json.dumps(result)


# --- share links --------------------------------------------------------------------

async def _share(session, scene, who=None, **body):
    return await share_api.create_project_share_link(
        scene.project_id, _req(), share_api.ShareLinkCreate(**body), session=session,
        principal=_me(who or scene.user))


async def _view(session, token):
    return await share_api.public_share.__wrapped__(_req(), {"token": token}, session=session)


async def test_share_link_lifecycle(session, scene):
    P, F = TestStatus.PASSED, TestStatus.FAILED
    run = await _run(session, scene, F, datetime.utcnow(), [("cart", P), ("pay", F)])
    made = await _share(session, scene, kind="run_report", run_id=run.id)
    token = made["token"]
    stored = await session.get(ShareLink, made["id"])
    assert stored.token_hash != token and len(stored.token_hash) == 64
    assert (stored.expires_at - stored.created_at).days == 7

    page = await _view(session, token)
    dumped = json.dumps(page)
    assert page["run"]["pass_rate"] == 50.0 and [r["test_name"] for r in page["results"]] == ["cart", "pay"]
    for secret in (ERROR, TRACE, "shop.internal", scene.user.email):
        assert secret not in dumped
    await _view(session, token)
    await session.refresh(stored)
    assert stored.view_count == 2 and stored.last_viewed_at is not None

    views = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "share_link", AuditLog.action == "view"))).all()
    assert len(views) == 2 and all(token not in json.dumps(v.changes or {}) for v in views)

    listed = await share_api.list_project_share_links(scene.project_id, session=session,
                                                      principal=_me(scene.user))
    assert listed[0]["state"] == "active" and "token" not in listed[0]

    await share_api.revoke_share_link(made["id"], _req(), session=session, principal=_me(scene.user))
    with pytest.raises(HTTPException) as exc:
        await _view(session, token)
    assert exc.value.status_code == 404


async def test_expired_and_unknown_tokens_404(session, scene):
    made = await _share(session, scene, kind="project_summary", expires_in_days=1)
    link = await session.get(ShareLink, made["id"])
    link.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.add(link)
    await session.commit()
    for token in (made["token"], "not-a-token"):
        with pytest.raises(HTTPException) as exc:
            await _view(session, token)
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await _share(session, scene, kind="project_summary", expires_in_days=91)
    assert exc.value.status_code == 422


async def test_cannot_share_another_projects_run_or_as_a_viewer(session, scene):
    foreign = await _run(session, scene, TestStatus.PASSED, datetime.utcnow(), [],
                         project_id=scene.other_project_id)
    with pytest.raises(HTTPException) as exc:
        await _share(session, scene, kind="run_report", run_id=foreign.id)
    assert exc.value.status_code == 404
    viewer = await _user(session, "viewer@corp.example.com", await _ws(session, scene), scene.project_id)
    with pytest.raises(HTTPException) as exc:
        await _share(session, scene, who=viewer, kind="project_summary")
    assert exc.value.status_code == 403


async def test_link_dies_with_its_creators_access(session, scene):
    ws = await _ws(session, scene)
    editor = await _user(session, "editor@corp.example.com", ws, scene.project_id, level="editor")
    made = await _share(session, scene, who=editor, kind="project_summary")
    page = await _view(session, made["token"])
    assert page["kind"] == "project_summary" and page["name"] == "Shop"
    grant = (await session.exec(select(UserProjectAccess).where(
        UserProjectAccess.user_id == editor.id))).first()
    await session.delete(grant)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _view(session, made["token"])
    assert exc.value.status_code == 404


async def test_workspace_summary_link_covers_only_the_creators_editor_projects(session, scene):
    ws = await _ws(session, scene)
    lead = await _user(session, "lead@corp.example.com", ws, scene.project_id, level="editor")
    session.add(UserProjectAccess(user_id=lead.id, project_id=scene.other_project_id, access_level="viewer"))
    await session.commit()
    made = await share_api.create_workspace_share_link(
        ws, _req(), share_api.ShareLinkCreate(kind="workspace_summary"), session=session,
        principal=_me(lead))
    page = await _view(session, made["token"])
    assert [p["name"] for p in page["projects"]] == ["Shop"]


# --- portfolio ------------------------------------------------------------------------

async def test_portfolio_shows_only_accessible_projects(session, scene):
    ws = await _ws(session, scene)
    member = await _user(session, "member@corp.example.com", ws, scene.project_id)
    other_ws = Workspace(name="Elsewhere", tenant_id=(await session.get(Workspace, ws)).tenant_id)
    session.add(other_ws)
    await session.flush()
    session.add(Project(name="Foreign", workspace_id=other_ws.id))
    await session.commit()
    await _run(session, scene, TestStatus.PASSED, datetime.utcnow(), [("cart", TestStatus.PASSED)])

    mine = await reports_api.workspace_portfolio(ws, session=session, principal=_me(member))
    assert [p["project_name"] for p in mine["projects"]] == ["Shop"]
    assert mine["projects"][0]["pass_rate_7d"] == 100.0 and mine["projects"][0]["last_run"]
    admin = await reports_api.workspace_portfolio(ws, session=session, principal=_me(scene.user))
    assert [p["project_name"] for p in admin["projects"]] == ["Other", "Shop"]
    with pytest.raises(HTTPException) as exc:
        await reports_api.workspace_portfolio(other_ws.id, session=session, principal=_me(member))
    assert exc.value.status_code == 403
    summary = await reports_api.workspace_executive_summary(ws, days=30, session=session,
                                                            principal=_me(member))
    assert [p["project_name"] for p in summary["projects"]] == ["Shop"]


async def test_erasure_revokes_the_persons_links(session, scene):
    from app.services.erasure import erase_user
    ws = await _ws(session, scene)
    editor = await _user(session, "editor@corp.example.com", ws, scene.project_id, level="editor")
    made = await _share(session, scene, who=editor, kind="project_summary")
    report = await erase_user(session, editor)
    await session.commit()
    assert report.share_links_revoked == 1
    with pytest.raises(HTTPException) as exc:
        await _view(session, made["token"])
    assert exc.value.status_code == 404


async def test_open_findings_counts_each_finding_once(session, scene):
    """Superseded occurrences keep their old status; counting them made the
    Portfolio show more open findings than the Security register."""
    from app.models import SecurityFinding
    newest = SecurityFinding(project_id=scene.project_id, category="missing-header",
                             severity="low", title="csp", fingerprint="fp", status="open")
    session.add(newest)
    await session.flush()
    session.add_all([
        SecurityFinding(project_id=scene.project_id, category="missing-header", severity="low",
                        title="csp", fingerprint="fp", status="open",
                        superseded_by_id=newest.id),
        SecurityFinding(project_id=scene.project_id, category="cookie", severity="low",
                        title="flags", fingerprint="fp2", status="verified_fixed"),
    ])
    await session.commit()
    assert await reporting.open_findings(session, [scene.project_id]) == {scene.project_id: 1}
