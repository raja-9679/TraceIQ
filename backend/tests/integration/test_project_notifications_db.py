"""Project notifications against a real Postgres: a project's email only
reaches people with access to THAT project, each on their own terms, and the
API keeps webhooks secret.

    ./run-tests-live.sh tests/integration/test_project_notifications_db.py -q
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from starlette.requests import Request

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import project_notifications as api  # noqa: E402
from app.core.auth import AuthPrincipal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, Project, ProjectNotificationPref, TestRun, TestStatus, User, UserProjectAccess,
    UserWorkspace, Workspace,
)
from app.services import project_notifications as pn  # noqa: E402
from app.settings_models import UserSettings  # noqa: E402

from tests.integration.test_targeted_runs_db import (  # noqa: E402,F401
    no_queue, scene, session,
)

SLACK = "https://hooks.slack.com/services/T000/B000/livesecretvalue"


def _sync():
    from app.tasks.monitor_tasks import sync_engine
    return Session(sync_engine)


def _req():
    return Request({"type": "http", "method": "PUT", "path": "/api/x", "query_string": b"",
                    "headers": [], "client": ("203.0.113.9", 1)})


async def _user(session, email, workspace_id=None, project_id=None):
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if workspace_id:
        session.add(UserWorkspace(user_id=u.id, workspace_id=workspace_id, role="member"))
    if project_id:
        session.add(UserProjectAccess(user_id=u.id, project_id=project_id, access_level="viewer"))
    await session.commit()
    return u


async def _ws(session, scene):
    return (await session.get(Project, scene.project_id)).workspace_id


async def _run(session, scene, user_id, status):
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, user_id=user_id, status=status)
    session.add(run)
    await session.commit()
    return run


def _recipients(project_id, starter_id, failed):
    with _sync() as s:
        return sorted(pn.email_recipients(s, s.get(Project, project_id), starter_id, run_failed=failed))


async def _set(session, scene, payload, who=None):
    return await api.set_settings(scene.project_id, _req(), payload, session=session,
                                  principal=AuthPrincipal(user=who or scene.user))


# --- who gets the email --------------------------------------------------------

async def test_default_is_the_starter_only(session, scene):
    ws = await _ws(session, scene)
    await _user(session, "colleague@corp.example.com", ws, scene.project_id)
    assert _recipients(scene.project_id, scene.user.id, failed=True) == [scene.user.email]


async def test_picked_members_and_extra_addresses(session, scene):
    ws = await _ws(session, scene)
    qa = await _user(session, "qa@corp.example.com", ws, scene.project_id)
    await _set(session, scene, {"recipient_user_ids": [qa.id], "extra_emails": ["list@corp.example.com"]})
    assert _recipients(scene.project_id, scene.user.id, failed=True) == sorted(
        [scene.user.email, "qa@corp.example.com", "list@corp.example.com"])


async def test_people_without_project_access_cannot_be_picked(session, scene):
    ws = await _ws(session, scene)
    no_grant = await _user(session, "same-ws-no-grant@corp.example.com", ws)   # in the workspace, not the project
    other_ws = Workspace(name="Elsewhere", tenant_id=(await session.get(Workspace, ws)).tenant_id)
    session.add(other_ws)
    await session.flush()
    stranger = await _user(session, "stranger@other.example.com", other_ws.id)
    for uid in (no_grant.id, stranger.id):
        with pytest.raises(HTTPException) as exc:
            await _set(session, scene, {"recipient_user_ids": [uid]})
        assert exc.value.status_code == 422
    eligible = {u["email"] for u in await api.get_recipients(scene.project_id, session=session,
                                                             principal=AuthPrincipal(user=scene.user))}
    assert "stranger@other.example.com" not in eligible and "same-ws-no-grant@corp.example.com" not in eligible


async def test_losing_access_stops_the_email(session, scene):
    ws = await _ws(session, scene)
    qa = await _user(session, "leaver@corp.example.com", ws, scene.project_id)
    await _set(session, scene, {"recipient_user_ids": [qa.id]})
    assert "leaver@corp.example.com" in _recipients(scene.project_id, None, failed=True)
    grant = (await session.exec(select(UserProjectAccess).where(UserProjectAccess.user_id == qa.id))).first()
    await session.delete(grant)
    await session.commit()
    assert "leaver@corp.example.com" not in _recipients(scene.project_id, None, failed=True)


async def test_each_person_decides_per_project(session, scene):
    ws = await _ws(session, scene)
    a = await _user(session, "a@corp.example.com", ws, scene.project_id)
    b = await _user(session, "b@corp.example.com", ws, scene.project_id)
    c = await _user(session, "c@corp.example.com", ws, scene.project_id)
    await _set(session, scene, {"recipient_user_ids": [a.id, b.id, c.id]})
    session.add_all([
        ProjectNotificationPref(user_id=a.id, project_id=scene.project_id, mode="off"),
        ProjectNotificationPref(user_id=b.id, project_id=scene.project_id, mode="failures"),
        UserSettings(user_id=c.id, notification_email="c-alerts@corp.example.com"),
    ])
    await session.commit()
    passed = _recipients(scene.project_id, None, failed=False)
    failed = _recipients(scene.project_id, None, failed=True)
    assert passed == ["c-alerts@corp.example.com"]
    assert failed == ["b@corp.example.com", "c-alerts@corp.example.com"]


async def test_the_task_entry_point_uses_the_project_rules(session, scene):
    from app.tasks.notification_tasks import get_email_recipients
    run = await _run(session, scene, scene.user.id, TestStatus.FAILED)
    with _sync() as s:
        assert get_email_recipients(s.get(TestRun, run.id), {}, s) == [scene.user.email]


# --- the API ---------------------------------------------------------------------

async def test_only_project_admins_change_settings(session, scene):
    ws = await _ws(session, scene)
    viewer = await _user(session, "viewer@corp.example.com", ws, scene.project_id)
    with pytest.raises(HTTPException) as exc:
        await _set(session, scene, {"failures_only": True}, who=viewer)
    assert exc.value.status_code == 403
    # ... but they can read them, and set their own choice.
    view = await api.get_settings(scene.project_id, session=session, principal=AuthPrincipal(user=viewer))
    assert view["failures_only"] is False
    mine = await api.set_mine(scene.project_id, {"mode": "failures"}, session=session, principal=AuthPrincipal(user=viewer))
    assert mine["mode"] == "failures"
    listed = await api.list_mine(session=session, principal=AuthPrincipal(user=viewer))
    assert {"project_id": scene.project_id, "mode": "failures"}.items() <= next(
        r for r in listed if r["project_id"] == scene.project_id).items()
    await api.set_mine(scene.project_id, {"mode": "default"}, session=session, principal=AuthPrincipal(user=viewer))
    assert (await api.get_mine(scene.project_id, session=session, principal=AuthPrincipal(user=viewer)))["mode"] == "default"


async def test_webhooks_never_leave_the_server_and_are_not_audited(session, scene, monkeypatch):
    from app.core import net_guard

    async def allow(url, **kw):
        return url
    monkeypatch.setattr(net_guard, "validate_outbound_url", allow)
    view = await _set(session, scene, {"slack_enabled": True, "slack_webhook": SLACK})
    assert view["slack_webhook_set"] is True and SLACK not in str(view)
    project = await session.get(Project, scene.project_id)
    assert SLACK not in str(project.notification_settings)          # encrypted at rest
    audits = (await session.exec(select(AuditLog).where(AuditLog.action == "update_notification_settings"))).all()
    assert audits and all("livesecretvalue" not in str(a.changes) for a in audits)


async def test_internal_webhook_targets_are_refused(session, scene):
    with pytest.raises(HTTPException) as exc:
        await _set(session, scene, {"slack_webhook": "https://169.254.169.254/latest/meta-data"})
    assert exc.value.status_code == 422
