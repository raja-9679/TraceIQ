"""Demo seed, Tests area (app/services/demo_seed/tests_quality.py) against a
real Postgres: each dataset is read back through the endpoint its page calls,
demo schedules never dispatch, and a demo delete removes all of it.

    TRACEIQ_SCRATCH_DB=traceiq_gseed_tq ./run-tests-live.sh tests/integration/test_demo_seed_tests_db.py -q
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                              # noqa: E402
from app.models import (                                             # noqa: E402
    AuditLog, FailureCluster, ProjectEnvironment, TestCase, TestRun, TestSchedule, TestSuite,
)
from app.services import demo_seed                                   # noqa: E402
from app.services import demo_workspace as dw                        # noqa: E402
from tests.integration.demo_seed_fixtures import (                   # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, owner, session,
)


@pytest_asyncio.fixture(loop_scope="function")
async def demo(session, owner, monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("tests_quality",))
    out = await dw.create_demo_workspace(session, owner)
    return SimpleNamespace(ws=out["workspace_id"], web=out["projects"][0]["id"],
                           api=out["projects"][1]["id"], user=owner,
                           principal=AuthPrincipal(user=owner, api_key=None))


async def test_ownership_and_exclusive_environment(session, demo):
    cases = (await session.exec(select(TestCase).where(TestCase.project_id == demo.web))).all()
    owned = [c for c in cases if c.owner_user_id]
    assert len(owned) >= 4 and any(c.last_reviewed_at for c in owned)
    assert any(c.last_reviewed_at is None for c in owned)          # an unreviewed one too
    assert all(c.last_reviewed_at <= datetime.utcnow() for c in owned if c.last_reviewed_at)
    envs = (await session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == demo.web, ProjectEnvironment.exclusive == True))).all()  # noqa: E712
    assert [e.name for e in envs] == ["shared-staging"]


async def test_nightly_schedule_is_active_and_owns_the_nightly_runs(session, demo):
    sched = (await session.exec(select(TestSchedule).where(TestSchedule.project_id == demo.web))).one()
    assert sched.is_active and not sched.is_monitor and sched.cron_expression == "0 2 * * *"
    assert sched.next_run_at > datetime.utcnow() and sched.last_run_at <= datetime.utcnow()
    tagged = (await session.exec(select(TestRun).where(TestRun.agent_id == f"schedule:{sched.id}"))).all()
    assert len(tagged) >= 29 and all(r.project_id == demo.web for r in tagged)


async def test_beat_never_dispatches_a_demo_schedule(session, demo, monkeypatch):
    from app.tasks import schedule_tasks
    sched = (await session.exec(select(TestSchedule).where(TestSchedule.project_id == demo.web))).one()
    sched.next_run_at = datetime(2000, 1, 1)
    session.add(sched)
    await session.commit()
    dispatched = []

    async def _dispatch(schedule, s):
        dispatched.append(schedule.id)
    monkeypatch.setattr(schedule_tasks, "_dispatch_schedule", _dispatch)

    @asynccontextmanager
    async def _ctx():
        yield session
    monkeypatch.setattr(schedule_tasks, "get_session_context", _ctx)
    before_last = sched.last_run_at
    await schedule_tasks._process_schedules_async()
    await session.refresh(sched)
    assert dispatched == []
    assert sched.next_run_at > datetime.utcnow()                # moved on, not re-selected
    assert sched.last_run_at == before_last                      # but never "ran"


async def test_nested_suite_and_its_audit_history(session, demo):
    from app.api.endpoints.test_runs import get_audit_log
    child = (await session.exec(select(TestSuite).where(TestSuite.project_id == demo.web,
                                                        TestSuite.parent_id.is_not(None)))).one()
    kids = (await session.exec(select(TestCase).where(TestCase.test_suite_id == child.id))).all()
    assert len(kids) == 2
    from app.services.test_packs import catalogue_step_types
    assert {s["type"] for c in kids for s in c.steps} <= catalogue_step_types()
    rows = await get_audit_log("suite", child.id, session=session, current_user=demo.user)
    assert {(r.entity_type, r.action) for r in rows} >= {("suite", "create"), ("suite", "update"),
                                                          ("case", "create")}
    ws_rows = (await session.exec(select(AuditLog).where(AuditLog.workspace_id == demo.ws))).all()
    assert len([r for r in ws_rows if r.entity_type in ("suite", "case")]) == 5


async def test_a_cluster_resolved_inside_the_window_gives_mttr(session, demo):
    from app.api.analytics import effectiveness_summary
    resolved = (await session.exec(select(FailureCluster).where(
        FailureCluster.project_id == demo.api, FailureCluster.status == "resolved"))).all()
    assert len(resolved) == 1 and resolved[0].resolved_at > resolved[0].last_seen_at
    assert resolved[0].resolved_at <= datetime.utcnow()
    summary = await effectiveness_summary(demo.api, days=30, session=session, current_user=demo.user)
    assert summary["mttr_hours"] and summary["mttr_hours"] > 0


async def test_site_crawl_runs_and_issues(session, demo):
    from app.api import frontend_quality as fq
    crawls = await fq.list_crawls(demo.web, session=session, principal=demo.principal)
    assert len(crawls) == 1
    crawl_id = crawls[0]["id"] if isinstance(crawls[0], dict) else crawls[0].id
    runs = await fq.list_crawl_runs(crawl_id, limit=30, session=session, principal=demo.principal)
    assert len(runs) == 2 and all(r["status"] == "completed" for r in runs)
    issues = await fq.list_crawl_issues(runs[0]["id"], category=None, element=None, page_url=None,
                                        q=None, limit=200, offset=0, session=session,
                                        principal=demo.principal)
    cats = {i["category"] for i in issues["items"]}
    assert {"broken", "mixed_content", "redirect_chain", "slow"} <= cats
    older = await fq.list_crawl_issues(runs[1]["id"], category=None, element=None, page_url=None,
                                       q=None, limit=200, offset=0, session=session,
                                       principal=demo.principal)
    assert older["total"] == issues["total"] + 1                  # one broken image since fixed


async def test_page_budget_trend(session, demo):
    from app.api import frontend_quality as fq
    pages = await fq.page_budget_summary(demo.web, days=30, session=session, principal=demo.principal)
    assert len(pages) == 1 and pages[0]["page_key"] == "https://example.com/"
    assert pages[0]["samples"] >= 50 and 0 < pages[0]["failed"] < pages[0]["samples"]
    trend = await fq.page_budget_trend(demo.web, page_key="https://example.com/", variant=None,
                                       limit=500, session=session, principal=demo.principal)
    assert len(trend) == pages[0]["samples"] and trend[-1]["passed"] is True
    home = (await session.exec(select(TestCase).where(TestCase.project_id == demo.web,
                                                      TestCase.name == "Homepage loads"))).one()
    budget_steps = [s for s in home.steps if s["type"] == "check-page-budget"]
    assert len(budget_steps) == 1 and budget_steps[0]["params"]["fail"] is False
    assert {t["step_id"] for t in trend} == {budget_steps[0]["id"]}


async def test_accessibility_register_and_waiver(session, demo):
    from app.api import accessibility as api
    trend = await api.accessibility_trend(demo.web, days=30, principal=demo.principal, session=session)
    assert trend["checks"] >= 50 and trend["series"]
    rules = {r["rule_id"]: r for r in trend["rules"]}
    assert "region" in rules and rules["region"]["waived"] >= 1   # covered by the waiver
    assert sum(d["fixed"] for d in trend["series"]) >= 1          # landmark-one-main got fixed
    waivers = await api.list_waivers(demo.web, principal=demo.principal, session=session)
    assert len(waivers) == 1


async def test_nothing_in_the_future_and_delete_is_clean(session, owner, monkeypatch):
    from app.core import storage
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    monkeypatch.setattr(demo_seed, "MODULES", ("tests_quality",))
    owner_id = owner.id
    before = await count_all_tables(session)
    started = datetime.utcnow()
    out = await dw.create_demo_workspace(session, owner)
    from sqlalchemy import text
    late = started + timedelta(seconds=30)
    for table, col in (("sitecrawlrun", "completed_at"), ("sitecrawl", "last_run_at"),
                       ("pagebudgetsample", "created_at"), ("accessibilitycheck", "created_at"),
                       ("failurecluster", "resolved_at"), ("testcase", "last_reviewed_at")):
        n = (await session.exec(text(
            f"SELECT count(*) FROM {table} t JOIN project p ON p.id = t.project_id "
            f"WHERE p.workspace_id = :w AND t.{col} > :late"),
            params={"w": out["workspace_id"], "late": late})).one()[0]
        assert n == 0, f"{table}.{col} in the future"
    assert await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, leftovers
