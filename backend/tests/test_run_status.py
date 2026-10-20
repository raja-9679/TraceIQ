"""Terminal vs verdict statuses and error kinds (WP15f L2)."""
from types import SimpleNamespace

import pytest

from app.models import TestStatus
from app.services import run_status
from app.services.run_status import (
    derive_error_kind, from_wire, has_verdict, is_terminal, normalise_result, run_outcome,
)


def test_partition_covers_every_status_exactly_once():
    groups = [run_status.LIVE, run_status.VERDICT, run_status.NO_VERDICT]
    flat = [s for g in groups for s in g]
    assert sorted(flat, key=str) == sorted(TestStatus, key=str)
    assert len(flat) == len(set(flat))
    assert set(run_status.TERMINAL) == set(run_status.VERDICT) | set(run_status.NO_VERDICT)


def test_cancelled_and_timed_out_are_over_but_not_a_verdict():
    for st in (TestStatus.CANCELLED, TestStatus.TIMED_OUT, "cancelled", "timed_out"):
        assert is_terminal(st)
        assert not has_verdict(st)
    assert has_verdict("error") and is_terminal("error")
    assert not is_terminal("running") and not is_terminal("nonsense")


def test_wire_mapping_keeps_unknown_as_error():
    assert from_wire("cancelled") == TestStatus.CANCELLED
    assert from_wire("TIMED_OUT") == TestStatus.TIMED_OUT
    assert from_wire("exploded") == TestStatus.ERROR
    assert from_wire(None) == TestStatus.ERROR


@pytest.mark.parametrize("status, message, reported, expected", [
    ("passed", None, None, None),
    ("failed", "Timeout 30000ms exceeded waiting for #buy", None, "test"),   # a step timeout IS a test failure
    ("failed", "browserType.launch: Executable doesn't exist at /ms-playwright", None, "infrastructure"),
    ("failed", "Unknown step type 'frobnicate' — not supported by this worker", None, "infrastructure"),
    ("error", "Suite setup failed: setup step 2 returned 500", None, "setup"),
    ("failed", "Suite setup failed: x", None, "test"),        # setup words only mean setup on ERROR
    ("error", "Manual test case — not executed by a worker", None, "not_executed"),
    ("failed", "Job j1 exceeded MAX_JOB_DURATION_MS (600000ms)", None, "timeout"),
    ("failed", "anything", "setup", "setup"),                   # the worker's kind wins
    ("failed", "anything", "bogus", "test"),                    # unknown kinds are ignored
    ("cancelled", "x", "test", "cancelled"),                    # status decides for no-verdict
    ("timed_out", "x", None, "timeout"),
])
def test_error_kind(status, message, reported, expected):
    assert derive_error_kind(status, message, reported) == expected


def test_old_worker_platform_timeout_becomes_timed_out():
    # Pre-WP15f workers report a blown job budget as an ordinary failure.
    status, kind = normalise_result(
        "failed", "Case timed out: the job time budget is exhausted (teardown time is reserved)")
    assert (status, kind) == (TestStatus.TIMED_OUT, "timeout")


def test_status_and_kind_never_disagree():
    assert normalise_result("failed", "x", "cancelled") == (TestStatus.CANCELLED, "cancelled")
    assert normalise_result("cancelled", "x", "test") == (TestStatus.CANCELLED, "cancelled")
    assert normalise_result("passed", None, "test") == (TestStatus.PASSED, None)
    assert normalise_result("failed", "assert", None) == (TestStatus.FAILED, "test")


@pytest.mark.parametrize("statuses, counter, expected", [
    (["passed", "passed"], 0, TestStatus.PASSED),
    (["passed", "failed"], 1, TestStatus.FAILED),
    (["passed", "error"], 1, TestStatus.FAILED),
    (["passed", "timed_out"], 1, TestStatus.TIMED_OUT),     # counter lumps it in; results explain it
    (["cancelled", "cancelled"], 2, TestStatus.CANCELLED),
    (["timed_out", "cancelled"], 2, TestStatus.TIMED_OUT),
    (["failed", "timed_out"], 2, TestStatus.FAILED),        # a real failure wins
    ([], 1, TestStatus.FAILED),                             # job with no result rows: as before
    (["passed"], 1, TestStatus.FAILED),                     # unexplained counter: as before
])
def test_run_outcome(statuses, counter, expected):
    assert run_outcome(statuses, counter) == expected


def test_retention_purges_the_new_terminal_states():
    from app.tasks.cleanup_tasks import _PURGEABLE_STATUSES
    assert TestStatus.CANCELLED in _PURGEABLE_STATUSES
    assert TestStatus.TIMED_OUT in _PURGEABLE_STATUSES
    assert TestStatus.RUNNING not in _PURGEABLE_STATUSES


def test_outbound_events_and_completed_alias():
    from app.tasks.outbound_webhook_tasks import _event_for_run, _match_event_filter
    run = SimpleNamespace
    assert _event_for_run(run(status=TestStatus.CANCELLED)) == "run.cancelled"
    assert _event_for_run(run(status=TestStatus.TIMED_OUT)) == "run.timed_out"
    assert _event_for_run(run(status=TestStatus.ERROR)) == "run.completed"
    # Subscribers filtered on run.completed used to get timeouts (then ERROR).
    assert _match_event_filter("run.completed", "run.timed_out")
    assert _match_event_filter("run.completed", "run.cancelled")
    assert not _match_event_filter("run.failed", "run.timed_out")
    assert not _match_event_filter("run.completed", "run.failed")
    assert _match_event_filter("run.cancelled", "run.cancelled")


def test_paging_pages_timeouts_never_cancels():
    from app.models import RunTrigger
    from app.tasks.paging_tasks import page_decision
    base = dict(project_id=1, local_worker_id=None, triggered_by=RunTrigger.SCHEDULE.value)
    assert page_decision(SimpleNamespace(status=TestStatus.TIMED_OUT, **base), False) == "trigger"
    assert page_decision(SimpleNamespace(status=TestStatus.CANCELLED, **base), False) is None


def test_monitor_counts_timeouts_not_cancels():
    from app.tasks.monitor_tasks import _TERMINAL
    assert TestStatus.TIMED_OUT in _TERMINAL
    assert TestStatus.CANCELLED not in _TERMINAL


def test_cancelled_run_is_never_reopened_by_late_results(monkeypatch):
    from unittest.mock import MagicMock
    from app.tasks import result_aggregator
    finalized = []
    monkeypatch.setattr(result_aggregator, "finalize_on_commit", lambda *a, **k: finalized.append(a))
    run = SimpleNamespace(id=5, total_tests=2, passed_tests=0, failed_tests=0,
                          status=TestStatus.CANCELLED, error_message="cancelled")
    result_aggregator.update_run_from_progress(
        run, 5, {"total": "2", "completed": "2", "passed": "2", "failed": "0"}, MagicMock())
    assert run.status == TestStatus.CANCELLED
    assert finalized == []
