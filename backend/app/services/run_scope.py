"""Which runs count (WP16c, decision V4).

Validation runs execute a test with a PROPOSED change applied to that run
only. What ran is not the stored test, so a validation run must never move a
statistic: pass rates, trends, the quality gate, release readiness, flake
scores and quarantine, last-validated commits, DORA, digests and summaries,
PoC reports, requirement/journey/flag coverage, selector health, failure
history and impact analysis all filter through here.

    query.where(counted())                      # TestRun in the FROM clause
    query.where(counted_result())               # TestCaseResult without a TestRun join
    f"... AND {COUNTED_SQL.format(alias='t')}"  # raw SQL over testrun t

Deliberately NOT filtered: run lists and run pages (people need to find a
validation run), cost/browser minutes and plan quota (a validation run is real
spend), operational metrics (queue depth, running counts) and the
concurrency cap — and the exclusive-environment lock, which a validation run
honours like any other.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import false, select, true

COUNTED_SQL = "NOT {alias}.is_validation"


def counted(model: Any = None):
    """SQL predicate: the run is not a validation run."""
    if model is None:
        from app.models import TestRun as model
    return model.is_validation == false()


def counted_result():
    """For queries over TestCaseResult that do not join TestRun. NOT IN the
    (few, indexed) validation runs rather than IN every other run."""
    from app.models import TestCaseResult, TestRun
    return TestCaseResult.test_run_id.not_in(
        select(TestRun.id).where(TestRun.is_validation == true()))


def is_counted(run: Any) -> bool:
    # `is True`: test doubles (MagicMock) answer every attribute truthily.
    return getattr(run, "is_validation", False) is not True
