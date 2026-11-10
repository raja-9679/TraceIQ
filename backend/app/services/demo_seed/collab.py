"""Demo: a failure being worked on together.

The newest failed result of the web project gets what the run page shows
above a failure — an assignment to the demo's owner, a short thread — and the
owner's bell gets the two notifications that go with them, so the feature is
visible in a fresh demo instead of an empty "Nobody yet".

Rows only, like every demo dataset; the workspace purge removes them
(resultcomment / resultassignment / notification are in PURGE_PLAN).
"""
from __future__ import annotations

from typing import Any

THREAD = (
    ("maya", 26, "This one started failing after Tuesday's deploy. The list does not refresh after Add."),
    ("sam", 25, "Same on Firefox. I can reproduce it by adding two todos quickly."),
)


async def seed(ctx: Any) -> None:
    from app.models import Notification, ResultAssignment, ResultComment, TestStatus

    web = ctx.projects["web"]
    failed = [(result, run) for result, run, _ in ctx.result_rows
              if run.project_id == web.id and result.status == TestStatus.FAILED
              and result.test_case_id is not None and run.created_at <= ctx.now]
    if not failed:
        return
    result, run = max(failed, key=lambda pair: (pair[1].created_at, pair[0].id))

    # Members come from the setup module; on its own (a narrowed test) the
    # owner plays every part.
    maya = ctx.members.get("maya") or ctx.user
    people = {"maya": maya, "sam": ctx.members.get("sam") or ctx.user}

    ctx.session.add(ResultAssignment(
        project_id=web.id, test_run_id=run.id, result_id=result.id, test_case_id=result.test_case_id,
        assignee_id=ctx.user.id, assigned_by_id=maya.id, status="open",
        created_at=ctx.ago(hours=27), updated_at=ctx.ago(hours=27)))
    for who, hours, body in THREAD:
        ctx.session.add(ResultComment(
            project_id=web.id, test_run_id=run.id, result_id=result.id,
            author_id=people[who].id, body=body, created_at=ctx.ago(hours=hours)))
    # What the owner's bell holds: asked to look, then a reply in the thread.
    if maya.id != ctx.user.id:
        ctx.session.add(Notification(user_id=ctx.user.id, project_id=web.id, kind="assigned",
                                     actor_id=maya.id, run_id=run.id, result_id=result.id,
                                     created_at=ctx.ago(hours=27)))
        ctx.session.add(Notification(user_id=ctx.user.id, project_id=web.id, kind="comment",
                                     actor_id=people["sam"].id, run_id=run.id, result_id=result.id,
                                     created_at=ctx.ago(hours=25)))
    ctx.extra["discussed_result"] = (run.id, result.id)
