"""Estate finding lifecycle + certificate alert thresholds (WP9).

    ./run-tests.sh tests/test_estate_findings.py -q
"""
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Project, SecurityEventOutbox, SecurityFinding, Workspace
from app.services.estate_findings import apply_findings, cert_alert_due
from app.services.passive_security import finding_fingerprint

URL = "https://example.com/"
NOW = datetime(2026, 10, 15, 12, 0)


@pytest.fixture()
def db(monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: None)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[
        AuditLog.__table__, SecurityEventOutbox.__table__, SecurityFinding.__table__,
        Project.__table__, Workspace.__table__])
    with Session(engine) as s:
        yield s


def finding(title, url=URL, scan_type="estate-dns", severity="medium"):
    return {"scan_type": scan_type, "category": "email-auth", "severity": severity, "title": title,
            "description": title, "evidence": None, "target_url": url,
            "fingerprint": finding_fingerprint("email-auth", title, url)}


def rows(db):
    return {f.title: f for f in db.exec(select(SecurityFinding)).all()}


def apply(db, produced, scopes, when=NOW):
    out = apply_findings(db, 1, produced, scopes, now=when)
    db.commit()
    return out


def test_create_refresh_and_verify_when_gone(db):
    scope = {("estate-dns", URL)}
    assert apply(db, [finding("SPF record missing"), finding("DMARC record missing")], scope)["created"] == 2
    r = rows(db)
    assert r["SPF record missing"].status == "open"
    assert r["SPF record missing"].due_at == NOW + timedelta(days=90)     # medium SLA

    out = apply(db, [finding("SPF record missing", severity="high")], scope)
    assert out == {"created": 0, "updated": 1, "reopened": 0, "resolved": 1}
    r = rows(db)
    assert r["SPF record missing"].severity == "high"
    assert r["DMARC record missing"].status == "verified_fixed"
    assert r["DMARC record missing"].verified_at == NOW
    actions = db.exec(select(AuditLog.action)).all()
    assert "verified_fixed" in actions


def test_false_positive_sticks_and_fixes_that_return_reopen(db):
    scope = {("estate-dns", URL)}
    apply(db, [finding("A"), finding("B"), finding("C")], scope)
    r = rows(db)
    r["A"].status = "false_positive"
    r["B"].status = "fixed_pending_verification"
    r["C"].status = "accepted_risk"
    r["C"].risk_expires_at = NOW + timedelta(days=30)
    db.add_all(r.values())
    db.commit()
    out = apply(db, [finding("A"), finding("B"), finding("C")], scope)
    assert out["reopened"] == 1
    r = rows(db)
    assert (r["A"].status, r["B"].status, r["C"].status) == ("false_positive", "open", "accepted_risk")
    # A false positive is never auto-"verified" when it disappears either.
    apply(db, [], scope)
    assert rows(db)["A"].status == "false_positive"


def test_expired_risk_acceptance_reopens(db):
    scope = {("estate-dns", URL)}
    apply(db, [finding("A")], scope)
    a = rows(db)["A"]
    a.status, a.risk_expires_at, a.risk_justification = "accepted_risk", NOW - timedelta(days=1), "x"
    db.add(a)
    db.commit()
    apply(db, [finding("A")], scope)
    a = rows(db)["A"]
    assert a.status == "open" and a.risk_justification is None


def test_out_of_scope_findings_are_left_alone(db):
    other = "https://shop.example.com/"
    apply(db, [finding("A"), finding("B", url=other), finding("T", scan_type="estate-tls")],
          {("estate-dns", URL), ("estate-dns", other), ("estate-tls", URL)})
    # Only example.com's DNS family ran this time.
    out = apply(db, [], {("estate-dns", URL)})
    assert out["resolved"] == 1
    r = rows(db)
    assert r["A"].status == "verified_fixed"
    assert r["B"].status == "open" and r["T"].status == "open"


def test_no_scopes_is_a_no_op(db):
    assert apply(db, [], set()) == {"created": 0, "updated": 0, "reopened": 0, "resolved": 0}


@pytest.mark.parametrize("days_left,sent,expect", [
    (45, None, None),
    (30, None, 30),
    (29, 30, None),      # already alerted at 30
    (14, 30, 14),
    (10, None, 14),      # first seen inside 14: alert once at 14, not 30 too
    (6, 14, 7),
    (3, 7, None),
    (-1, 7, None),
])
def test_cert_alert_due(days_left, sent, expect):
    assert cert_alert_due(days_left, [30, 14, 7], sent) == expect


def test_cert_alert_due_ignores_bad_thresholds():
    assert cert_alert_due(5, [0, -3, 10], None) == 10
    assert cert_alert_due(5, [], None) is None
