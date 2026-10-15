"""Findings workflow rules and SARIF export (WP7). The DB paths (carry-forward,
re-open, retest verification, access control) are in
tests/integration/test_security_ops_db.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import finding_lifecycle as lc
from app.services import sarif

NOW = datetime(2026, 10, 15, 12, 0, 0)


# --- transitions ------------------------------------------------------------------

@pytest.mark.parametrize("current,target", [
    ("open", "in_progress"), ("open", "fixed_pending_verification"), ("open", "false_positive"),
    ("in_progress", "fixed_pending_verification"), ("fixed_pending_verification", "open"),
    ("verified_fixed", "open"), ("accepted_risk", "open"), ("false_positive", "open"),
])
def test_allowed_transitions(current, target):
    lc.check_transition(current, target, now=NOW)


@pytest.mark.parametrize("current,target", [
    ("open", "verified_fixed"),                     # only a rescan verifies
    ("fixed_pending_verification", "verified_fixed"),
    ("verified_fixed", "in_progress"),
    ("false_positive", "accepted_risk"),
    ("open", "open"),
    ("open", "resolved_somehow"),
])
def test_refused_transitions(current, target):
    with pytest.raises(lc.TransitionError):
        lc.check_transition(current, target, now=NOW)


def test_accepting_a_risk_needs_justification_and_a_bounded_future_expiry():
    with pytest.raises(lc.TransitionError, match="justification"):
        lc.check_transition("open", "accepted_risk", expires_at=NOW + timedelta(days=30), now=NOW)
    with pytest.raises(lc.TransitionError, match="expiry"):
        lc.check_transition("open", "accepted_risk", justification="compensating WAF rule", now=NOW)
    with pytest.raises(lc.TransitionError, match="future"):
        lc.check_transition("open", "accepted_risk", justification="x",
                            expires_at=NOW - timedelta(days=1), now=NOW)
    with pytest.raises(lc.TransitionError, match="365"):
        lc.check_transition("open", "accepted_risk", justification="x",
                            expires_at=NOW + timedelta(days=400), now=NOW)
    lc.check_transition("in_progress", "accepted_risk", justification="WAF rule 9001",
                        expires_at=NOW + timedelta(days=90), now=NOW)


def test_legacy_status_names_are_understood():
    assert lc.normalize_status("acknowledged") == "in_progress"
    assert lc.normalize_status("resolved") == "fixed_pending_verification"
    lc.check_transition("acknowledged", "fixed_pending_verification", now=NOW)


# --- SLA --------------------------------------------------------------------------

def test_sla_defaults_and_overrides():
    assert lc.resolve_sla(None) == {"critical": 7, "high": 30, "medium": 90, "low": 180}
    assert lc.resolve_sla({"high": 14, "bogus": 3, "low": -1})["high"] == 14
    assert lc.resolve_sla({"low": -1})["low"] == 180


def test_due_date_from_severity_and_none_for_info():
    sla = lc.resolve_sla(None)
    assert lc.due_date(NOW, "high", sla) == NOW + timedelta(days=30)
    assert lc.due_date(NOW, "info", sla) is None


@pytest.mark.parametrize("payload", [{"urgent": 3}, {"high": 0}, {"high": "30"}, {"high": True}, []])
def test_sla_validation_rejects_nonsense(payload):
    with pytest.raises(ValueError):
        lc.validate_sla(payload)


def test_overdue_only_while_actionable():
    f = SimpleNamespace(status="open", due_at=NOW - timedelta(days=1))
    assert lc.is_overdue(f, NOW)
    f.status = "accepted_risk"
    assert not lc.is_overdue(f, NOW)
    f.status, f.due_at = "in_progress", NOW + timedelta(days=1)
    assert not lc.is_overdue(f, NOW)


# --- carry-forward ----------------------------------------------------------------

def prior(**kw):
    base = dict(status="open", assignee_id=7, due_at=NOW + timedelta(days=3),
                risk_justification=None, risk_expires_at=None, risk_approved_by_id=None,
                risk_approved_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_owner_and_original_due_date_carry_forward():
    c = lc.carry_forward(prior(status="in_progress"), NOW)
    assert c.status == "in_progress" and c.reopened is None
    assert c.fields["assignee_id"] == 7
    assert c.fields["due_at"] == NOW + timedelta(days=3)


@pytest.mark.parametrize("status", ["fixed_pending_verification", "verified_fixed"])
def test_a_fixed_finding_that_reappears_is_reopened(status):
    c = lc.carry_forward(prior(status=status), NOW)
    assert (c.status, c.reopened) == ("open", "seen_again")
    assert c.fields["assignee_id"] == 7     # the owner stays on the hook


def test_false_positive_verdict_carries_forward():
    assert lc.carry_forward(prior(status="false_positive"), NOW).status == "false_positive"


def test_unexpired_risk_acceptance_carries_forward_with_its_approval():
    p = prior(status="accepted_risk", risk_justification="WAF", risk_approved_by_id=3,
              risk_expires_at=NOW + timedelta(days=10), risk_approved_at=NOW)
    c = lc.carry_forward(p, NOW)
    assert c.status == "accepted_risk"
    assert c.fields["risk_approved_by_id"] == 3


def test_expired_risk_acceptance_reopens_and_drops_the_approval():
    p = prior(status="accepted_risk", risk_justification="WAF", risk_approved_by_id=3,
              risk_expires_at=NOW - timedelta(seconds=1))
    c = lc.carry_forward(p, NOW)
    assert (c.status, c.reopened) == ("open", "risk_acceptance_expired")
    assert c.fields["risk_approved_by_id"] is None and c.fields["risk_justification"] is None


def test_carry_from_a_snapshot_dict():
    snap = {"status": "in_progress", "assignee_id": 4, "due_at": None,
            "risk_justification": None, "risk_expires_at": None,
            "risk_approved_by_id": None, "risk_approved_at": None}
    assert lc.carry_forward(snap, NOW).fields["assignee_id"] == 4


def test_run_origins_cover_network_events():
    run = SimpleNamespace(network_events=[{"url": "https://a.example.com/x?y"},
                                          {"url": "not a url"}, "junk"])
    assert lc.run_origins(run) == {"https://a.example.com"}


# --- SARIF ------------------------------------------------------------------------

def finding(**kw):
    base = dict(id=1, scan_type="zap", category="dast", severity="high",
                title="Cross Site Scripting (Reflected)", description="Reflected XSS",
                evidence="<script>alert(1)</script>", target_url="https://app.example.com/search?q=x",
                status="open", fingerprint="abc123", assignee_id=None, due_at=NOW,
                created_at=NOW, risk_justification=None, risk_expires_at=None, location=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_sarif_document_structure():
    doc = sarif.build_sarif([
        finding(),
        finding(id=2, severity="medium", target_url="https://app.example.com/other"),
        finding(id=3, scan_type="passive", category="missing-header", severity="low",
                title="Missing Referrer-Policy", fingerprint="def"),
        finding(id=4, severity="info", title="Info", fingerprint=None),
    ], automation_id="traceiq/scan/9")
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-2.1.0.json")
    assert len(doc["runs"]) == 1
    run = doc["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "TraceIQ" and driver["informationUri"].startswith("https://")
    rule_ids = [r["id"] for r in driver["rules"]]
    assert len(rule_ids) == len(set(rule_ids)) == 3          # 1 and 2 share a rule
    assert run["automationDetails"] == {"id": "traceiq/scan/9"}
    for r in driver["rules"]:
        assert {"id", "shortDescription", "fullDescription", "defaultConfiguration"} <= set(r)
        assert r["defaultConfiguration"]["level"] in ("error", "warning", "note")
        float(r["properties"]["security-severity"])
    for res in run["results"]:
        assert driver["rules"][res["ruleIndex"]]["id"] == res["ruleId"]
        assert res["message"]["text"]
        uri = res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert uri.startswith(("https://", "urn:"))
    levels = [r["level"] for r in run["results"]]
    assert levels == ["error", "warning", "note", "note"]
    assert run["results"][0]["partialFingerprints"] == {"traceiqFingerprint/v1": "abc123"}
    assert run["results"][3]["partialFingerprints"] == {}


def test_sarif_marks_waived_findings_as_suppressed():
    doc = sarif.build_sarif([
        finding(status="false_positive"),
        finding(id=2, status="accepted_risk", risk_justification="WAF rule 9001",
                risk_expires_at=NOW + timedelta(days=30)),
        finding(id=3),
    ])
    results = doc["runs"][0]["results"]
    assert results[0]["suppressions"][0]["status"] == "accepted"
    assert "WAF rule 9001" in results[1]["suppressions"][0]["justification"]
    assert "suppressions" not in results[2]


def test_sarif_leak_finding_has_a_logical_location():
    doc = sarif.build_sarif([finding(scan_type="leak", category="sensitive_data_exposure",
                                     location={"field": "response_body.config.key"})])
    loc = doc["runs"][0]["results"][0]["locations"][0]
    assert loc["logicalLocations"] == [{"name": "response_body.config.key", "kind": "member"}]


def test_empty_sarif_is_still_valid():
    doc = sarif.build_sarif([])
    assert doc["runs"][0]["results"] == [] and doc["runs"][0]["tool"]["driver"]["rules"] == []
