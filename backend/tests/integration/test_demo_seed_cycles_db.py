"""Demo workspace — the test cycle (app/services/demo_seed/cycles.py), read
back through the endpoint the Test cycles page calls.

    ./run-tests-live.sh tests/integration/test_demo_seed_cycles_db.py -q
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
    monkeypatch.setattr(demo_seed, "MODULES", ("setup", "releases_reports", "cycles"))


async def test_the_cycle_shows_a_release_part_way_through(session, owner):
    from sqlmodel import select
    from app.api import cycles as api
    from app.models import TestCycle

    await dw.create_demo_workspace(session, owner)
    cycle = (await session.exec(select(TestCycle))).one()
    d = await api._detail(session, AuthPrincipal(user=owner, api_key=None), cycle)

    by_name = {i["name"]: i for i in d["items"]}
    assert d["manual_cases"] == 4 and d["automated_cases"] >= 1
    # The upgrade was re-tested and passes; the declined-card check was not, and still fails.
    assert by_name["Upgrade to Pro with a test card"]["status"] == "passed"
    assert by_name["Declined card keeps the free plan"]["status"] == "failed"
    assert by_name["Declined card keeps the free plan"]["assignee"].name.startswith("Maya")
    # The smoke tests take their state from the run that was linked, not from all history.
    automated = [i for i in d["items"] if i["kind"] == "automated"]
    assert all(i["status"] != "not_run" for i in automated)
    assert len({i["run_id"] for i in automated}) == 1
    assert d["progress"]["executed"] == d["progress"]["total"]
    assert d["signoff"]["verdict"] == "approve"
    assert {r["kind"] for r in d["runs"]} == {"manual", "automated"} and d["runs_total"] == 3
    assert d["status"] == "open" and d["overdue"] is False


async def test_deleting_the_demo_removes_it_all(session, owner):
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    mid = await count_all_tables(session)
    assert mid["testcycle"] - before["testcycle"] == 1
    assert mid["testcycleitem"] - before["testcycleitem"] >= 5
    await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"
