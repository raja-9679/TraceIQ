"""Result↔case linking + last_validated stamping in the result aggregator.

Verifies _update_flake_scores' new post-processing duties:
  - stamps TestCaseResult.test_case_id when it can resolve the case,
  - stamps TestCase.last_validated_commit/_at only on PASSED + run.git_commit,
  - leaves failed results / git-less runs unstamped.

No database: a queued fake session answers exec()/get(), mirroring the style
of test_stale_run_detection.py. Run with:
    cd backend && pytest tests/test_result_case_link.py -v
"""
from unittest.mock import MagicMock

from app.models import TestCase, TestCaseResult, TestStatus
from app.tasks.result_aggregator import _update_flake_scores


class _Result:
    def __init__(self, value):
        self._value = value

    def first(self):
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def all(self):
        if isinstance(self._value, list):
            return self._value
        return [self._value] if self._value is not None else []


class FakeSession:
    """Answers exec() from a FIFO queue; records add() calls; get() from a map."""

    def __init__(self, exec_responses, get_map=None):
        self._responses = list(exec_responses)
        self._get_map = get_map or {}
        self.added = []

    def exec(self, _stmt):
        return _Result(self._responses.pop(0))

    def get(self, model, pk):
        return self._get_map.get((model, pk))

    def add(self, obj):
        self.added.append(obj)


def _run(run_id=99, test_case_id=None, git_commit=None, project_id=1,
         test_suite_id=2):
    run = MagicMock()
    run.id = run_id
    run.test_case_id = test_case_id
    run.git_commit = git_commit
    run.project_id = project_id
    run.test_suite_id = test_suite_id
    return run


def _case(case_id=5):
    return TestCase(id=case_id, name="checkout works", project_id=1,
                    test_suite_id=2)


def _result(status, test_case_id=None):
    return TestCaseResult(id=10, test_run_id=99, test_name="checkout works",
                          status=status, duration_ms=100.0,
                          test_case_id=test_case_id)


def test_pass_with_git_commit_stamps_validation_and_link():
    case = _case()
    res = _result(TestStatus.PASSED)
    run = _run(test_case_id=5, git_commit="abc1234")
    session = FakeSession(
        exec_responses=[
            [res],    # results of the run
            [res],    # history window for flake scoring
            None,     # no existing FlakeRecord
        ],
        get_map={(TestCase, 5): case},
    )

    _update_flake_scores(run, 99, session)

    assert res.test_case_id == 5
    assert case.last_validated_commit == "abc1234"
    assert case.last_validated_at is not None
    assert res in session.added
    assert case in session.added


def test_failed_result_does_not_stamp_validation():
    case = _case()
    res = _result(TestStatus.FAILED)
    run = _run(test_case_id=5, git_commit="abc1234")
    session = FakeSession(
        exec_responses=[[res], [res], None],
        get_map={(TestCase, 5): case},
    )

    _update_flake_scores(run, 99, session)

    assert res.test_case_id == 5          # link still stamped
    assert case.last_validated_commit is None


def test_pass_without_git_commit_does_not_stamp_validation():
    case = _case()
    res = _result(TestStatus.PASSED)
    run = _run(test_case_id=5, git_commit=None)
    session = FakeSession(
        exec_responses=[[res], [res], None],
        get_map={(TestCase, 5): case},
    )

    _update_flake_scores(run, 99, session)

    assert res.test_case_id == 5
    assert case.last_validated_commit is None


def test_result_with_preset_id_resolves_directly():
    """A worker-stamped test_case_id wins over the run-level fallback."""
    case = _case(case_id=7)
    res = _result(TestStatus.PASSED, test_case_id=7)
    run = _run(test_case_id=None, git_commit="fff0000")
    session = FakeSession(
        exec_responses=[[res], [res], None],
        get_map={(TestCase, 7): case},
    )

    _update_flake_scores(run, 99, session)

    assert case.last_validated_commit == "fff0000"


def test_suite_run_resolves_by_name_and_stamps_link():
    """No ids anywhere: falls back to name-in-suite lookup, then stamps."""
    case = _case()
    res = _result(TestStatus.PASSED)
    run = _run(test_case_id=None, git_commit="1a2b3c4")
    session = FakeSession(
        exec_responses=[
            [res],    # results of the run
            [case],   # name + suite lookup
            [res],    # history window
            None,     # no FlakeRecord
        ],
    )

    _update_flake_scores(run, 99, session)

    assert res.test_case_id == 5
    assert case.last_validated_commit == "1a2b3c4"


def test_unresolvable_result_is_left_alone():
    res = _result(TestStatus.PASSED)
    run = _run(test_case_id=None, git_commit="abc")
    session = FakeSession(
        exec_responses=[
            [res],   # results of the run
            None,    # name + suite lookup misses
            None,    # name + project lookup misses
        ],
    )

    _update_flake_scores(run, 99, session)

    assert res.test_case_id is None
    assert session.added == []
