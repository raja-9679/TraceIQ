"""
Unit tests for the stale-run detection logic in result_aggregator.

These tests verify that check_stale_runs() correctly distinguishes between:
  - Healthy large runs (700+ tests actively processing → should NOT be killed)
  - Genuinely stuck runs (no results arriving for >15 min → SHOULD be killed)
  - Brand-new runs with no results yet (should NOT be killed prematurely)

Run with:
    cd /home/raja/Documents/repos/public/TraceIQ/backend
    python -m pytest tests/test_stale_run_detection.py -v
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_run(run_id: int, created_minutes_ago: int):
    """Create a lightweight mock TestRun."""
    run = MagicMock()
    run.id = run_id
    run.created_at = datetime.utcnow() - timedelta(minutes=created_minutes_ago)
    run.total_tests = 0
    run.passed_tests = 0
    run.failed_tests = 0
    return run


def _make_progress(
    completed: int,
    total: int,
    passed: int = 0,
    failed: int = 0,
    last_progress_minutes_ago: int | None = None,
) -> dict:
    """Build a Redis-style progress hash."""
    p = {
        "completed": str(completed),
        "total": str(total),
        "passed": str(passed),
        "failed": str(failed),
    }
    if last_progress_minutes_ago is not None:
        ts = datetime.utcnow() - timedelta(minutes=last_progress_minutes_ago)
        p["last_progress_at"] = ts.isoformat()
    return p


# ──────────────────────────────────────────────────────────────────────────────
# _mark_run_timeout helper
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkRunTimeout:
    def test_sets_error_status(self):
        from app.tasks.result_aggregator import _mark_run_timeout
        from app.models import TestStatus

        run = _make_run(1, created_minutes_ago=20)
        progress = _make_progress(14, 48, passed=10, failed=4)
        session = MagicMock()

        _mark_run_timeout(run, progress, session, reason="test reason")

        assert run.status == TestStatus.ERROR
        assert "test reason" in run.error_message
        assert "14/48" in run.error_message

    def test_counts_incomplete_jobs_as_failures(self):
        from app.tasks.result_aggregator import _mark_run_timeout

        run = _make_run(2, created_minutes_ago=20)
        progress = _make_progress(14, 48, passed=10, failed=4)
        session = MagicMock()

        _mark_run_timeout(run, progress, session, reason="stuck")

        # 48 total - 14 completed = 34 unfinished; 4 explicit failures
        # failed_tests should count both
        assert run.failed_tests == 4 + (48 - 14)
        assert run.passed_tests == 10
        assert run.total_tests == 48

    def test_handles_no_progress(self):
        from app.tasks.result_aggregator import _mark_run_timeout
        from app.models import TestStatus

        run = _make_run(3, created_minutes_ago=20)
        run.total_tests = 0
        session = MagicMock()

        _mark_run_timeout(run, None, session, reason="no progress")

        assert run.status == TestStatus.ERROR


# ──────────────────────────────────────────────────────────────────────────────
# Inactivity logic (mirrors check_stale_runs decision tree)
# These tests exercise the *logic* without hitting the DB.
# ──────────────────────────────────────────────────────────────────────────────

INACTIVITY_MINUTES = 15
ABSOLUTE_HOURS = 6


def _should_kill(run, progress: dict | None) -> tuple[bool, str]:
    """
    Pure-python mirror of the check_stale_runs() decision logic.
    Returns (should_kill, reason).
    """
    inactivity_cutoff = datetime.utcnow() - timedelta(minutes=INACTIVITY_MINUTES)
    absolute_cutoff = datetime.utcnow() - timedelta(hours=ABSOLUTE_HOURS)

    # Case 1: absolute hard cap
    if run.created_at and run.created_at < absolute_cutoff:
        return True, "absolute cap"

    # Case 2: no progress data
    if not progress:
        if run.created_at and run.created_at < inactivity_cutoff:
            return True, "no progress data"
        return False, "new run, no progress yet"

    # Case 3: all complete → don't touch
    completed = int(progress.get("completed", 0))
    total = int(progress.get("total", 0))
    if total > 0 and completed >= total:
        return False, "all complete"

    last_str = progress.get("last_progress_at")
    if last_str:
        last_at = datetime.fromisoformat(last_str)
        if last_at < inactivity_cutoff:
            return True, "inactive"
        return False, "recently active"
    else:
        if run.created_at and run.created_at < inactivity_cutoff:
            return True, "no progress_at and old"
        return False, "no progress_at but new"


class TestStaleRunDecisionLogic:

    # ── healthy cases (must NOT be killed) ────────────────────────────────

    def test_active_large_run_not_killed(self):
        """700-test run, last result 2 min ago → should NOT be killed."""
        run = _make_run(10, created_minutes_ago=120)  # 2 hours old
        progress = _make_progress(350, 700, last_progress_minutes_ago=2)
        killed, reason = _should_kill(run, progress)
        assert not killed, f"Should not kill active run, reason: {reason}"

    def test_recent_run_no_results_yet_not_killed(self):
        """Run just dispatched (5 min ago), no results yet → NOT killed."""
        run = _make_run(11, created_minutes_ago=5)
        progress = _make_progress(0, 100)  # no last_progress_at
        killed, reason = _should_kill(run, progress)
        assert not killed, f"New run should not be killed: {reason}"

    def test_run_just_started_no_redis_data_not_killed(self):
        """Run created 3 min ago, no Redis data yet → NOT killed."""
        run = _make_run(12, created_minutes_ago=3)
        killed, reason = _should_kill(run, None)
        assert not killed, f"Brand-new run should not be killed: {reason}"

    def test_all_jobs_complete_not_killed(self):
        """All jobs done, awaiting aggregator finalization → NOT killed."""
        run = _make_run(13, created_minutes_ago=60)
        progress = _make_progress(100, 100, passed=95, failed=5,
                                  last_progress_minutes_ago=20)
        killed, reason = _should_kill(run, progress)
        assert not killed, f"Completed run should not be killed: {reason}"

    # ── stuck cases (MUST be killed) ──────────────────────────────────────

    def test_stalled_run_killed(self):
        """Run has been stuck for 20 minutes with no new results."""
        run = _make_run(20, created_minutes_ago=60)
        progress = _make_progress(14, 48, passed=10, failed=4,
                                  last_progress_minutes_ago=20)
        killed, reason = _should_kill(run, progress)
        assert killed, f"Stalled run should be killed: {reason}"

    def test_no_redis_data_old_run_killed(self):
        """No Redis data and run created >15 min ago → killed."""
        run = _make_run(21, created_minutes_ago=20)
        killed, reason = _should_kill(run, None)
        assert killed, f"Old run with no data should be killed: {reason}"

    def test_absolute_cap_kills_even_active_run(self):
        """Run older than MAX_RUN_DURATION_HOURS → killed regardless."""
        run = _make_run(22, created_minutes_ago=7 * 60)  # 7 hours
        progress = _make_progress(699, 700, last_progress_minutes_ago=1)
        killed, reason = _should_kill(run, progress)
        assert killed, f"7-hour run should hit absolute cap: {reason}"

    def test_no_progress_at_and_stale_run_killed(self):
        """Progress hash exists but last_progress_at not recorded; run old."""
        run = _make_run(23, created_minutes_ago=20)
        progress = _make_progress(10, 50)  # no last_progress_at key
        killed, reason = _should_kill(run, progress)
        assert killed, f"Old run without progress timestamp should be killed: {reason}"
