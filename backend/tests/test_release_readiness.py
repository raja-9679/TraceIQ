"""Release-readiness gate checks and verdict — the decisions, without a database.

Queries and access control are covered against Postgres in
tests/integration/test_release_readiness_db.py.
"""
from datetime import datetime, timedelta

from app.models import QualityGateCheck, QualityGatePolicy, QualityGateResult, TestStatus
from app.services.release_readiness import (
    FAILED, NOT_RUN, PASSED, ResultRow, classify_case, critical_passed_check,
    evidence_age_check, flaky_cases_check, is_critical, name_list,
    requirement_status, verdict_for,
)

P, F, E = TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR
NOW = datetime(2026, 10, 15, 12, 0)


def row(rid, status, browser="chromium", device=None, run=None, at=None):
    return ResultRow(result_id=rid, case_id=1, status=status, run_id=run or rid,
                     browser=browser, device=device, at=at)


# -- policy defaults --------------------------------------------------------

def test_new_checks_are_off_by_default():
    p = QualityGatePolicy()
    assert p.max_flaky_cases is None
    assert p.require_critical_passed is False
    assert p.max_evidence_age_days is None
    # A policy stored before these fields existed still loads.
    old = QualityGatePolicy(**{"min_pass_rate": 90.0, "max_high_severity_findings": 0})
    assert old.max_flaky_cases is None


def test_priority_match_is_trimmed_and_case_insensitive():
    assert is_critical("Critical ") and is_critical("CRITICAL")
    assert not is_critical("high") and not is_critical(None)


def test_name_list_caps():
    assert name_list([str(i) for i in range(12)], cap=3) == "0, 1, 2 (+9 more)"


# -- classify_case ----------------------------------------------------------

def test_no_results_is_not_run_not_pass():
    assert classify_case([])[0] == NOT_RUN


def test_green_rerun_replaces_red_in_the_same_browser():
    assert classify_case([row(1, F), row(2, P)])[0] == PASSED


def test_red_rerun_after_green_fails():
    status, run_id, detail = classify_case([row(1, P), row(2, F)])
    assert status == FAILED and run_id == 2 and "failed in run 2" in detail


def test_pass_in_one_browser_does_not_hide_failure_in_another():
    status, _, detail = classify_case([row(1, E, "firefox"), row(2, P, "chromium")])
    assert status == FAILED and "firefox" in detail


# -- flaky_cases ------------------------------------------------------------

def test_flaky_cases_within_limit():
    c = flaky_cases_check(["b", "a"], 2)
    assert c.passed and c.actual == "2 flaky" and c.threshold == "<= 2"
    assert c.detail == "a, b"


def test_flaky_cases_over_limit():
    c = flaky_cases_check(["a"], 0)
    assert not c.passed and not c.missing_data


def test_no_flaky_cases():
    c = flaky_cases_check([], 0)
    assert c.passed and c.detail is None


# -- critical_passed --------------------------------------------------------

CRIT = [(1, "login"), (2, "checkout"), (3, "pay")]


def test_all_critical_passed():
    c = critical_passed_check(CRIT, {1: PASSED, 2: PASSED, 3: PASSED}, "for commit abc")
    assert c.passed and c.actual == "3 of 3 passed"


def test_missing_critical_is_named_and_flagged_as_missing_data():
    c = critical_passed_check(CRIT, {1: PASSED, 2: PASSED}, "for commit abc")
    assert not c.passed and c.missing_data
    assert c.detail == "no result for commit abc: pay"


def test_failed_critical_is_not_missing_data():
    c = critical_passed_check(CRIT, {1: FAILED, 2: PASSED}, "for commit abc")
    assert not c.passed and not c.missing_data
    assert "failed: login" in c.detail and "no result for commit abc: pay" in c.detail
    assert c.actual == "1 of 3 passed"


def test_no_critical_cases_fails_closed():
    c = critical_passed_check([], {}, "x")
    assert not c.passed and c.missing_data and "priority 'critical'" in c.detail


# -- evidence_age -----------------------------------------------------------

def test_fresh_evidence_passes():
    c = evidence_age_check([(1, "a"), (2, "b")],
                           {1: NOW - timedelta(days=1), 2: NOW - timedelta(days=3)},
                           7, NOW, "critical cases")
    assert c.passed and c.actual == "oldest 3.0d (b)"
    assert c.threshold == "<= 7d (critical cases)"


def test_stale_evidence_fails_and_names_the_case():
    c = evidence_age_check([(1, "a"), (2, "b")],
                           {1: NOW - timedelta(days=1), 2: NOW - timedelta(days=10)},
                           7, NOW, "critical cases")
    assert not c.passed and not c.missing_data and c.detail == "stale: b 10d"


def test_never_passed_is_missing_data_not_a_pass():
    c = evidence_age_check([(1, "a"), (2, "b")], {1: NOW}, 7, NOW, "s")
    assert not c.passed and c.missing_data
    assert "never passed: b" in c.detail and "1 never passed" in c.actual


def test_no_cases_at_all():
    c = evidence_age_check([], {}, 7, NOW, "s")
    assert not c.passed and c.missing_data


# -- requirements -----------------------------------------------------------

def test_requirement_status():
    assert requirement_status([PASSED, PASSED]) == "passing"
    assert requirement_status([PASSED, NOT_RUN]) == "partial"
    assert requirement_status([NOT_RUN]) == "untested"
    assert requirement_status([PASSED, FAILED]) == "failing"


# -- verdict ----------------------------------------------------------------

def gate(*checks):
    return QualityGateResult(project_id=1, passed=all(c.passed for c in checks),
                             evaluated_run_ids=[1, 2], checks=list(checks))


def chk(name, passed, missing=False, detail=None):
    return QualityGateCheck(name=name, passed=passed, actual="a", threshold="t",
                            missing_data=missing, detail=detail)


def test_verdict_ready_mentions_partial_coverage():
    v, reasons = verdict_for(gate(chk("pass_rate", True),
                                  chk("coverage", True, detail="partial coverage")), True)
    assert v == "ready"
    assert reasons[0] == "all 2 checks passed over 2 run(s)"
    assert reasons[1].startswith("Coverage: ")


def test_reasons_name_checks_in_words_not_ids():
    _, reasons = verdict_for(gate(chk("external_tests", False), chk("flaky_cases", False),
                                  chk("some_new_check", False)), True)
    assert reasons[0].startswith("External CI tests: a (needs t)")
    assert reasons[1].startswith("Flaky tests: ")
    assert reasons[2].startswith("Some new check: ")


def test_newest_result_is_by_evidence_time_not_insert_order():
    # A rerun written later (higher id) but finished earlier does not replace
    # the newer failure.
    old_pass = row(9, P, at=NOW - timedelta(days=2))
    new_fail = row(3, F, at=NOW)
    assert classify_case([old_pass, new_fail])[0] == FAILED
    assert classify_case([row(3, F, at=NOW - timedelta(days=2)), row(9, P, at=NOW)])[0] == PASSED


def test_verdict_not_ready_when_anything_actually_failed():
    v, reasons = verdict_for(gate(chk("pass_rate", False),
                                  chk("critical_passed", False, missing=True)), True)
    assert v == "not_ready" and len(reasons) == 2


def test_verdict_insufficient_when_only_evidence_is_missing():
    v, _ = verdict_for(gate(chk("pass_rate", True),
                            chk("critical_passed", False, missing=True)), True)
    assert v == "insufficient_data"


def test_verdict_insufficient_without_runs():
    v, _ = verdict_for(gate(chk("pass_rate", False, missing=True)), False)
    assert v == "insufficient_data"
