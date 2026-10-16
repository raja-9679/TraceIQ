"""WP13b trust center and PoC report against a real Postgres: the public page
is off by default and shows only what was published, the gated-document link
lifecycle, cross-workspace refusals, and the PoC report's project set.

    ./run-tests-live.sh tests/integration/test_trust_center_db.py -q
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select
from starlette.datastructures import UploadFile

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import poc_reports as poc_api  # noqa: E402
from app.api import share_links as share_api  # noqa: E402
from app.api import trust_center as tc_api  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, Project, SecurityFinding, ShareLink, StatusPage, TestStatus, TrustCenter, TrustDocument,
    Workspace,
)
from app.services import share_links as sl  # noqa: E402

from tests.integration.test_reporting_db import _me, _req, _run, _user, _ws  # noqa: E402
from tests.integration.test_targeted_runs_db import (  # noqa: E402,F401
    no_queue, scene, session,
)

FINDING = "SQL injection in /admin/login?user="
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"


@pytest.fixture
def store(monkeypatch):
    """The object store, faked: uploads are kept in a dict."""
    from app.core import storage
    objects = {}
    monkeypatch.setattr(storage.object_store, "upload_fileobj",
                        lambda f, key, content_type=None: objects.__setitem__(key, f.read()))
    monkeypatch.setattr(storage.object_store, "delete_object", lambda key: objects.pop(key, None))
    monkeypatch.setattr(storage.object_store, "get_presigned_download_url",
                        lambda key, fn, ct, exp=60: f"https://objects.example/{key}?X-Amz-Expires={exp}")
    return objects


@pytest.fixture
def mail(monkeypatch):
    sent = []
    from app.tasks import notification_tasks
    monkeypatch.setattr(notification_tasks.send_account_email, "delay",
                        lambda to, subject, html, text=None: sent.append((to, subject, text)))
    return sent


async def _put(session, scene, who=None, **body):
    ws = await _ws(session, scene)
    return await tc_api.update_trust_center(ws, _req(), tc_api.TrustCenterUpdate(**body),
                                            session=session, principal=_me(who or scene.user))


async def _public(session, slug):
    return await tc_api.public_trust_page.__wrapped__(_req(), slug, session=session)


async def _download(session, slug, token):
    return await tc_api.public_document_download.__wrapped__(_req(), slug, {"token": token}, session=session)


async def _upload(session, ws_id, who, data=PDF, name="soc2.pdf", title="SOC 2 report"):
    return await tc_api.upload_document(ws_id, _req(), UploadFile(file=io.BytesIO(data), filename=name),
                                        title=title, description=None, listed=True,
                                        session=session, principal=_me(who))


async def _other_workspace(session, scene):
    ws = await session.get(Workspace, await _ws(session, scene))
    other = Workspace(name="Elsewhere", tenant_id=ws.tenant_id)
    session.add(other)
    await session.flush()
    admin = await _user(session, "admin@other.example.com")
    from app.models import UserWorkspace
    from app.services.rbac_service import rbac_service
    role = await rbac_service.get_role_by_name(session, "Workspace Admin")
    session.add(UserWorkspace(user_id=admin.id, workspace_id=other.id, role="admin", role_id=role.id))
    await session.commit()
    return other, admin


# --- off by default / what is published ---------------------------------------------

async def test_off_means_404(session, scene):
    with pytest.raises(HTTPException) as exc:
        await _public(session, "no-such-slug")
    assert exc.value.status_code == 404
    cfg = await _put(session, scene, title="Acme trust")          # saved, still off
    assert cfg["enabled"] is False and cfg["slug"]
    with pytest.raises(HTTPException) as exc:
        await _public(session, cfg["slug"])
    assert exc.value.status_code == 404


async def test_only_published_posts_and_opted_in_posture(session, scene):
    ws = await _ws(session, scene)
    session.add(SecurityFinding(project_id=scene.project_id, category="dast", severity="high",
                                title=FINDING, status="open", target_url="https://shop.internal/admin"))
    page = StatusPage(project_id=scene.project_id, slug="sp-1", title="Shop status")
    session.add(page)
    await session.commit()
    cfg = await _put(session, scene, enabled=True, posture_items=["critical_findings", "restore_drill"],
                     status_page_ids=[page.id], contact_email="security@acme.example")
    me = _me(scene.user)
    await tc_api.create_incident_post(ws, _req(), tc_api.IncidentPostCreate(
        title="Draft only", body="not yet"), session=session, principal=me)
    live = await tc_api.create_incident_post(ws, _req(), tc_api.IncidentPostCreate(
        title="API latency", components=["API"], body="Looking <script>x</script>", published=True),
        session=session, principal=me)
    await tc_api.add_incident_update(live["id"], _req(), tc_api.IncidentUpdateCreate(
        status="resolved", body="**Fixed**"), session=session, principal=me)

    out = await _public(session, cfg["slug"])
    assert [p["title"] for p in out["incidents"]] == ["API latency"]
    post = out["incidents"][0]
    assert post["status"] == "resolved" and post["resolved_at"]
    assert post["updates"][0]["body_html"] == "<p><strong>Fixed</strong></p>"
    assert "&lt;script&gt;" in post["updates"][1]["body_html"]
    # Opted in: findings band. restore_drill has no data, so it is hidden.
    assert out["posture"] == [{"key": "critical_findings", "label": "Open critical or high security findings",
                               "value": "1-4", "state": "attention"}]
    assert out["status"] == {"overall": "unknown", "monitors": []}
    dumped = json.dumps(out)
    assert FINDING not in dumped and "shop.internal" not in dumped and "dev@corp" not in dumped

    # A status page disabled later drops out; another workspace's page cannot be picked.
    other, _admin = await _other_workspace(session, scene)
    foreign_project = Project(name="Foreign", workspace_id=other.id)
    session.add(foreign_project)
    await session.flush()
    foreign_page = StatusPage(project_id=foreign_project.id, slug="sp-2")
    session.add(foreign_page)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _put(session, scene, status_page_ids=[foreign_page.id])
    assert exc.value.status_code == 404
    center = (await session.exec(select(TrustCenter).where(TrustCenter.workspace_id == ws))).first()
    assert center.view_count == 1


async def test_only_workspace_admins_configure(session, scene):
    ws = await _ws(session, scene)
    member = await _user(session, "member@corp.example.com", ws, scene.project_id, level="admin")
    with pytest.raises(HTTPException) as exc:
        await _put(session, scene, who=member, enabled=True)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await tc_api.get_trust_center(ws, session=session, principal=_me(member))
    assert exc.value.status_code == 403


# --- gated documents ---------------------------------------------------------------------

async def test_document_link_lifecycle(session, scene, store):
    ws = await _ws(session, scene)
    cfg = await _put(session, scene, enabled=True)
    doc = await _upload(session, ws, scene.user)
    assert doc["content_type"] == "application/pdf" and list(store)[0].startswith(f"trust/{ws}/")
    out = await _public(session, cfg["slug"])
    assert out["documents"] == [{"id": doc["id"], "title": "SOC 2 report", "description": None}]

    made = await tc_api.create_document_link(doc["id"], _req(), tc_api.DocumentLinkCreate(
        recipient="Auditor at Bigco"), session=session, principal=_me(scene.user))
    assert made["path"] == f"/trust/{cfg['slug']}#{made['token']}"
    row = await session.get(ShareLink, made["id"])
    assert row.kind == "trust_document" and row.token_hash == sl.hash_token(made["token"])
    assert made["token"] not in json.dumps(sl.describe(row), default=str)

    got = await _download(session, cfg["slug"], made["token"])
    assert got["url"].endswith("X-Amz-Expires=60") and got["sha256"] == doc["sha256"]
    audits = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "share_link", AuditLog.action == "download"))).all()
    assert len(audits) == 1 and made["token"] not in json.dumps(audits[0].changes)
    # Not resolvable through the generic share view, nor on another slug.
    with pytest.raises(HTTPException):
        await share_api.public_share.__wrapped__(_req(), {"token": made["token"]}, session=session)
    # Not in the member-visible workspace link list.
    listed = await share_api.list_workspace_share_links(ws, session=session, principal=_me(scene.user))
    assert all(l["kind"] != "trust_document" for l in listed)

    await share_api.revoke_share_link(made["id"], _req(), session=session, principal=_me(scene.user))
    with pytest.raises(HTTPException) as exc:
        await _download(session, cfg["slug"], made["token"])
    assert exc.value.status_code == 404

    # Expired.
    old = await tc_api.create_document_link(doc["id"], _req(), tc_api.DocumentLinkCreate(
        recipient="Late", expires_in_days=1), session=session, principal=_me(scene.user))
    row = await session.get(ShareLink, old["id"])
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    session.add(row)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _download(session, cfg["slug"], old["token"])
    assert exc.value.status_code == 404
    # Expiry limits are the share-link ones.
    with pytest.raises(HTTPException) as exc:
        await tc_api.create_document_link(doc["id"], _req(), tc_api.DocumentLinkCreate(
            recipient="x", expires_in_days=91), session=session, principal=_me(scene.user))
    assert exc.value.status_code == 422

    # Trust center switched off: even a live link stops.
    live = await tc_api.create_document_link(doc["id"], _req(), tc_api.DocumentLinkCreate(
        recipient="y"), session=session, principal=_me(scene.user))
    await _put(session, scene, enabled=False)
    with pytest.raises(HTTPException) as exc:
        await _download(session, cfg["slug"], live["token"])
    assert exc.value.status_code == 404
    await _put(session, scene, enabled=True)
    assert (await _download(session, cfg["slug"], live["token"]))["title"] == "SOC 2 report"
    # Deleting the document revokes its links and removes the object.
    await tc_api.delete_document(doc["id"], _req(), session=session, principal=_me(scene.user))
    assert store == {}
    with pytest.raises(HTTPException) as exc:
        await _download(session, cfg["slug"], live["token"])
    assert exc.value.status_code == 404


async def test_uploads_are_sniffed(session, scene, store):
    ws = await _ws(session, scene)
    await _put(session, scene)
    for data, name in ((b"<html><script>alert(1)</script>", "x.pdf"), (b"<svg onload=1>", "x.svg"),
                       (PDF, "x.html")):
        with pytest.raises(HTTPException) as exc:
            await _upload(session, ws, scene.user, data=data, name=name)
        assert exc.value.status_code == 415
    assert store == {}


async def test_another_workspaces_document_is_404(session, scene, store):
    ws = await _ws(session, scene)
    mine = await _put(session, scene, enabled=True)
    other, other_admin = await _other_workspace(session, scene)
    await tc_api.update_trust_center(other.id, _req(), tc_api.TrustCenterUpdate(enabled=True),
                                     session=session, principal=_me(other_admin))
    theirs = await _upload(session, other.id, other_admin, title="Their pen test")
    # Issuing, listing or deleting their document by id: 404, not 403.
    for call in (
        lambda: tc_api.create_document_link(theirs["id"], _req(), tc_api.DocumentLinkCreate(recipient="me"),
                                            session=session, principal=_me(scene.user)),
        lambda: tc_api.list_document_links(theirs["id"], session=session, principal=_me(scene.user)),
        lambda: tc_api.delete_document(theirs["id"], _req(), session=session, principal=_me(scene.user)),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404
    # A forged link row in MY workspace pointing at THEIR document id.
    token = sl.new_token()
    session.add(ShareLink(workspace_id=ws, kind="trust_document", target_id=theirs["id"],
                          token_hash=sl.hash_token(token), created_by_id=scene.user.id,
                          expires_at=datetime.utcnow() + timedelta(days=1)))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _download(session, mine["slug"], token)
    assert exc.value.status_code == 404
    # Their genuine link used on my slug.
    real = await tc_api.create_document_link(theirs["id"], _req(), tc_api.DocumentLinkCreate(recipient="r"),
                                             session=session, principal=_me(other_admin))
    with pytest.raises(HTTPException) as exc:
        await _download(session, mine["slug"], real["token"])
    assert exc.value.status_code == 404
    # Their documents never appear on my page.
    assert (await _public(session, mine["slug"]))["documents"] == []


async def test_request_access_emails_only_this_workspaces_admins(session, scene, mail):
    ws = await _ws(session, scene)
    await _user(session, "viewer@corp.example.com", ws, scene.project_id)
    await _other_workspace(session, scene)
    cfg = await _put(session, scene, enabled=True)
    body = tc_api.AccessRequest(name="Pat\r\nBcc: x@evil.example", email="pat@bigco.example", message="<b>hi</b>")
    with pytest.raises(HTTPException) as exc:        # off unless the admin allows it
        await tc_api.public_request_access.__wrapped__(_req(), cfg["slug"], body, session=session)
    assert exc.value.status_code == 404
    await _put(session, scene, allow_access_requests=True)
    await tc_api.public_request_access.__wrapped__(_req(), cfg["slug"], body, session=session)
    assert [to for to, _s, _t in mail] == ["dev@corp.example.com"]
    assert "\n" not in mail[0][1] and "Bcc" in mail[0][1]          # header injection flattened
    audit = (await session.exec(select(AuditLog).where(AuditLog.action == "access_request"))).one()
    assert "pat@bigco" not in json.dumps(audit.changes)


# --- PoC report -----------------------------------------------------------------------

async def test_poc_report_covers_only_accessible_projects(session, scene):
    ws = await _ws(session, scene)
    P, F = TestStatus.PASSED, TestStatus.FAILED
    now = datetime.utcnow()
    await _run(session, scene, P, now - timedelta(days=1), [("cart", P), ("pay", P)])
    await _run(session, scene, F, now - timedelta(days=1), [("cart", F)])
    await _run(session, scene, F, now - timedelta(days=1), [("x", F)] * 5, project_id=scene.other_project_id)
    viewer = await _user(session, "viewer@corp.example.com", ws, scene.project_id)

    async def report(who, **kw):
        return await poc_api.workspace_poc_report(ws, start=None, end=None, project_ids=kw.get("ids"),
                                                  minutes_per_execution=kw.get("m"), hourly_rate=None,
                                                  currency=None, session=session, principal=_me(who))

    r = await report(viewer)
    assert r["project_ids"] == [scene.project_id]
    assert r["activity"]["runs"] == 2 and r["activity"]["case_executions"] == 3
    assert r["value"]["failures_caught"] == 1
    assert r["estimate"]["hours_saved"] == 0.2 and "5 min" in r["estimate"]["label"]
    # Asking for a project one cannot see changes nothing.
    r2 = await report(viewer, ids=f"{scene.other_project_id}")
    assert r2["project_ids"] == [] and r2["activity"]["runs"] == 0
    assert r2["estimate"]["hours_saved"] == 0.0
    admin = await report(scene.user, m=6)
    assert sorted(admin["project_ids"]) == sorted([scene.project_id, scene.other_project_id])
    assert admin["activity"]["case_executions"] == 8 and admin["estimate"]["hours_saved"] == 0.8


async def test_poc_share_link_reuses_share_link_rules(session, scene):
    ws = await _ws(session, scene)
    P, F = TestStatus.PASSED, TestStatus.FAILED
    await _run(session, scene, F, datetime.utcnow() - timedelta(days=1), [("cart", F), ("pay", P)])
    viewer = await _user(session, "viewer@corp.example.com", ws, scene.project_id)

    async def share(who, **params):
        return await share_api.create_workspace_share_link(
            ws, _req(), share_api.ShareLinkCreate(kind="poc_report", params=params),
            session=session, principal=_me(who))

    with pytest.raises(HTTPException) as exc:       # viewers cannot share
        await share(viewer)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await share(scene.user, hourly_rate=50)     # a rate needs a currency code
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await share_api.create_workspace_share_link(
            ws, _req(), share_api.ShareLinkCreate(kind="poc_report", expires_in_days=91),
            session=session, principal=_me(scene.user))
    assert exc.value.status_code == 422

    made = await share(scene.user, project_ids=[scene.project_id], hourly_rate=60, currency="gbp")
    row = await session.get(ShareLink, made["id"])
    assert row.token_hash == sl.hash_token(made["token"]) and row.params["currency"] == "GBP"
    out = await share_api.public_share.__wrapped__(_req(), {"token": made["token"]}, session=session)
    assert out["kind"] == "poc_report" and out["inventory"]["projects"] == ["Shop"]
    assert out["activity"]["case_executions"] == 2
    assert out["estimate"]["money"]["currency"] == "GBP"
    dumped = json.dumps(out)
    assert "Timeout" not in dumped and "objects.internal" not in dumped and "@" not in dumped.replace(
        out.get("label") or "", "")
    listed = await share_api.list_workspace_share_links(ws, kind="poc_report", session=session,
                                                        principal=_me(scene.user))
    assert [l["id"] for l in listed] == [made["id"]]
