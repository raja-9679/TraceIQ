"""Test cycles: the progress rules (app/services/cycles.py), no database."""
from datetime import date, datetime, timedelta

import pytest

from app.services import cycles as cy

T0 = datetime(2026, 11, 1, 9, 0)


def ev(rid, status="passed", *, at=0, browser="chromium", device=None, manual=False,
       outcome=None, tester=None, run=None):
    return cy.Evidence(result_id=rid, case_id=1, status=status, run_id=run or rid * 10,
                       browser=browser, device=device, at=T0 + timedelta(hours=at),
                       manual=manual, outcome=outcome, tester_id=tester)


def test_no_results_is_not_run():
    assert cy.classify([])["status"] == cy.NOT_RUN


def test_rows_without_a_verdict_do_not_count():
    rows = [ev(1, "cancelled"), ev(2, "pending"), ev(3, "running"),
            ev(4, "pending", manual=True, outcome=None, browser="manual")]
    out = cy.classify(rows)
    assert out["status"] == cy.NOT_RUN and out["run_id"] is None


def test_green_rerun_replaces_red():
    out = cy.classify([ev(1, "failed", at=0), ev(2, "passed", at=1)])
    assert out["status"] == cy.PASSED and out["result_id"] == 2


def test_cancelled_rerun_does_not_hide_the_failure():
    out = cy.classify([ev(1, "failed", at=0), ev(2, "cancelled", at=1)])
    assert out["status"] == cy.FAILED and out["result_id"] == 1


def test_one_browser_cannot_hide_another():
    out = cy.classify([ev(1, "failed", at=0, browser="firefox"), ev(2, "passed", at=5)])
    assert out["status"] == cy.FAILED and out["result_id"] == 1
    assert out["configurations"] == 2


def test_error_and_timeout_are_blocked():
    assert cy.classify([ev(1, "error")])["status"] == cy.BLOCKED
    assert cy.classify([ev(1, "timed_out")])["status"] == cy.BLOCKED


def test_failed_outranks_blocked():
    out = cy.classify([ev(1, "error", browser="webkit"), ev(2, "failed", browser="firefox")])
    assert out["status"] == cy.FAILED


def test_manual_outcomes_and_tester():
    rows = [ev(1, "error", manual=True, outcome="blocked", browser="manual", tester=7, at=0),
            ev(2, "pending", manual=True, outcome="skipped", browser="manual", tester=8, at=1)]
    out = cy.classify(rows)
    assert out["status"] == cy.SKIPPED and out["tester_id"] == 8
    # The manual status column says PENDING for "skipped"; the outcome decides.
    assert cy.evidence_state(rows[1]) == cy.SKIPPED


def test_newest_is_by_time_not_id():
    out = cy.classify([ev(9, "failed", at=0), ev(2, "passed", at=3)])
    assert out["status"] == cy.PASSED


def test_summary_counts_and_percentages():
    s = cy.summarize(["passed", "passed", "failed", "not_run"])
    assert s["total"] == 4 and s["executed"] == 3
    assert s["counts"] == {"passed": 2, "failed": 1, "blocked": 0, "skipped": 0, "not_run": 1}
    assert s["percent_executed"] == 75 and s["percent_passed"] == 50


def test_empty_cycle_has_no_percentage():
    s = cy.summarize([])
    assert s["percent_executed"] is None and s["percent_passed"] is None


def test_by_assignee_puts_unassigned_last():
    items = [{"assignee_id": None, "status": "not_run"}, {"assignee_id": None, "status": "passed"},
             {"assignee_id": None, "status": "passed"}, {"assignee_id": 4, "status": "failed"}]
    groups = cy.by_assignee(items)
    assert [g["assignee_id"] for g in groups] == [4, None]
    assert groups[1]["total"] == 3


def test_name_and_dates():
    assert cy.clean_name("  Release   2.4 \n") == "Release 2.4"
    with pytest.raises(cy.CycleError):
        cy.clean_name("   ")
    with pytest.raises(cy.CycleError):
        cy.check_dates(date(2026, 11, 10), date(2026, 11, 9))
    cy.check_dates(date(2026, 11, 10), date(2026, 11, 10))


def test_root_suites_survive_broken_chains():
    roots = cy.root_suites({1: None, 2: 1, 3: 2, 4: 99, 5: 6, 6: 5})
    assert roots[3] == 1 and roots[1] == 1
    assert roots[4] == 4            # parent outside the project: stops there
    assert roots[5] in (5, 6)       # a loop ends instead of spinning


def test_every_counted_run_is_evidence_not_only_the_worst():
    out = cy.classify([ev(1, "failed", browser="firefox", run=11), ev(2, "passed", run=12),
                       ev(3, "failed", at=-5, run=13)])       # superseded on chromium
    assert out["run_id"] == 11 and out["run_ids"] == [11, 12]
    assert cy.classify([])["run_ids"] == []
