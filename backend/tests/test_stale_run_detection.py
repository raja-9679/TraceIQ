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
from datetime import datetime, timedelta, timezone
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
    def test_sets_timed_out_status(self):
        from app.tasks.result_aggregator import _mark_run_timeout
        from app.models import TestStatus

        run = _make_run(1, created_minutes_ago=20)
        progress = _make_progress(14, 48, passed=10, failed=4)
        session = MagicMock()

        _mark_run_timeout(run, progress, session, reason="test reason")

        assert run.status == TestStatus.TIMED_OUT
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

        assert run.status == TestStatus.TIMED_OUT


# ──────────────────────────────────────────────────────────────────────────────
# Inactivity logic — the REAL decision function check_stale_runs() uses.
# (These used to exercise a copy of the logic kept in this file, which is how
# a crash in the real code and a wrong rule both went unnoticed.)
# ──────────────────────────────────────────────────────────────────────────────

INACTIVITY_MINUTES = 15
ABSOLUTE_HOURS = 6


def _should_kill(run, progress: dict | None) -> tuple[bool, str]:
    from app.tasks.result_aggregator import stale_reason
    reason = stale_reason(run.created_at, progress, datetime.utcnow(),
                          INACTIVITY_MINUTES, ABSOLUTE_HOURS)
    return reason is not None, reason or "healthy"


def _iso_z(minutes_ago: int) -> str:
    """What the Node worker writes: new Date().toISOString() — aware, 'Z'."""
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

    def test_queued_case_waiting_for_a_worker_not_killed(self):
        """Per-case run of an 800-case suite on 6 workers: dispatched 50 min
        ago, no worker has picked its job up yet → still queued, NOT stuck."""
        run = _make_run(14, created_minutes_ago=50)
        progress = _make_progress(0, 1)
        progress["status"] = "running"
        killed, reason = _should_kill(run, progress)
        assert not killed, f"Queued run must not time out: {reason}"

    def test_picked_up_recently_not_killed(self):
        run = _make_run(15, created_minutes_ago=50)
        progress = _make_progress(0, 1)
        progress["worker_started_at"] = _iso_z(3)
        killed, reason = _should_kill(run, progress)
        assert not killed, reason

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

    def test_absolute_cap_kills_a_run_queued_too_long(self):
        run = _make_run(23, created_minutes_ago=7 * 60)
        killed, _ = _should_kill(run, _make_progress(0, 1))
        assert killed

    def test_worker_picked_up_but_no_result_killed(self):
        """A worker took the job 20 min ago (aware 'Z' timestamp, as the Node
        worker writes it) and nothing came back → stuck. This exact value
        used to raise TypeError and crash the whole sweep."""
        run = _make_run(24, created_minutes_ago=60)
        progress = _make_progress(0, 1)
        progress["worker_started_at"] = _iso_z(20)
        killed, reason = _should_kill(run, progress)
        assert killed and "no initial results" in reason

    def test_unparseable_timestamps_are_ignored(self):
        run = _make_run(25, created_minutes_ago=60)
        progress = _make_progress(0, 1)
        progress["worker_started_at"] = "not-a-date"
        progress["last_progress_at"] = ""
        killed, reason = _should_kill(run, progress)
        assert not killed, reason


class TestCheckStaleRunsSweep:
    """The Celery task itself, over a mixed set of runs: one stuck run must
    not stop (or roll back) the sweep, and queued runs are left alone."""

    def _sweep(self, monkeypatch, runs, hashes, setting=None):
        from app.tasks import result_aggregator as ra
        from app.services import instance_settings
        session = MagicMock()
        session.exec.return_value.all.return_value = runs
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        monkeypatch.setattr(ra, "Session", lambda *_a, **_k: ctx)
        fake_redis = MagicMock()
        fake_redis.hgetall.side_effect = lambda key: hashes[int(key.split(":")[1])]
        monkeypatch.setattr(ra, "redis_client", fake_redis)
        monkeypatch.setattr(instance_settings, "effective",
                            lambda key: setting if key == "STALE_RUN_INACTIVITY_MINUTES" else None)
        ra.check_stale_runs()
        return session

    def test_instance_setting_sets_the_inactivity_window(self, monkeypatch):
        """Stuck-run timeout raised to 40 min in Instance settings: a test a
        worker picked up 25 min ago (slow uncached page) is left running."""
        from app.models import TestStatus
        run = _make_run(41, created_minutes_ago=60)
        run.status = TestStatus.RUNNING
        hashes = {41: {**_make_progress(0, 1), "worker_started_at": _iso_z(25)}}
        self._sweep(monkeypatch, [run], hashes, setting=40)
        assert run.status == TestStatus.RUNNING
        self._sweep(monkeypatch, [run], hashes, setting=None)  # blank → 15
        assert run.status == TestStatus.TIMED_OUT

    def test_sweep_marks_only_the_stuck_run_and_commits(self, monkeypatch):
        from app.tasks import result_aggregator as ra
        from app.models import TestStatus
        from app.services import instance_settings
        monkeypatch.setattr(instance_settings, "effective", lambda key: None)

        stuck = _make_run(31, created_minutes_ago=60)
        queued = _make_run(32, created_minutes_ago=60)
        active = _make_run(33, created_minutes_ago=60)
        for r in (stuck, queued, active):
            r.status = TestStatus.RUNNING
        hashes = {
            31: {**_make_progress(0, 1), "worker_started_at": _iso_z(25)},
            32: {**_make_progress(0, 1), "status": "running"},
            33: {**_make_progress(0, 1), "worker_started_at": _iso_z(2)},
        }

        session = MagicMock()
        session.exec.return_value.all.return_value = [stuck, queued, active]
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        monkeypatch.setattr(ra, "Session", lambda *_a, **_k: ctx)
        fake_redis = MagicMock()
        fake_redis.hgetall.side_effect = lambda key: hashes[int(key.split(":")[1])]
        monkeypatch.setattr(ra, "redis_client", fake_redis)
        monkeypatch.setattr(ra, "finalize_on_commit", lambda *a, **k: None, raising=False)

        ra.check_stale_runs()

        assert stuck.status == TestStatus.TIMED_OUT
        assert queued.status == TestStatus.RUNNING
        assert active.status == TestStatus.RUNNING
        session.commit.assert_called_once()
