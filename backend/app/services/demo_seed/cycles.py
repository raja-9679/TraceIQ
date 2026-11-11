"""Demo: one test cycle for release 2.4.0.

Nothing new is run for it. The cycle gathers what the demo already holds —
the manual release checklist with its two manual runs, the web smoke tests
with their newest finished run, and the approval — so the Test cycles page
shows a release part-way through: most tests passed, the declined-card check
still failing after the re-test, people assigned, and a sign-off that says
why that was accepted.

Rows only; the workspace purge removes them (testcycle / testcycleitem are in
PURGE_PLAN, and the runs it links are the demo's own).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

NAME = "Release 2.4.0 regression"


async def seed(ctx: Any) -> None:
    from app.models import TestCycle, TestCycleItem, TestStatus

    web = ctx.projects["web"]
    manual = ctx.extra.get("manual_cases") or {}
    automated = list((ctx.cases.get("web") or {}).values())
    if not manual and not automated:
        return
    maya = ctx.members.get("maya") or ctx.user
    sam = ctx.members.get("sam") or ctx.user

    approval = ctx.extra.get("manual_approval")
    cycle = TestCycle(
        project_id=web.id, name=NAME, release_label="2.4.0",
        git_commit=getattr(approval, "git_commit", None),
        description="Regression for 2.4.0: the web smoke tests on every browser we support, "
                    "plus the manual release checklist.",
        status="open", start_date=ctx.ago(days=3).date(),
        # A plan, not an event: the one date here that may lie ahead.
        due_date=(ctx.now + timedelta(days=2)).date(),
        created_by_id=ctx.user.id, created_at=ctx.ago(days=3), updated_at=ctx.ago(days=3))
    ctx.session.add(cycle)
    await ctx.session.flush()

    # Who tests what: the payment checks to one tester, the rest of the
    # checklist to another, the automated tests that failed to the owner.
    finished = [run for run in ctx.runs_of("web")
                if run.status in (TestStatus.PASSED, TestStatus.FAILED)]
    newest = finished[-1] if finished else None
    failing = {result.test_case_id for result, run, _ in ctx.result_rows
               if newest is not None and run.id == newest.id and result.status != TestStatus.PASSED}
    who = {"checkout": maya, "decline": maya, "mobile_layout": sam, "screen_reader": sam}
    for key, case in manual.items():
        ctx.session.add(TestCycleItem(
            cycle_id=cycle.id, project_id=web.id, test_case_id=case.id,
            assignee_id=who[key].id if key in who else None, added_by_id=ctx.user.id,
            created_at=ctx.ago(days=3)))
    for case in automated:
        ctx.session.add(TestCycleItem(
            cycle_id=cycle.id, project_id=web.id, test_case_id=case.id,
            assignee_id=ctx.user.id if case.id in failing else None, added_by_id=ctx.user.id,
            created_at=ctx.ago(days=3)))

    for run in (*(ctx.extra.get("manual_runs") or ()), newest):
        if run is not None:
            run.cycle_id = cycle.id
            ctx.session.add(run)
    if approval is not None:
        approval.cycle_id = cycle.id
        ctx.session.add(approval)
    ctx.extra["test_cycle"] = cycle
