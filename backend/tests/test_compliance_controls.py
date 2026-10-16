"""Control model rules (WP13a): evidence-source judges, status/freshness math,
exception expiry and the four-eyes rule. Pure — no database.

    ./run-tests.sh tests/test_compliance_controls.py -q
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import compliance as c

NOW = datetime(2026, 10, 16, 12, 0, 0)


def days(n):
    return timedelta(days=n)


# --- evidence-source validation -------------------------------------------------

def test_sources_get_defaults_and_unknown_types_are_refused():
    out = c.validate_sources([{"type": "access_review_recent"}, {"type": "audit_chain_verifies"}])
    assert out == [{"type": "access_review_recent", "params": {"days": 90}},
                   {"type": "audit_chain_verifies", "params": {}}]
    with pytest.raises(c.ComplianceError):
        c.validate_sources([{"type": "shell_command", "params": {"cmd": "id"}}])


@pytest.mark.parametrize("bad", [
    {"type": "access_review_recent", "params": {"days": 0}},
    {"type": "access_review_recent", "params": {"days": True}},
    {"type": "access_review_recent", "params": {"window": 3}},
    {"type": "no_findings_past_sla", "params": {"severities": ["urgent"]}},
    {"type": "no_findings_past_sla", "params": {"severities": []}},
])
def test_bad_params_are_refused(bad):
    with pytest.raises(c.ComplianceError):
        c.validate_sources([bad])


def test_duplicate_sources_collapse():
    out = c.validate_sources([{"type": "sso_enforced"}, {"type": "sso_enforced", "params": {}}])
    assert len(out) == 1


def test_every_starter_control_has_valid_sources_and_unique_keys():
    keys = [i["key"] for i in c.SOC2_STARTER]
    assert len(keys) == len(set(keys))
    for item in c.SOC2_STARTER:
        c.validate_sources(item["sources"])
    used = {s["type"] for i in c.SOC2_STARTER for s in i["sources"]}
    # Every automated source is used by at least one starter control.
    assert used == set(c.SOURCES)


# --- judges ---------------------------------------------------------------------

def test_access_review_within_window_passes_and_records_when():
    status, reason, at = c.judge_access_review(NOW - days(30), 90, NOW)
    assert status == "pass" and at == NOW - days(30) and "30 days ago" in reason


def test_access_review_too_old_or_missing_fails():
    assert c.judge_access_review(NOW - days(91), 90, NOW)[0] == "fail"
    assert c.judge_access_review(None, 90, NOW)[0] == "fail"


def test_restore_drill_rules():
    assert c.judge_restore_drill(None, None, 90, NOW)[0] == "fail"
    ok = c.judge_restore_drill(NOW - days(10), (NOW - days(10), "pass"), 90, NOW)
    assert ok[0] == "pass"
    # A later failing drill is called out but an in-window pass still passes.
    later_fail = c.judge_restore_drill(NOW - days(10), (NOW - days(2), "fail"), 90, NOW)
    assert later_fail[0] == "pass" and "failed" in later_fail[1]
    only_fail = c.judge_restore_drill(None, (NOW - days(2), "fail"), 90, NOW)
    assert only_fail[0] == "fail"


def test_chain_judge_never_names_another_workspace():
    assert c.judge_chain({"verified": True, "rows_checked": 5}, NOW)[0] == "pass"
    status, reason, _ = c.judge_chain({"verified": False, "in_this_workspace": False}, NOW)
    assert status == "fail" and "outside this workspace" in reason
    assert c.judge_chain(None, NOW)[0] == "unknown"


def test_data_policy_judge():
    assert c.judge_data_policies([], NOW)[0] == "unknown"
    assert c.judge_data_policies([("Shop", {"capture_level": "minimal"})], NOW)[0] == "pass"
    status, reason, _ = c.judge_data_policies([("Shop", {"x": 1}), ("Blog", None), ("Docs", {})], NOW)
    assert status == "fail" and "2 of 3" in reason and "Blog" in reason


def test_overdue_findings_judge_counts_only_chosen_severities():
    assert c.judge_overdue_findings({"high": 2}, ["critical"], NOW)[0] == "pass"
    status, reason, _ = c.judge_overdue_findings({"critical": 1, "high": 2}, ["critical", "high"], NOW)
    assert status == "fail" and "3 finding" in reason


def test_mfa_judge():
    assert c.judge_mfa(True, [("a@x", False)], NOW)[0] == "pass"
    assert c.judge_mfa(False, [], NOW)[0] == "unknown"
    assert c.judge_mfa(False, [("a@x", True), ("b@x", False)], NOW)[0] == "fail"
    assert c.judge_mfa(False, [("a@x", True)], NOW)[0] == "pass"


def test_api_key_judge():
    assert c.judge_api_keys([], 365, NOW)[0] == "pass"
    assert c.judge_api_keys([(NOW, None)], 365, NOW)[0] == "fail"
    assert c.judge_api_keys([(NOW, NOW + days(400))], 365, NOW)[0] == "fail"
    assert c.judge_api_keys([(NOW, NOW + days(30))], 365, NOW)[0] == "pass"


def test_sso_judge():
    assert c.judge_sso(True, NOW)[0] == "pass"
    assert c.judge_sso(False, NOW)[0] == "fail"
    assert c.judge_sso(None, NOW)[0] == "unknown"


# --- status + freshness ---------------------------------------------------------

def res(status, observed=None):
    return c.EvidenceResult("t", "Check", status, "why", observed)


def status(**kw):
    kw.setdefault("results", [])
    kw.setdefault("manual", [])
    kw.setdefault("exceptions", [])
    kw.setdefault("freshness_days", 30)
    return c.control_status(now=NOW, **kw)["status"]


def test_nothing_recorded_is_not_assessed():
    assert status() == "not_assessed"
    assert status(results=[res("unknown")]) == "not_assessed"


def test_fresh_pass_is_passing_and_old_pass_is_stale():
    assert status(results=[res("pass", NOW - days(29))]) == "passing"
    assert status(results=[res("pass", NOW - days(31))]) == "stale"
    assert status(manual=[(NOW - days(31), "pass")]) == "stale"
    # The NEWEST passing evidence decides freshness.
    assert status(manual=[(NOW - days(100), "pass"), (NOW - days(1), "pass")]) == "passing"


def test_any_automated_fail_or_newest_manual_fail_is_failing():
    assert status(results=[res("pass", NOW), res("fail", NOW)]) == "failing"
    assert status(manual=[(NOW - days(2), "pass"), (NOW - days(1), "fail")]) == "failing"
    assert status(manual=[(NOW - days(1), "pass"), (NOW - days(2), "fail")]) == "passing"


def exc(state="approved", expires=NOW + days(10)):
    return SimpleNamespace(status=state, expires_at=expires, requested_by_id=1)


def test_active_exception_wins_and_expired_one_stops_counting():
    failing = [res("fail", NOW)]
    assert status(results=failing, exceptions=[exc()]) == "excepted"
    assert status(results=failing, exceptions=[exc(expires=NOW - timedelta(seconds=1))]) == "failing"
    assert status(results=failing, exceptions=[exc(state="pending")]) == "failing"
    assert status(results=failing, exceptions=[exc(state="revoked")]) == "failing"


def test_exception_state():
    assert c.exception_state(exc(), NOW) == "approved"
    assert c.exception_state(exc(expires=NOW), NOW) == "expired"
    assert c.exception_state({"status": "pending", "expires_at": NOW - days(1)}, NOW) == "pending"


# --- exception requests + four-eyes ----------------------------------------------

def test_exception_request_limits():
    c.validate_exception_request("vendor delay", NOW + days(365), NOW)
    for reason, expires in [("", NOW + days(5)), ("x", None), ("x", NOW),
                            ("x", NOW + days(365) + timedelta(seconds=1))]:
        with pytest.raises(c.ComplianceError):
            c.validate_exception_request(reason, expires, NOW)


def test_four_eyes():
    pending = SimpleNamespace(status="pending", requested_by_id=7, expires_at=NOW + days(30))
    with pytest.raises(c.ComplianceError, match="four-eyes"):
        c.check_approval(pending, 7, NOW)
    c.check_approval(pending, 8, NOW)


def test_approval_refuses_non_pending_or_expired():
    with pytest.raises(c.ComplianceError):
        c.check_approval(SimpleNamespace(status="approved", requested_by_id=1,
                                         expires_at=NOW + days(3)), 2, NOW)
    with pytest.raises(c.ComplianceError):
        c.check_approval(SimpleNamespace(status="pending", requested_by_id=1,
                                         expires_at=NOW - days(1)), 2, NOW)


# --- uploads + drill view -------------------------------------------------------

def test_evidence_file_types():
    assert c.evidence_file_type("Report.PDF") == "application/pdf"
    for bad in ("page.html", "x.svg", "run.sh", "noext"):
        with pytest.raises(c.ComplianceError):
            c.evidence_file_type(bad)
    assert c.safe_filename("../../etc/pass wd.txt") == "pass wd.txt"
    assert c.safe_filename('a"b<c>.pdf') == "a_b_c_.pdf"


def test_drill_view_hides_instance_details_from_workspaces():
    drill = SimpleNamespace(id=1, ran_at=NOW, status="pass", duration_seconds=4,
                            dump_name="traceiq.sql.gz", target="db", reporter_label="ops@x",
                            received_at=NOW,
                            checks=[{"check": "row counts", "status": "PASS", "detail": "users=812"}])
    reduced = c.drill_view(drill, full=False)
    assert reduced["checks"] == [{"check": "row counts", "status": "PASS"}]
    assert "dump_name" not in reduced and "reporter" not in reduced
    assert c.drill_view(drill, full=True)["checks"][0]["detail"] == "users=812"
