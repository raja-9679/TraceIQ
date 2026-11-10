"""Demo workspace — a failure being worked on together
(app/services/demo_seed/collab.py), read back through the endpoints the run
page and the bell call.

    ./run-tests-live.sh tests/integration/test_demo_seed_collab_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                              # noqa: E402
from app.services import demo_seed                                   # noqa: E402
from app.services import demo_workspace as dw                        # noqa: E402
from tests.integration.demo_seed_fixtures import (                   # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, owner, session,
)


@pytest.fixture(autouse=True)
def only_these(monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("setup", "collab"))


def _p(user):
    return AuthPrincipal(user=user, api_key=None)


async def test_a_failed_result_is_assigned_discussed_and_in_the_owners_bell(session, owner):
    from sqlmodel import select
    from app.api import collaboration as collab
    from app.models import ResultAssignment, TestCaseResult, TestStatus

    await dw.create_demo_workspace(session, owner)
    row = (await session.exec(select(ResultAssignment))).one()
    result = await session.get(TestCaseResult, row.result_id)
    assert result.status == TestStatus.FAILED and result.test_run_id == row.test_run_id

    d = await collab.get_discussion(row.test_run_id, row.result_id, principal=_p(owner), session=session)
    assert d.assignment.assignee.id == owner.id and d.assignment.status == "open" and not d.assignment.carried
    assert d.assignment.assigned_by.name.startswith("Maya")
    assert [c.author.name.split()[0] for c in d.comments] == ["Maya", "Sam"]
    assert d.can_write is True

    bell = await collab.list_notifications(limit=30, principal=_p(owner), session=session)
    assert bell.unread == 2
    assert sorted(i.kind for i in bell.items) == ["assigned", "comment"]
    assert {i.test_name for i in bell.items} == {result.test_name}
    # Nothing is dated in the future.
    from datetime import datetime
    assert all(i.created_at <= datetime.utcnow() for i in bell.items)

    mine = await collab.my_assignments(status="open", principal=_p(owner), session=session)
    assert [(m.run_id, m.result_id) for m in mine] == [(row.test_run_id, row.result_id)]


async def test_deleting_the_demo_removes_it_all(session, owner):
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    mid = await count_all_tables(session)
    assert mid["resultcomment"] - before["resultcomment"] == 2
    assert mid["resultassignment"] - before["resultassignment"] == 1
    assert mid["notification"] - before["notification"] == 2
    await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"
