"""Impact-analysis v2: pattern matching + the run/review suggestion heuristic.

Pure unit tests — the DB layer is a queued fake session, no Postgres needed.
Run with:
    cd backend && pytest tests/test_impact_analysis_v2.py -v
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.api.agent_ownership import (
    _BROAD_PREFIX_THRESHOLD,
    _enrich_impacted_case,
    _path_matches,
)
from app.models import (
    FlakeRecord,
    ImpactMatch,
    TestCase,
    TestCaseResult,
    TestRun,
    TestStatus,
)


class _Result:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def all(self):
        return self._value


class FakeAsyncSession:
    """Answers session.exec() calls from a FIFO queue."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def exec(self, _stmt):
        return _Result(self._responses.pop(0))


def _case(**kw) -> TestCase:
    defaults = dict(id=1, name="checkout works", project_id=1, test_suite_id=2,
                    is_ai_authored=False, code_paths=["frontend/src/Checkout/"])
    defaults.update(kw)
    return TestCase(**defaults)


def _last_result_row(status: TestStatus, git_commit="abc1234"):
    res = TestCaseResult(id=10, test_run_id=99, test_name="checkout works",
                         status=status, duration_ms=1200.0)
    run = MagicMock(spec=TestRun)
    run.id = 99
    run.created_at = datetime(2026, 8, 1)
    run.git_commit = git_commit
    return (res, run)


# --- _path_matches -----------------------------------------------------------

@pytest.mark.parametrize("file,pattern,expected", [
    ("backend/app/api/articles.py", "backend/app/api/articles.py", True),
    ("backend/app/api/articles.py", "backend/app/api/", True),
    ("backend/app/api/articles.py", "backend/app/api", True),
    ("backend/app/api_v2/articles.py", "backend/app/api", False),
    ("backend/app/api/deep/nested.py", "backend/app/api/**/*.py", True),
    ("frontend/src/Articles/List.tsx", "frontend/src/Articles/**", True),
    ("frontend/src/Cart/List.tsx", "frontend/src/Articles/**", False),
    ("anything", "", False),
])
def test_path_matches(file, pattern, expected):
    assert _path_matches(file, pattern) is expected


# --- suggested_action heuristic ---------------------------------------------

async def test_clean_case_suggests_run():
    session = FakeAsyncSession([
        _last_result_row(TestStatus.PASSED),  # id-join hit
        None,                                  # no flake record
    ])
    pairs = [ImpactMatch(file="frontend/src/Checkout/Cart.tsx",
                         pattern="frontend/src/Checkout/")]
    out = await _enrich_impacted_case(
        session, _case(), {2: "Checkout"}, hits=["frontend/src/Checkout/Cart.tsx"],
        pairs=pairs)
    assert out.suggested_action == "run"
    assert out.reasons == []
    assert out.suite_name == "Checkout"
    assert out.matched == pairs
    assert out.last_result.status == "passed"
    assert out.last_result.git_commit == "abc1234"


async def test_failed_last_result_suggests_review():
    session = FakeAsyncSession([
        _last_result_row(TestStatus.FAILED),
        None,
    ])
    out = await _enrich_impacted_case(session, _case(), {}, hits=["f"], pairs=[])
    assert out.suggested_action == "review"
    assert any("failed" in r for r in out.reasons)


async def test_quarantined_flake_suggests_review():
    flake = FlakeRecord(id=1, test_case_id=1, step_id=None,
                        flake_score=0.6, is_quarantined=True)
    session = FakeAsyncSession([
        _last_result_row(TestStatus.PASSED),
        flake,
    ])
    out = await _enrich_impacted_case(session, _case(), {}, hits=["f"], pairs=[])
    assert out.suggested_action == "review"
    assert out.flake.is_quarantined is True
    assert any("quarantined" in r for r in out.reasons)


async def test_ai_authored_unreviewed_suggests_review():
    session = FakeAsyncSession([
        _last_result_row(TestStatus.PASSED),
        None,
    ])
    case = _case(is_ai_authored=True, last_human_reviewed_at=None)
    out = await _enrich_impacted_case(session, case, {}, hits=["f"], pairs=[])
    assert out.suggested_action == "review"
    assert any("never human-reviewed" in r for r in out.reasons)


async def test_broad_prefix_suggests_run_then_review():
    n = _BROAD_PREFIX_THRESHOLD
    pairs = [ImpactMatch(file=f"backend/app/file{i}.py", pattern="backend/")
             for i in range(n)]
    session = FakeAsyncSession([
        _last_result_row(TestStatus.PASSED),
        None,
    ])
    out = await _enrich_impacted_case(
        session, _case(code_paths=["backend/"]), {},
        hits=[m.file for m in pairs], pairs=pairs)
    assert out.suggested_action == "run_then_review"
    assert any("too coarse" in r for r in out.reasons)


async def test_glob_patterns_do_not_trigger_broad_prefix():
    n = _BROAD_PREFIX_THRESHOLD
    pairs = [ImpactMatch(file=f"backend/app/file{i}.py", pattern="backend/**/*.py")
             for i in range(n)]
    session = FakeAsyncSession([
        _last_result_row(TestStatus.PASSED),
        None,
    ])
    out = await _enrich_impacted_case(
        session, _case(code_paths=["backend/**/*.py"]), {},
        hits=[m.file for m in pairs], pairs=pairs)
    assert out.suggested_action == "run"


async def test_name_fallback_when_no_id_stamped_rows():
    # First exec (id join) returns nothing; second (name match) hits.
    session = FakeAsyncSession([
        None,
        _last_result_row(TestStatus.PASSED),
        None,
    ])
    out = await _enrich_impacted_case(session, _case(), {}, hits=["f"], pairs=[])
    assert out.last_result is not None
    assert out.last_result.run_id == 99


async def test_no_history_at_all():
    session = FakeAsyncSession([None, None, None])
    case = _case(last_validated_commit="deadbee",
                 last_validated_at=datetime(2026, 7, 1))
    out = await _enrich_impacted_case(session, case, {}, hits=["f"], pairs=[])
    assert out.last_result is None
    assert out.suggested_action == "run"
    assert out.last_validated_commit == "deadbee"
