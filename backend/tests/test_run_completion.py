"""Run completion side effects (WP15f).

Before WP15f a finished distributed run only got its notifications, outbound
webhooks, monitor check, paging, scans and triage when the LEGACY engine
called POST /runs/{id}/finalize — a service none of the shipped deployments
run. These tests pin the replacement: the aggregator claims the run and queues
the side effects once, after its transaction commits.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import run_completion
from app.services.run_completion import SIDE_EFFECT_TASKS, finalize_on_commit, queue_side_effects


class _Task:
    def __init__(self, calls, name):
        self.calls, self.name = calls, name

    def delay(self, *args):
        self.calls.append((self.name, args))


def _loader(calls, broken=()):
    def load(module_name):
        if module_name in broken:
            raise ImportError(module_name)
        names = [t for m, t, _ in SIDE_EFFECT_TASKS if m == module_name]
        return SimpleNamespace(**{n: _Task(calls, n) for n in names})
    return load


def test_every_side_effect_the_endpoint_used_to_queue_is_still_queued():
    # The eight tasks POST /finalize queued before the refactor, plus the
    # WP16b owner notice.
    assert [t for _, t, _ in SIDE_EFFECT_TASKS] == [
        "send_run_notifications", "dispatch_run_webhooks", "propose_selector_heals_for_run",
        "evaluate_monitor_for_run", "page_for_run", "scan_run_passive", "scan_run_leaks",
        "cluster_run_failures", "notify_case_owners_for_run",
        # WP16c: settles a validation run's proposal (no-op for other runs).
        "record_validation_outcome",
    ]


def test_validation_runs_only_settle_their_proposal(monkeypatch):
    released = []
    from app.services import environment_lock
    monkeypatch.setattr(environment_lock, "release_for_run", lambda rid, **k: released.append(rid))
    calls = []
    queued = queue_side_effects(9, None, loader=_loader(calls), is_validation=True)
    assert queued == ["record_validation_outcome"]
    assert released == [9]          # the environment lease goes on every terminal path


def test_every_finished_run_releases_its_environment_lease(monkeypatch):
    released = []
    from app.services import environment_lock
    monkeypatch.setattr(environment_lock, "release_for_run", lambda rid, **k: released.append(rid))
    queue_side_effects(4, None, loader=_loader([]), is_validation=False)
    assert released == [4]


def test_queue_side_effects_passes_payload_only_to_notifications():
    calls = []
    queued = queue_side_effects(7, {"aiAnalysis": "x"}, loader=_loader(calls))
    assert len(queued) == len(SIDE_EFFECT_TASKS)
    assert ("send_run_notifications", (7, {"aiAnalysis": "x"})) in calls
    assert ("dispatch_run_webhooks", (7,)) in calls


def test_one_broken_task_does_not_stop_the_others():
    calls = []
    queued = queue_side_effects(7, None, loader=_loader(calls, broken={"app.tasks.paging_tasks"}))
    assert "page_for_run" not in queued
    assert "cluster_run_failures" in queued
    assert ("send_run_notifications", (7, {})) in calls


def test_side_effect_modules_exist_and_are_routed():
    # A renamed task would make queue_side_effects log-and-skip forever.
    import importlib
    from app.core.celery_app import celery_app
    for module_name, task_name, _ in SIDE_EFFECT_TASKS:
        task = getattr(importlib.import_module(module_name), task_name)
        assert task.name in celery_app.tasks


def test_lost_claim_queues_nothing(monkeypatch):
    queued = []
    monkeypatch.setattr(run_completion, "queue_side_effects", lambda *a, **k: queued.append(a))
    session = MagicMock()
    session.execute.return_value = SimpleNamespace(rowcount=0)
    assert finalize_on_commit(session, 3) is False
    assert queued == []


def test_won_claim_queues_after_commit_not_before(monkeypatch):
    queued = []
    monkeypatch.setattr(run_completion, "queue_side_effects", lambda *a, **k: queued.append(a))
    listeners = []
    import sqlalchemy.event
    monkeypatch.setattr(sqlalchemy.event, "listen",
                        lambda target, name, fn, once=False: listeners.append((name, fn)))
    session = MagicMock()
    session.execute.return_value = SimpleNamespace(rowcount=1)
    assert finalize_on_commit(session, 3) is True
    assert queued == []                      # nothing before commit
    assert [n for n, _ in listeners] == ["after_commit"]
    listeners[0][1](session)                 # the commit happens
    assert queued == [(3, None)]


def test_claim_error_never_raises(monkeypatch):
    session = MagicMock()
    session.execute.side_effect = RuntimeError("db gone")
    assert finalize_on_commit(session, 3) is False


def test_aggregator_finalizes_a_completed_run(monkeypatch):
    from app.tasks import result_aggregator
    from app.models import TestStatus
    finalized = []
    monkeypatch.setattr(result_aggregator, "finalize_on_commit",
                        lambda session, run_id, payload=None: finalized.append(run_id) or True)
    monkeypatch.setattr(result_aggregator, "_update_flake_scores", lambda *a: None)
    run = SimpleNamespace(id=5, total_tests=2, passed_tests=0, failed_tests=0, created_at=None,
                          status=TestStatus.RUNNING, error_message=None,
                          video_url=None, trace_url=None, har_url=None, duration_ms=None)
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    result_aggregator.update_run_from_progress(
        run, 5, {"total": "2", "completed": "1", "passed": "1", "failed": "0"}, session)
    assert finalized == [] and run.status == TestStatus.RUNNING
    result_aggregator.update_run_from_progress(
        run, 5, {"total": "2", "completed": "2", "passed": "2", "failed": "0"}, session)
    assert finalized == [5] and run.status == TestStatus.PASSED


def test_stale_timeout_finalizes(monkeypatch):
    from app.tasks import result_aggregator
    finalized = []
    monkeypatch.setattr(result_aggregator, "finalize_on_commit",
                        lambda session, run_id, payload=None: finalized.append(run_id) or True)
    run = SimpleNamespace(id=9, total_tests=3, passed_tests=0, failed_tests=0)
    result_aggregator._mark_run_timeout(run, {"total": "3", "completed": "1"}, MagicMock(), reason="x")
    assert finalized == [9]
