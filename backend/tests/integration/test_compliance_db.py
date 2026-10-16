"""Compliance (WP13a) against a real Postgres: who may see and change controls,
four-eyes exceptions, the SOC 2 starter set, evidence-pack contents (only the
requested workspace — never another tenant's audit rows) and restore drills.

    TRACEIQ_SCRATCH_DB=traceiq_wp13a ./run-tests-live.sh tests/integration/test_compliance_db.py -q
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import compliance as api                               # noqa: E402
from app.core.auth import AuthPrincipal                             # noqa: E402
from app.core.secrets import purpose_keys                           # noqa: E402
from app.models import (                                            # noqa: E402
    ApiKey, AuditLog, ComplianceControl, DrillResult, EvidencePack, Project, SecurityFinding,
    Tenant, User, UserSystemRole, UserWorkspace, Workspace,
)
from app.services import audit                                      # noqa: E402
from app.services import compliance as svc                          # noqa: E402
from app.services.evidence_pack import KEY_PURPOSE, verify_pack     # noqa: E402
from app.services.rbac_service import rbac_service                  # noqa: E402
from app.tasks.compliance_tasks import run_build                    # noqa: E402

from tests.integration.test_targeted_runs_db import scene, session  # noqa: E402,F401


def P(user, key=None):
    return AuthPrincipal(user=user, api_key=key)


async def _person(session, email, **kw) -> User:
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", **kw)
    session.add(u)
    await session.flush()
    return u


@pytest_asyncio.fixture(loop_scope="function")
async def world(session, scene):
    """Workspace A (scene): admin = scene.user, admin2, member. Workspace B in
    another tenant with its own admin and its own audit rows + findings."""
    ws_a = (await session.get(Project, scene.project_id)).workspace_id
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    ws_member = await rbac_service.get_role_by_name(session, "Workspace Member")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    admin2 = await _person(session, "second-admin@corp.example.com", mfa_enabled=True)
    member = await _person(session, "member@corp.example.com")
    other = await _person(session, "boss@rival.example.com")
    rival = Tenant(name="Rival", owner_id=other.id)
    session.add(rival)
    await session.flush()
    ws_b = Workspace(name="Rival ws", tenant_id=rival.id)
    session.add(ws_b)
    await session.flush()
    session.add_all([
        UserWorkspace(user_id=admin2.id, workspace_id=ws_a, role="admin", role_id=ws_admin.id),
        UserWorkspace(user_id=member.id, workspace_id=ws_a, role="member", role_id=ws_member.id),
        UserWorkspace(user_id=other.id, workspace_id=ws_b.id, role="admin", role_id=ws_admin.id),
        UserSystemRole(user_id=other.id, role_id=tenant_admin.id, tenant_id=rival.id),
    ])
    rival_project = Project(name="RivalSecret", workspace_id=ws_b.id)
    session.add(rival_project)
    await session.flush()
    now = datetime.utcnow()
    session.add_all([
        SecurityFinding(project_id=scene.project_id, scan_type="passive", category="headers",
                        severity="critical", title="Missing CSP", status="open",
                        due_at=now - timedelta(days=1), fingerprint="fa"),
        SecurityFinding(project_id=rival_project.id, scan_type="passive", category="headers",
                        severity="critical", title="RIVAL-FINDING", status="open",
                        due_at=now - timedelta(days=1), fingerprint="fb"),
    ])
    await audit.record(session, entity_type="project", entity_id=scene.project_id, action="update",
                       user_id=scene.user.id, workspace_id=ws_a, changes={"marker": "ours"})
    await audit.record(session, entity_type="project", entity_id=rival_project.id, action="update",
                       user_id=other.id, workspace_id=ws_b.id, changes={"marker": "RIVAL-AUDIT"})
    await session.commit()
    return {"a": ws_a, "b": ws_b.id, "admin": scene.user, "admin2": admin2, "member": member,
            "other": other, "rival_project": rival_project.id}


async def _create(session, world, **kw):
    body = api.ControlIn(framework="SOC 2", reference="CC6.1", title="MFA for admins",
                         evidence_sources=[{"type": "mfa_enforced_for_admins"}], **kw)
    return await api.create_control(world["a"], body, None, session=session, principal=P(world["admin"]))


# --- access control ------------------------------------------------------------

async def test_members_view_admins_edit_outsiders_see_nothing(session, world):
    control = await _create(session, world)
    listing = await api.list_controls(world["a"], session=session, principal=P(world["member"]))
    assert [c["id"] for c in listing["controls"]] == [control["id"]] and listing["can_edit"] is False
    assert (await api.list_controls(world["a"], session=session, principal=P(world["admin"])))["can_edit"]

    with pytest.raises(HTTPException) as exc:
        await _create(session, {**world, "admin": world["member"]})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await api.update_control(control["id"], api.ControlPatch(title="x"), None,
                                 session=session, principal=P(world["member"]))
    assert exc.value.status_code == 403
    # The rival tenant's admin (a Tenant Admin of THEIR tenant) gets nothing.
    for call in (api.list_controls(world["a"], session=session, principal=P(world["other"])),
                 api.get_control(control["id"], session=session, principal=P(world["other"]))):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403


async def test_owner_must_be_a_member_and_owner_may_add_evidence(session, world):
    with pytest.raises(HTTPException) as exc:
        await _create(session, world, owner_id=world["other"].id)
    assert exc.value.status_code == 422
    control = await _create(session, world, owner_id=world["member"].id)
    ev = await api.add_evidence(control["id"], None, note="Screenshot of MFA_REQUIRED", outcome="pass",
                                file=None, session=session, principal=P(world["member"]))
    assert ev["outcome"] == "pass"
    with pytest.raises(HTTPException) as exc:
        await api.add_evidence(control["id"], None, note="x", outcome="pass", file=None,
                               session=session, principal=P(world["admin2"], ApiKey(
                                   workspace_id=world["a"], name="k", prefix="tiq_x", hashed_key="h",
                                   created_by_id=world["admin2"].id)))
    assert exc.value.status_code == 403            # API keys never write
    detail = await api.get_control(control["id"], session=session, principal=P(world["member"]))
    assert [h["action"] for h in detail["history"]] == ["evidence_add", "create"]
    assert detail["can_contribute"] and not detail["can_edit"]


async def test_project_scoped_or_foreign_api_keys_cannot_read(session, world):
    await _create(session, world)
    scoped = ApiKey(workspace_id=world["a"], project_id=1, name="ci", prefix="tiq_p",
                    hashed_key="h", created_by_id=world["admin"].id)
    foreign = ApiKey(workspace_id=world["b"], name="ci", prefix="tiq_f",
                     hashed_key="h", created_by_id=world["admin"].id)
    for key in (scoped, foreign):
        with pytest.raises(HTTPException) as exc:
            await api.list_controls(world["a"], session=session, principal=P(world["admin"], key))
        assert exc.value.status_code == 403


# --- SOC 2 starter set -------------------------------------------------------------

async def test_soc2_starter_set_is_idempotent_and_per_workspace(session, world):
    first = await api.load_soc2_starter(world["a"], None, session=session, principal=P(world["admin"]))
    assert first["created"] == len(svc.SOC2_STARTER)
    # An edited starter control is not re-created or overwritten.
    some = (await session.exec(select(ComplianceControl).where(
        ComplianceControl.workspace_id == world["a"]))).first()
    await api.update_control(some.id, api.ControlPatch(title="Our wording"), None,
                             session=session, principal=P(world["admin"]))
    again = await api.load_soc2_starter(world["a"], None, session=session, principal=P(world["admin"]))
    assert again == {"created": 0, "already_present": len(svc.SOC2_STARTER), "total": len(svc.SOC2_STARTER)}
    rows = (await session.exec(select(ComplianceControl).where(
        ComplianceControl.workspace_id == world["a"]))).all()
    assert len(rows) == len(svc.SOC2_STARTER)
    assert any(r.title == "Our wording" for r in rows)
    # Nothing was seeded anywhere else.
    assert not (await session.exec(select(ComplianceControl).where(
        ComplianceControl.workspace_id == world["b"]))).all()
    with pytest.raises(HTTPException):
        await api.load_soc2_starter(world["a"], None, session=session, principal=P(world["member"]))


# --- exceptions --------------------------------------------------------------------

async def test_exceptions_are_four_eyes_and_make_the_control_excepted(session, world):
    control = await _create(session, world)
    # scene.user has no MFA and MFA_REQUIRED is off → failing.
    assert (await api.get_control(control["id"], session=session,
                                  principal=P(world["admin"])))["status"] == "failing"
    with pytest.raises(HTTPException) as exc:
        await api.request_exception(control["id"], api.ExceptionIn(
            reason="x", expires_at=datetime.utcnow() + timedelta(days=400)), None,
            session=session, principal=P(world["admin"]))
    assert exc.value.status_code == 422
    req = await api.request_exception(control["id"], api.ExceptionIn(
        reason="Hardware keys arrive next month", compensating_control="IP allowlist",
        expires_at=datetime.utcnow() + timedelta(days=30)), None,
        session=session, principal=P(world["admin"]))
    with pytest.raises(HTTPException) as exc:
        await api.approve_exception(req["id"], None, session=session, principal=P(world["admin"]))
    assert exc.value.status_code == 409 and "four-eyes" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await api.approve_exception(req["id"], None, session=session, principal=P(world["member"]))
    assert exc.value.status_code == 403
    ok = await api.approve_exception(req["id"], None, session=session, principal=P(world["admin2"]))
    assert ok["status"] == "approved" and ok["approved_by"] == world["admin2"].email
    detail = await api.get_control(control["id"], session=session, principal=P(world["member"]))
    assert detail["status"] == "excepted"
    assert {"exception_request", "exception_approve"} <= {h["action"] for h in detail["history"]}

    await api.revoke_exception(req["id"], None, session=session, principal=P(world["admin2"]))
    assert (await api.get_control(control["id"], session=session,
                                  principal=P(world["admin"])))["status"] == "failing"


# --- evidence pack -------------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.objects = {}

    def upload_fileobj(self, fileobj, key, content_type=None):
        self.objects[key] = fileobj.read()


async def test_evidence_pack_covers_only_the_requested_workspace(session, world, monkeypatch):
    await api.load_soc2_starter(world["a"], None, session=session, principal=P(world["admin"]))
    session.add(DrillResult(ran_at=datetime.utcnow() - timedelta(hours=1), status="pass",
                            dump_name="d.sql.gz", checks=[{"status": "PASS", "check": "row counts",
                                                           "detail": "users=4 RIVAL-COUNT"}]))
    await session.commit()
    dispatched = []
    monkeypatch.setattr(api, "dispatch_pack", dispatched.append)

    with pytest.raises(HTTPException) as exc:
        await api.create_evidence_pack(world["a"], api.PackIn(
            period_start=datetime.utcnow() - timedelta(days=1), period_end=datetime.utcnow()),
            None, session=session, principal=P(world["member"]))
    assert exc.value.status_code == 403
    view = await api.create_evidence_pack(world["a"], api.PackIn(
        period_start=datetime.utcnow() - timedelta(days=1), period_end=datetime.utcnow()),
        None, session=session, principal=P(world["admin"]))
    assert view["status"] == "queued" and dispatched == [view["id"]]
    with pytest.raises(HTTPException) as exc:     # one build at a time per workspace
        await api.create_evidence_pack(world["a"], api.PackIn(
            period_start=datetime.utcnow() - timedelta(days=1), period_end=datetime.utcnow()),
            None, session=session, principal=P(world["admin"]))
    assert exc.value.status_code == 409

    store = FakeStore()
    await run_build(session, view["id"], store=store)
    pack = await session.get(EvidencePack, view["id"])
    await session.refresh(pack)
    assert pack.status == "ready", pack.error
    [blob] = store.objects.values()
    assert list(store.objects)[0].startswith(f"compliance/ws{world['a']}/evidence-packs/")

    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    root = names[0].split("/")[0] + "/"
    everything = b"".join(zf.read(n) for n in names)
    assert b"RIVAL-AUDIT" not in everything and b"RIVAL-FINDING" not in everything
    assert b"RivalSecret" not in everything and b"RIVAL-COUNT" not in everything
    rows = list(csv.DictReader(io.StringIO(zf.read(root + "audit/audit-log.csv").decode())))
    assert rows and {r["workspace_id"] for r in rows} == {str(world["a"])}
    assert any("ours" in r["changes"] for r in rows)
    sarifs = [n for n in names if n.endswith(".sarif")]
    assert sarifs and all(f"/project-{world['rival_project']}-" not in n for n in sarifs)
    for expected in ("controls/controls.json", "controls/summary.html", "data-policies/projects.json",
                     "security/finding-sla.json", "restore-drills/history.json",
                     "audit/chain-verification.json", "access-reviews/current-access-report.csv",
                     "access-reviews/current-access-report.html", "manifest.json"):
        assert root + expected in names, expected

    report = verify_pack(io.BytesIO(blob), purpose_keys(KEY_PURPOSE))
    assert report["verified"], report
    assert report["workspace_id"] == world["a"]

    actions = (await session.exec(select(AuditLog.action).where(
        AuditLog.entity_type == "evidence_pack", AuditLog.entity_id == pack.id))).all()
    assert set(actions) == {"generate", "ready"}

    # Only workspace admins of A can see the pack.
    with pytest.raises(HTTPException):
        await api.get_evidence_pack(pack.id, session=session, principal=P(world["other"]))
    with pytest.raises(HTTPException):
        await api.list_evidence_packs(world["a"], session=session, principal=P(world["member"]))


async def test_verify_endpoint_hides_reports_from_other_workspaces(session, world, monkeypatch):
    from starlette.datastructures import UploadFile
    monkeypatch.setattr(api, "dispatch_pack", lambda _id: None)
    view = await api.create_evidence_pack(world["a"], api.PackIn(
        period_start=datetime.utcnow() - timedelta(days=1), period_end=datetime.utcnow()),
        None, session=session, principal=P(world["admin"]))
    store = FakeStore()
    await run_build(session, view["id"], store=store)
    [blob] = store.objects.values()

    report = await api.verify_evidence_pack(None, file=UploadFile(io.BytesIO(blob), filename="p.zip"),
                                            manifest=None, session=session, principal=P(world["member"]))
    assert report["verified"]
    with pytest.raises(HTTPException) as exc:
        await api.verify_evidence_pack(None, file=UploadFile(io.BytesIO(blob), filename="p.zip"),
                                       manifest=None, session=session, principal=P(world["other"]))
    assert exc.value.status_code == 403


# --- restore drills -----------------------------------------------------------------

DRILL = {"status": "pass", "ran_at": (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z",
         "dump_name": "traceiq.sql.gz",
         "checks": [{"status": "PASS", "check": "restore", "detail": "12s"}]}


async def test_drill_reports_need_an_instance_admin_and_no_secrets(session, world):
    with pytest.raises(HTTPException) as exc:
        await api.report_restore_drill(None, dict(DRILL), session=session, principal=P(world["other"]))
    assert exc.value.status_code == 403
    ops = await _person(session, "ops@corp.example.com", is_instance_admin=True)
    await session.commit()
    scoped = ApiKey(workspace_id=world["a"], project_id=1, name="cron", prefix="tiq_s",
                    hashed_key="h", created_by_id=ops.id)
    with pytest.raises(HTTPException) as exc:
        await api.report_restore_drill(None, dict(DRILL), session=session, principal=P(ops, scoped))
    assert exc.value.status_code == 403

    leaky = {**DRILL, "checks": [{"status": "FAIL", "check": "restore",
                                  "detail": "postgresql://traceiq:hunter2@db/traceiq refused"}],
             "status": "fail"}
    with pytest.raises(HTTPException) as exc:
        await api.report_restore_drill(None, leaky, session=session, principal=P(ops))
    assert exc.value.status_code == 422 and "hunter2" not in str(exc.value.detail)
    assert not (await session.exec(select(DrillResult))).all()

    key = ApiKey(workspace_id=world["a"], name="cron", prefix="tiq_c", hashed_key="h",
                 created_by_id=ops.id)
    stored = await api.report_restore_drill(None, dict(DRILL), session=session, principal=P(ops, key))
    assert stored["status"] == "pass" and stored["reporter"] == "api_key:tiq_c"
    seen = await api.list_restore_drills(world["a"], session=session, principal=P(world["member"]))
    assert seen[0]["checks"] == [{"check": "restore", "status": "PASS"}]
    assert "dump_name" not in seen[0]

    # And it now evidences the A1.2 control.
    control = await api.create_control(world["a"], api.ControlIn(
        framework="SOC 2", reference="A1.2", title="Restore tested",
        evidence_sources=[{"type": "restore_drill_recent", "params": {"days": 400}}]), None,
        session=session, principal=P(world["admin"]))
    assert control["status"] == "passing", control["status_reason"]
