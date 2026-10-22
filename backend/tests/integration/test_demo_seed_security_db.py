"""Demo security datasets (app/services/demo_seed/security.py) against a real
Postgres, read back through the endpoints the security pages call.

    TRACEIQ_SCRATCH_DB=traceiq_gseed_sec ./run-tests-live.sh tests/integration/test_demo_seed_security_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                                     # noqa: E402
from app.services import demo_seed                                          # noqa: E402
from app.services import demo_workspace as dw                               # noqa: E402
from tests.integration.demo_seed_fixtures import (                          # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, owner, session,
)


@pytest.fixture(autouse=True)
def only_security(monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("security",))


def _p(user):
    return AuthPrincipal(user=user, api_key=None)


async def test_security_pages_have_data(session, owner):
    from app.api import access_matrix, access_reviews, compliance, estates, security, trust_center
    from app.models import Estate, MonitoredPage, Project, SecurityScan

    out = await dw.create_demo_workspace(session, owner)
    ws_id = out["workspace_id"]
    web, api = out["projects"][0]["id"], out["projects"][1]["id"]
    p = _p(owner)
    now = datetime.utcnow()

    project = await session.get(Project, web)
    assert project.security_settings["enabled"] is True
    assert "example.com" in project.security_settings["allowed_domains"]

    # Security register: two completed ZAP scans, every workflow status present.
    scans = await security.list_security_scans(web, session=session, current_user=owner)
    assert len(scans) == 2
    rows = (await session.exec(select(SecurityScan).where(SecurityScan.project_id == web))).all()
    assert all(s.status == "completed" and s.counts and s.finished_at <= now for s in rows)
    reg = await security.list_project_findings(
        web, status=None, severity=None, scan_type="zap", overdue=False, owner_id=None,
        include_superseded=False, limit=500, session=session, principal=p)
    assert {"open", "in_progress", "verified_fixed", "accepted_risk", "false_positive"} <= set(reg["by_status"])
    zap = [f for f in reg["findings"]]
    assert all(f["due_at"] or f["severity"] == "info" for f in zap)
    # Older occurrences are superseded by the newer scan's rows (WP7 linking).
    everything = await security.list_project_findings(
        web, status=None, severity=None, scan_type="zap", overdue=False, owner_id=None,
        include_superseded=True, limit=500, session=session, principal=p)
    assert everything["total"] > reg["total"]

    # Access matrix: a finished run with pass, a granted deny and an inconclusive cell.
    matrices = await access_matrix.list_matrices(web, session=session, principal=p)
    assert len(matrices) == 1
    runs = await access_matrix.list_matrix_runs(matrices[0]["id"], limit=20, session=session, principal=p)
    assert len(runs) == 1 and runs[0]["status"] == "completed"
    run = await access_matrix.get_matrix_run(runs[0]["id"], session=session, principal=p)
    verdicts = {c["verdict"] for c in run["cells"]}
    assert {"pass", "fail", "inconclusive"} <= verdicts
    assert run["summary"]["access_granted"] == 1
    mf = await access_matrix.matrix_findings(matrices[0]["id"], session=session, principal=p)
    assert len(mf) == 1 and mf[0]["severity"] == "high"

    # Access reviews: one signed off, one open.
    reviews = await access_reviews.list_workspace_reviews(ws_id, session=session, principal=p)
    assert sorted(r["status"] for r in reviews) == ["open", "signed_off"]
    signed = next(r for r in reviews if r["status"] == "signed_off")
    detail = await access_reviews.get_review(signed["id"], session=session, principal=p)
    assert detail["items"] and all(i["decision"] for i in detail["items"])

    # Estate: two domains scored by two scans; www's HSTS finding verified fixed.
    est = (await session.exec(select(Estate).where(Estate.project_id == web))).one()
    assert est.schedule_enabled is False
    view = await estates.get_estate(est.id, session=session, principal=p)
    assert len(view["domains"]) == 2 and all(d["score"] is not None for d in view["domains"])
    assert all(d["allowlisted"] for d in view["domains"])
    assert view["latest_scan"]["status"] == "completed"
    estate_findings = await security.list_project_findings(
        web, status=None, severity=None, scan_type="estate-tls", overdue=False, owner_id=None,
        include_superseded=False, limit=500, session=session, principal=p)
    assert "verified_fixed" in estate_findings["by_status"]

    # Monitored page: approved scripts, one changed (approval revoked), history.
    page = (await session.exec(select(MonitoredPage).where(MonitoredPage.project_id == web))).one()
    assert page.schedule_enabled is False
    scripts = await estates.list_scripts(page.id, include_removed=False, session=session, principal=p)
    assert sum(1 for s in scripts if s["authorized"]) == 2
    assert sum(1 for s in scripts if not s["authorized"]) == 2
    changes = await estates.list_changes(page.id, limit=100, session=session, principal=p)
    kinds = {c["kind"] for c in (changes if isinstance(changes, list) else changes["changes"])}
    assert {"baseline", "script_changed"} <= kinds

    # SOC 2 controls with evidence and an exception.
    controls = await compliance.list_controls(ws_id, framework=None, status=None, owner_id=None,
                                              session=session, principal=p)
    assert len(controls["controls"]) >= 15
    assert len(set(controls["counts"]) - {"not_assessed"}) >= 1

    # Trust center: disabled, with the status page and a draft incident post.
    tc = await trust_center.get_trust_center(ws_id, session=session, principal=p)
    assert tc["enabled"] is False
    posts = await trust_center.list_incident_posts(ws_id, session=session, principal=p)
    assert len(posts) == 1 and posts[0]["published"] is False
    from app.models import StatusPage
    assert (await session.exec(select(StatusPage).where(StatusPage.project_id == api))).one()


async def test_security_demo_delete_leaves_every_table_as_it_was(session, owner, monkeypatch):
    from app.core import storage
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    during = await count_all_tables(session)
    for table in ("securityscan", "accessmatrixcell", "accessreviewitem", "estatedomainresult",
                  "pagescript", "pagechangeevent", "compliancecontrol", "controlevidence",
                  "controlexception", "trustcenter", "trustincidentpost", "statuspage"):
        assert during[table] > before[table], table
    await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"


async def test_nothing_security_is_dated_in_the_future(session, owner):
    from app.models import (AccessMatrixRun, AccessReviewItem, EstateDomainResult, PageChangeEvent,
                            SecurityFinding)
    out = await dw.create_demo_workspace(session, owner)
    now = datetime.utcnow() + timedelta(seconds=1)
    web = out["projects"][0]["id"]
    for f in (await session.exec(select(SecurityFinding).where(SecurityFinding.project_id == web))).all():
        assert f.created_at <= now and (f.resolved_at is None or f.resolved_at <= now)
    for model in (AccessMatrixRun, EstateDomainResult, PageChangeEvent):
        for row in (await session.exec(select(model))).all():
            assert row.created_at <= now, model
    for item in (await session.exec(select(AccessReviewItem))).all():
        assert item.decided_at is None or item.decided_at <= now
