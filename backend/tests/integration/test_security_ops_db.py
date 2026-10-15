"""Security operations (WP7) against a real Postgres: access reviews, the
findings workflow (carry-forward, re-open, retest verification), leak findings
and the SIEM outbox.

    TRACEIQ_SCRATCH_DB=traceiq_wp7 ./run-tests-live.sh tests/integration/test_security_ops_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import access_reviews, security                       # noqa: E402
from app.core.auth import AuthPrincipal                             # noqa: E402
from app.models import (                                            # noqa: E402
    AccessReviewItem, ApiKey, AuditLog, Project, SecurityEventOutbox, SecurityFinding,
    SecurityScan, Team, TeamProjectAccess, TestCaseResult, TestRun, User, UserProjectAccess,
    UserTeam, UserWorkspace, Workspace,
)
from app.services import finding_lifecycle as lifecycle            # noqa: E402
from app.services.rbac_service import rbac_service                 # noqa: E402

from tests.integration.test_targeted_runs_db import scene, session  # noqa: E402,F401


def me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _person(session, email, **kw) -> User:
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", **kw)
    session.add(u)
    await session.flush()
    return u


async def _workspace_id(session, scene) -> int:
    return (await session.get(Project, scene.project_id)).workspace_id


async def _actions(session, entity_type, entity_id=None):
    q = select(AuditLog.action).where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == entity_id)
    return list((await session.exec(q.order_by(AuditLog.id))).all())


# =============================================================================
# Access reviews
# =============================================================================

@pytest_asyncio.fixture(loop_scope="function")
async def people(session, scene):
    """editor: direct Project Editor on Shop; viewer: via team QA (viewer);
    a key created by the admin, scoped to Shop."""
    ws_id = await _workspace_id(session, scene)
    member = await rbac_service.get_role_by_name(session, "Workspace Member")
    editor_role = await rbac_service.get_role_by_name(session, "Project Editor")
    editor = await _person(session, "ed@corp.example.com", mfa_enabled=True)
    viewer = await _person(session, "vi@corp.example.com", scim_external_id="okta-1")
    team = Team(name="QA", workspace_id=ws_id)
    session.add(team)
    await session.flush()
    session.add_all([
        UserWorkspace(user_id=editor.id, workspace_id=ws_id, role="member", role_id=member.id),
        UserWorkspace(user_id=viewer.id, workspace_id=ws_id, role="member", role_id=member.id),
        UserProjectAccess(user_id=editor.id, project_id=scene.project_id,
                          access_level="editor", role_id=editor_role.id),
        UserTeam(user_id=viewer.id, team_id=team.id),
        TeamProjectAccess(team_id=team.id, project_id=scene.project_id, access_level="viewer"),
        ApiKey(workspace_id=ws_id, project_id=scene.project_id, name="ci", prefix="tiq_abcd",
               hashed_key="h", created_by_id=scene.user.id),
        AuditLog(entity_type="auth", entity_id=editor.id, action="login", user_id=editor.id,
                 changes={"email": editor.email, "method": "sso_saml"}),
    ])
    await session.commit()
    return {"ws": ws_id, "editor": editor, "viewer": viewer, "team": team}


async def test_workspace_access_report(session, scene, people):
    report = await access_reviews.workspace_access_report(
        people["ws"], format="json", session=session, principal=me(scene))
    by_email = {u["email"]: u for u in report["users"]}
    assert set(by_email) == {scene.user.email, "ed@corp.example.com", "vi@corp.example.com"}

    ed = by_email["ed@corp.example.com"]
    assert ed["mfa_enabled"] is True
    assert ed["auth_method"] == "sso (saml)"
    assert ed["workspaces"][0]["role"] == "Workspace Member"
    assert ed["workspaces"][0]["projects"] == [
        {"project_id": scene.project_id, "project": "Shop", "role": "Project Editor", "via": "direct"}]

    vi = by_email["vi@corp.example.com"]
    assert vi["auth_method"] == "never signed in (scim-provisioned)"
    assert vi["workspaces"][0]["teams"] == ["QA"]
    assert vi["workspaces"][0]["projects"][0]["via"] == "team:QA"

    [key] = report["api_keys"]
    assert key["prefix"] == "tiq_abcd" and key["scope"] == "project:Shop"
    assert key["created_by"] == scene.user.email and key["last_used_at"] is None

    csv_resp = await access_reviews.workspace_access_report(
        people["ws"], format="csv", session=session, principal=me(scene))
    body = csv_resp.body.decode()
    assert body.splitlines()[0].startswith("subject_type,subject_id,label")
    assert "Shop=Project Editor (direct)" in body
    html_resp = await access_reviews.workspace_access_report(
        people["ws"], format="html", session=session, principal=me(scene))
    assert b"<table>" in html_resp.body
    assert "default-src 'none'" in html_resp.headers["content-security-policy"]


async def test_only_a_workspace_admin_may_see_the_report(session, scene, people):
    with pytest.raises(HTTPException) as exc:
        await access_reviews.workspace_access_report(
            people["ws"], format="json", session=session,
            principal=AuthPrincipal(user=people["editor"], api_key=None))
    assert exc.value.status_code == 403


async def test_review_campaign_end_to_end(session, scene, people):
    principal = me(scene)
    camp = await access_reviews.start_workspace_review(
        people["ws"], access_reviews.CampaignCreate(name="Q4 review"), None,
        session=session, principal=principal)
    assert camp["counts"]["total"] == 4           # 3 people + 1 key
    items = {i["label"]: i for i in camp["items"]}

    # Sign-off refuses while anything is undecided; revoke needs a reason.
    with pytest.raises(HTTPException) as exc:
        await access_reviews.sign_off(camp["id"], access_reviews.SignOff(), None,
                                      session=session, principal=principal)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        await access_reviews.decide_item(
            camp["id"], items["vi@corp.example.com"]["id"],
            access_reviews.Decision(decision="revoke"), None, session=session, principal=principal)

    for label, item in items.items():
        decision = "revoke" if label in ("vi@corp.example.com", "ci (tiq_abcd…)") else "keep"
        await access_reviews.decide_item(
            camp["id"], item["id"],
            access_reviews.Decision(decision=decision, note="left the team" if decision == "revoke" else None),
            None, session=session, principal=principal)

    # Revocations cannot run before sign-off.
    with pytest.raises(HTTPException) as exc:
        await access_reviews.apply_revocations(camp["id"], access_reviews.ApplyRequest(confirm=True),
                                               None, session=session, principal=principal)
    assert exc.value.status_code == 409

    signed = await access_reviews.sign_off(camp["id"], access_reviews.SignOff(note="all good"), None,
                                           session=session, principal=principal)
    assert signed["status"] == "signed_off" and signed["signed_off_by"] == scene.user.email
    with pytest.raises(HTTPException):   # decisions are final after sign-off
        await access_reviews.decide_item(
            camp["id"], items["ed@corp.example.com"]["id"],
            access_reviews.Decision(decision="keep"), None, session=session, principal=principal)

    preview = await access_reviews.apply_revocations(
        camp["id"], access_reviews.ApplyRequest(confirm=False), None,
        session=session, principal=principal)
    assert {p["label"] for p in preview["would_revoke"]} == {"vi@corp.example.com", "ci (tiq_abcd…)"}
    assert (await session.exec(select(UserWorkspace).where(
        UserWorkspace.user_id == people["viewer"].id))).first() is not None   # nothing yet

    done = await access_reviews.apply_revocations(
        camp["id"], access_reviews.ApplyRequest(confirm=True), None,
        session=session, principal=principal)
    assert {r["result"] for r in done["results"]} == {
        "removed from workspace, its teams and projects", "revoked"}
    assert (await session.exec(select(UserWorkspace).where(
        UserWorkspace.user_id == people["viewer"].id))).first() is None
    assert (await session.exec(select(UserTeam).where(
        UserTeam.user_id == people["viewer"].id))).first() is None
    key = (await session.exec(select(ApiKey).where(ApiKey.prefix == "tiq_abcd"))).first()
    assert key.revoked_at is not None
    # The editor was kept.
    assert (await session.exec(select(UserProjectAccess).where(
        UserProjectAccess.user_id == people["editor"].id))).first() is not None

    actions = await _actions(session, "access_review", camp["id"])
    assert actions[0] == "start" and actions.count("decision") == 4 and "sign_off" in actions
    sign = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "access_review", AuditLog.action == "sign_off"))).first()
    assert sign.user_id == scene.user.id and sign.changes["reviewer"] == scene.user.email
    assert "access_review_revoke" in await _actions(session, "api_key")

    export = await access_reviews.export_review(camp["id"], format="csv", session=session,
                                                principal=principal)
    assert "left the team" in export.body.decode()


async def test_api_keys_cannot_carry_out_a_review(session, scene, people):
    key = (await session.exec(select(ApiKey))).first()
    with pytest.raises(HTTPException) as exc:
        await access_reviews.start_workspace_review(
            people["ws"], access_reviews.CampaignCreate(), None, session=session,
            principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code == 403


async def test_a_reviewer_cannot_revoke_themselves(session, scene, people):
    principal = me(scene)
    camp = await access_reviews.start_workspace_review(
        people["ws"], access_reviews.CampaignCreate(), None, session=session, principal=principal)
    for item in camp["items"]:
        await access_reviews.decide_item(
            camp["id"], item["id"], access_reviews.Decision(decision="revoke", note="test"),
            None, session=session, principal=principal)
    await access_reviews.sign_off(camp["id"], access_reviews.SignOff(), None,
                                  session=session, principal=principal)
    done = await access_reviews.apply_revocations(
        camp["id"], access_reviews.ApplyRequest(confirm=True), None, session=session,
        principal=principal)
    mine = [r for r in done["results"] if r["label"] == scene.user.email][0]
    assert mine["result"].startswith("skipped")
    assert (await session.exec(select(UserWorkspace).where(
        UserWorkspace.user_id == scene.user.id))).first() is not None


async def test_instance_review_is_instance_admin_only_and_deactivates(session, scene, people):
    scene.user.is_instance_admin = True
    session.add(scene.user)
    await session.commit()
    principal = me(scene)
    camp = await access_reviews.start_instance_review(
        access_reviews.CampaignCreate(), None, session=session, principal=principal)
    assert camp["scope"] == "instance"
    with pytest.raises(HTTPException):
        await access_reviews.get_review(camp["id"], session=session,
                                        principal=AuthPrincipal(user=people["editor"], api_key=None))
    item = [i for i in camp["items"] if i["label"] == "ed@corp.example.com"][0]
    for i in camp["items"]:
        await access_reviews.decide_item(
            camp["id"], i["id"],
            access_reviews.Decision(decision="revoke" if i["id"] == item["id"] else "keep",
                                    note="contractor ended"),
            None, session=session, principal=principal)
    await access_reviews.sign_off(camp["id"], access_reviews.SignOff(), None,
                                  session=session, principal=principal)
    await access_reviews.apply_revocations(camp["id"], access_reviews.ApplyRequest(confirm=True),
                                           None, session=session, principal=principal)
    await session.refresh(people["editor"])
    assert people["editor"].is_active is False


# =============================================================================
# Findings workflow
# =============================================================================

@pytest_asyncio.fixture(loop_scope="function")
async def zap(session, scene, monkeypatch):
    project = await session.get(Project, scene.project_id)
    project.security_settings = {"enabled": True, "allowed_domains": ["app.example.com"]}
    session.add(project)
    await session.commit()
    queued = []
    from app.tasks import zap_tasks
    monkeypatch.setattr(zap_tasks.run_zap_scan, "delay", lambda scan_id: queued.append(scan_id))
    return queued


async def _scan_with(session, scene, fingerprints, scan_type="baseline"):
    scan = SecurityScan(project_id=scene.project_id, target_url="https://app.example.com/",
                        scan_type=scan_type, status="completed")
    session.add(scan)
    await session.flush()
    rows = [SecurityFinding(scan_id=scan.id, project_id=scene.project_id, scan_type="zap",
                            category="dast", severity="high", title=f"Issue {fp}",
                            target_url="https://app.example.com/x", fingerprint=fp)
            for fp in fingerprints]
    for r in rows:
        session.add(r)

    def ingest(s):
        lifecycle.ingest(s, rows, project_id=scene.project_id, scan_type="zap")
        from app.tasks.zap_tasks import _same_target
        lifecycle.verify_fixes(s, project_id=scene.project_id, scan_type="zap",
                               produced=fingerprints,
                               in_scope=lambda f: _same_target(s, f, scan),
                               evidence={"verified_by_scan_id": scan.id})
    await session.run_sync(ingest)
    await session.commit()
    return scan, rows


async def _current(session, scene, fp):
    return (await session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == scene.project_id, SecurityFinding.fingerprint == fp,
        SecurityFinding.superseded_by_id == None))).one()  # noqa: E711


async def test_new_finding_gets_an_sla_due_date(session, scene, zap):
    _, [row] = await _scan_with(session, scene, ["fp1"])
    assert row.status == "open"
    assert row.due_at == row.created_at + timedelta(days=30)        # high → 30d


async def test_fix_retest_verify_and_reappearance_reopens(session, scene, zap):
    _, [first] = await _scan_with(session, scene, ["fp1", ][:1])
    out = await security.update_security_finding(
        first.id, security.FindingUpdate(status="fixed_pending_verification", owner_id=scene.user.id),
        None, session=session, principal=me(scene))
    assert out["status"] == "fixed_pending_verification"
    assert out["retest"]["queued"] is True and zap == [out["retest"]["scan_id"]]
    retest = await session.get(SecurityScan, out["retest"]["scan_id"])
    assert retest.scan_type == "baseline" and retest.target_url == "https://app.example.com/"

    # A later scan of the same target that does not produce fp1 verifies the fix.
    await _scan_with(session, scene, ["other"])
    await session.refresh(first)
    assert first.status == "verified_fixed" and first.verified_at is not None
    assert "verified_fixed" in await _actions(session, "security_finding", first.id)

    # It comes back: the new occurrence re-opens, keeps owner and original due date.
    _, [again] = await _scan_with(session, scene, ["fp1"])
    assert again.status == "open"
    assert again.assignee_id == scene.user.id and again.due_at == first.due_at
    await session.refresh(first)
    assert first.superseded_by_id == again.id
    assert "reopen" in await _actions(session, "security_finding", again.id)


async def test_a_scan_of_another_kind_does_not_verify(session, scene, zap):
    _, [first] = await _scan_with(session, scene, ["fp1"], scan_type="active")
    await security.update_security_finding(
        first.id, security.FindingUpdate(status="fixed_pending_verification"), None,
        session=session, principal=me(scene))
    await _scan_with(session, scene, [], scan_type="baseline")
    await session.refresh(first)
    assert first.status == "fixed_pending_verification"


async def test_false_positive_carries_forward(session, scene, zap):
    _, [first] = await _scan_with(session, scene, ["fp1"])
    await security.update_security_finding(
        first.id, security.FindingUpdate(status="false_positive"), None,
        session=session, principal=me(scene))
    _, [second] = await _scan_with(session, scene, ["fp1"])
    assert second.status == "false_positive"


async def test_accepted_risk_needs_an_admin_and_expires_back_to_open(session, scene, zap, people):
    _, [first] = await _scan_with(session, scene, ["fp1"])
    body = security.FindingUpdate(status="accepted_risk", risk_justification="WAF rule 9001",
                                  risk_expires_at=datetime.utcnow() + timedelta(days=30))
    with pytest.raises(HTTPException) as exc:     # an editor cannot approve a risk
        await security.update_security_finding(
            first.id, body, None, session=session,
            principal=AuthPrincipal(user=people["editor"], api_key=None))
    assert exc.value.status_code == 403
    out = await security.update_security_finding(first.id, body, None, session=session,
                                                 principal=me(scene))
    assert out["risk_approved_by_id"] == scene.user.id

    # Let it lapse; the beat task re-opens it.
    first = await session.get(SecurityFinding, first.id)
    await session.exec(text("UPDATE securityfinding SET risk_expires_at = now() - interval '1 day' "
                            f"WHERE id = {first.id}"))
    await session.commit()
    n = await session.run_sync(lambda s: lifecycle.reopen_expired_risks(s))
    await session.commit()
    await session.refresh(first)
    assert n == 1 and first.status == "open"


async def test_viewers_cannot_triage_and_verified_fixed_is_not_manual(session, scene, zap, people):
    _, [first] = await _scan_with(session, scene, ["fp1"])
    with pytest.raises(HTTPException) as exc:
        await security.update_security_finding(
            first.id, security.FindingUpdate(status="in_progress"), None, session=session,
            principal=AuthPrincipal(user=people["viewer"], api_key=None))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await security.update_security_finding(
            first.id, security.FindingUpdate(status="verified_fixed"), None, session=session,
            principal=me(scene))
    assert exc.value.status_code == 422


async def test_findings_register_overdue_filter_and_sarif(session, scene, zap):
    _, [a, b] = await _scan_with(session, scene, ["fp1", "fp2"])
    a.due_at = datetime.utcnow() - timedelta(days=1)
    session.add(a)
    await session.commit()
    listing = await security.list_project_findings(
        scene.project_id, status=None, severity=None, scan_type=None, overdue=True,
        owner_id=None, include_superseded=False, limit=500, session=session, principal=me(scene))
    assert [f["id"] for f in listing["findings"]] == [a.id]
    assert listing["findings"][0]["overdue"] is True

    resp = await security.project_findings_sarif(scene.project_id, status=None, session=session,
                                                 principal=me(scene))
    import json
    doc = json.loads(resp.body)
    assert resp.media_type == "application/sarif+json"
    assert {r["partialFingerprints"]["traceiqFingerprint/v1"] for r in doc["runs"][0]["results"]} \
        == {"fp1", "fp2"}


async def test_workspace_sla_applies_to_new_findings(session, scene, zap):
    ws_id = await _workspace_id(session, scene)
    out = await security.set_finding_sla(ws_id, None, body={"sla_days": {"high": 5}},
                                         session=session, principal=me(scene))
    assert out["sla_days"]["high"] == 5
    _, [row] = await _scan_with(session, scene, ["fresh"])
    assert row.due_at == row.created_at + timedelta(days=5)


# =============================================================================
# Leak findings
# =============================================================================

async def test_leak_pass_files_masked_findings_and_rescan_keeps_the_owner(session, scene):
    from app.tasks.security_tasks import persist_leaks_sync
    aws = "AKIA" + "IOSFODNN7EXAMPLE"
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status="passed")
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name="cart", status="passed",
                               duration_ms=1, test_case_id=scene.root_cases[0],
                               request_url="https://app.example.com/api/cfg",
                               response_body='{"aws": "' + aws + '"}'))
    await session.commit()
    await session.run_sync(lambda s: persist_leaks_sync(s, run))
    await session.commit()
    [f] = (await session.exec(select(SecurityFinding).where(SecurityFinding.run_id == run.id))).all()
    assert f.scan_type == "leak" and f.category == "sensitive_data_exposure"
    assert aws not in (f.evidence or "") and f.location["case_id"] == scene.root_cases[0]

    f.assignee_id = scene.user.id
    session.add(f)
    await session.commit()
    await session.run_sync(lambda s: persist_leaks_sync(s, run))     # re-scan the same run
    await session.commit()
    [again] = (await session.exec(select(SecurityFinding).where(SecurityFinding.run_id == run.id))).all()
    assert again.assignee_id == scene.user.id


# =============================================================================
# SIEM outbox
# =============================================================================

async def test_audit_rows_reach_the_outbox_only_when_committed(session, scene, monkeypatch):
    from app.services import audit, instance_settings
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: {"SIEM_ENABLED": True}.get(k, False))
    await session.exec(text("TRUNCATE securityeventoutbox"))
    await session.commit()

    uid = scene.user.id
    await audit.record(session, entity_type="auth", entity_id=1, action="login", user_id=uid)
    await session.flush()
    await session.rollback()
    assert (await session.exec(select(SecurityEventOutbox))).all() == []

    row = await audit.record(session, entity_type="auth", entity_id=1, action="login", user_id=uid)
    await session.commit()
    [out] = (await session.exec(select(SecurityEventOutbox))).all()
    assert out.audit_row_hash == row.row_hash and out.needs_delivery
