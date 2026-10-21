"""WP10 frontend quality against a real Postgres.

    TRACEIQ_SCRATCH_DB=traceiq_wp10 ./run-tests-live.sh tests/integration/test_frontend_quality_db.py -q

Matrix fan-out into runs, the sweep view's project boundary, crawl storage +
filters + access control, page-budget persistence/trend, selector health and
Storybook case generation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import frontend_quality as fq                       # noqa: E402
from app.api.endpoints import test_runs                         # noqa: E402
from app.core.auth import AuthPrincipal                         # noqa: E402
from app.core.config import db_url_for, settings                # noqa: E402
from app.models import (                                        # noqa: E402
    ApiKey, PageBudgetSample, ProjectEnvironment, SelectorHealProposal, SiteCrawlIssue,
    SiteCrawlRun, TestCase, TestCaseResult, TestRun, TestStatus, User,
)

from tests.integration.test_targeted_runs_db import (           # noqa: E402,F401
    no_queue, scene, session,
)
from tests.integration.denied import DENIED  # noqa: E402


def me(scene, key=None):
    return AuthPrincipal(user=scene.user, api_key=key)


async def outsider(session) -> AuthPrincipal:
    u = User(email="out@else.example.com", full_name="Out", hashed_password="x", is_verified=True)
    session.add(u)
    await session.commit()
    return AuthPrincipal(user=u, api_key=None)


def sync_session() -> Session:
    return Session(create_engine(db_url_for(settings.DATABASE_URL, sync=True)))


async def _run(session, scene, **ctx):
    return await test_runs.create_run(
        suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=test_runs.RunCreateContext(case_ids=[scene.root_cases[0]], **ctx),
        session=session, principal=me(scene))


# --- matrix fan-out ---------------------------------------------------------------

async def test_viewports_x_locales_fan_out_with_one_sweep_group(session, scene):
    runs = await _run(session, scene, viewports=["mobile", "desktop", "wide"], locales=["en-US", "ar-EG"])
    assert len(runs) == 6
    assert len({r.sweep_group for r in runs}) == 1 and runs[0].sweep_group
    labels = {r.emulation["label"] for r in runs}
    assert "mobile 375x812 · ar-EG" in labels
    widths = sorted({r.emulation["viewport"]["width"] for r in runs})
    assert widths == [375, 1366, 1920]


async def test_suite_setting_axes_apply_when_the_request_names_none(session, scene):
    from app.models import TestSuite
    suite = await session.get(TestSuite, scene.root_id)
    suite.settings = {**(suite.settings or {}), "color_schemes": ["light", "dark"], "reduced_motion": True}
    session.add(suite)
    await session.commit()
    runs = await _run(session, scene)
    assert sorted(r.emulation["color_scheme"] for r in runs) == ["dark", "light"]
    assert all(r.emulation["reduced_motion"] for r in runs)
    # A plain run has no emulation and no group.
    plain = await test_runs.create_run(
        suite_id=scene.sibling_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=None, session=session, principal=me(scene))
    assert all(r.emulation is None and r.sweep_group is None for r in plain)


async def test_invalid_options_and_oversized_matrix_are_400(session, scene):
    for ctx in ({"viewports": ["phablet"]}, {"locales": ["not a locale"]}, {"color_schemes": ["sepia"]}):
        with pytest.raises(HTTPException) as exc:
            await _run(session, scene, **ctx)
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await test_runs.create_run(
            suite_id=scene.root_id, case_id=None, browser=["chromium", "firefox", "webkit"], device=None,
            tags=None, context=test_runs.RunCreateContext(
                case_ids=[scene.root_cases[0]], viewports=["mobile", "tablet", "desktop", "wide"],
                locales=["en-US", "ar-EG", "de-DE", "ja-JP"], color_schemes=["light", "dark", "no-preference"]),
            session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "limit" in exc.value.detail
    # Validation happens before any run is added.
    assert (await session.exec(select(TestRun))).all() == []


async def test_sweep_view_groups_variants_and_stays_inside_the_project(session, scene):
    runs = await _run(session, scene, viewports=["mobile", "desktop"])
    shot = f"runs/{runs[0].id}/screenshots/job-sweep-1-s0.png"
    session.add(TestCaseResult(test_run_id=runs[0].id, test_name="cart", status=TestStatus.PASSED,
                               duration_ms=1, test_case_id=scene.root_cases[0], result_payload={
                                   "step_evidence": [{"index": 0, "type": "goto", "status": "passed",
                                                      "screenshot": shot}]}))
    # A run of ANOTHER project that (somehow) carries the same group id.
    session.add(TestRun(test_suite_id=scene.sibling_id, project_id=scene.other_project_id,
                        sweep_group=runs[0].sweep_group, emulation={"label": "foreign"}))
    await session.commit()
    out = await fq.run_sweep(runs[1].id, session=session, principal=me(scene))
    assert [v["run_id"] for v in out["variants"]] == [r.id for r in runs]
    assert out["variants"][0]["tests"][0]["steps"][0]["screenshot"] == shot
    assert all(v["label"] != "foreign" for v in out["variants"])
    with pytest.raises(HTTPException) as exc:
        await fq.run_sweep(runs[0].id, session=session, principal=await outsider(session))
    assert exc.value.status_code in DENIED


# --- site crawler -------------------------------------------------------------------

async def _with_host(session, scene, host="shop.example.com"):
    session.add(ProjectEnvironment(project_id=scene.project_id, name="prod", base_url=f"https://{host}"))
    await session.commit()


def _crawl_body(**kw):
    return fq.SiteCrawlIn(**{"name": "Shop links", "start_url": "https://shop.example.com/", **kw})


async def test_crawl_needs_a_project_host_and_editor_access(session, scene):
    with pytest.raises(HTTPException) as exc:
        await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "not one of this project's hosts" in exc.value.detail
    await _with_host(session, scene)
    crawl = await fq.create_crawl(scene.project_id, _crawl_body(cron_expression="0 3 * * *"), None,
                                  session=session, principal=me(scene))
    assert crawl["next_run_at"] is not None
    with pytest.raises(HTTPException) as exc:
        await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session,
                              principal=await outsider(session))
    assert exc.value.status_code in DENIED
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await fq.list_crawls(scene.project_id, session=session, principal=me(scene, key))
    assert exc.value.status_code in DENIED
    # Another project's host is not this project's host.
    with pytest.raises(HTTPException):
        await fq.update_crawl(crawl["id"], fq.SiteCrawlPatch(extra_hosts=["evil.example.net"]), None,
                              session=session, principal=me(scene))


async def test_crawl_count_per_project_is_capped(session, scene, monkeypatch):
    await _with_host(session, scene)
    monkeypatch.setattr(fq.site_crawl, "MAX_CRAWLS_PER_PROJECT", 2)
    for _ in range(2):
        await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session, principal=me(scene))
    with pytest.raises(HTTPException) as exc:
        await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session, principal=me(scene))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:     # sub-hourly schedule refused server-side
        await fq.update_crawl((await fq.list_crawls(scene.project_id, session=session,
                                                    principal=me(scene)))[0]["id"],
                              fq.SiteCrawlPatch(cron_expression="*/10 * * * *"), None,
                              session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "hourly" in exc.value.detail


async def test_storybook_index_is_size_capped_while_streaming(session, scene, monkeypatch):
    from app.core.net_guard import ResponseTooLarge
    seen = {}

    async def fake_get(url, **kw):
        seen.update(kw)
        raise ResponseTooLarge("too big")
    monkeypatch.setattr("app.core.net_guard.safe_get", fake_get)
    await _with_host(session, scene, "sb.example.com")
    with pytest.raises(HTTPException) as exc:
        await fq.storybook_stories(scene.project_id, fq.StorybookIndexIn(url="https://sb.example.com/"),
                                   session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "20 MB" in exc.value.detail
    assert seen["max_bytes"] == fq.STORYBOOK_INDEX_MAX_BYTES


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, k):
        return self.data.get(k)

    def delete(self, k):
        self.data.pop(k, None)


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


async def test_crawl_run_results_are_stored_filtered_and_access_checked(session, scene, monkeypatch):
    from app.tasks import frontend_quality_tasks as tasks
    monkeypatch.setattr(tasks.dispatch_site_crawl, "delay", lambda *a, **k: None)
    await _with_host(session, scene)
    crawl = await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session, principal=me(scene))
    run = await fq.run_crawl(crawl["id"], None, session=session, principal=me(scene))
    assert run["status"] == "pending"
    with pytest.raises(HTTPException) as exc:     # one at a time
        await fq.run_crawl(crawl["id"], None, session=session, principal=me(scene))
    assert exc.value.status_code == 409

    report = {"status": "ok", "links_checked": 3,
              "pages": [{"url": "https://shop.example.com/", "depth": 0, "status": 200, "load_ms": 50,
                         "links": 3, "issues": 3}],
              "robots": {"respected": True, "fetched": True, "skipped": 0},
              "issues": [
                  {"page_url": "https://shop.example.com/", "target_url": "https://shop.example.com/missing",
                   "element": "a", "category": "broken", "status_code": 404},
                  {"page_url": "https://shop.example.com/", "target_url": f"https://shop.example.com/r/{JWT}",
                   "element": "img", "category": "slow", "status_code": 200, "duration_ms": 4000},
                  {"page_url": "https://shop.example.com/", "target_url": "http://cdn.example.com/a.js",
                   "element": "script", "category": "mixed_content"},
              ]}
    rc = FakeRedis({f"site_crawl:result:{run['id']}": json.dumps(report)})
    with sync_session() as s:
        assert tasks.collect_once(s, rc, run["id"]) is True
        # Re-delivery does not double the issues.
        rc.data[f"site_crawl:result:{run['id']}"] = json.dumps(report)
        s.get(SiteCrawlRun, run["id"]).status = "running"
        s.commit()
        tasks.collect_once(s, rc, run["id"])
    assert rc.data == {}
    got = await fq.get_crawl_run(run["id"], session=session, principal=me(scene))
    assert got["status"] == "completed" and got["issue_counts"]["broken"] == 1
    assert got["pages"][0]["url"] == "https://shop.example.com/"
    all_issues = await fq.list_crawl_issues(run["id"], category=None, element=None, page_url=None, q=None,
                                            limit=200, offset=0, session=session, principal=me(scene))
    assert all_issues["total"] == 3
    assert not any(JWT in i["target_url"] for i in all_issues["items"])
    broken = await fq.list_crawl_issues(run["id"], category=["broken"], element=None, page_url=None, q=None,
                                        limit=200, offset=0, session=session, principal=me(scene))
    assert [i["target_url"] for i in broken["items"]] == ["https://shop.example.com/missing"]
    by_q = await fq.list_crawl_issues(run["id"], category=None, element=["script"], page_url=None, q="cdn.",
                                      limit=200, offset=0, session=session, principal=me(scene))
    assert by_q["total"] == 1
    wildcard = await fq.list_crawl_issues(run["id"], category=None, element=None, page_url=None, q="%",
                                          limit=200, offset=0, session=session, principal=me(scene))
    assert wildcard["total"] == 0     # '%' is a literal, not a wildcard
    stranger = await outsider(session)
    for call in (fq.get_crawl_run(run["id"], session=session, principal=stranger),
                 fq.list_crawl_issues(run["id"], category=None, element=None, page_url=None, q=None,
                                      limit=10, offset=0, session=session, principal=stranger)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code in DENIED
    # Deleting the crawl removes its runs and issues.
    await fq.delete_crawl(crawl["id"], None, session=session, principal=me(scene))
    assert (await session.exec(select(SiteCrawlIssue))).all() == []


async def test_dispatch_refuses_a_host_removed_since_and_deadline_errors(session, scene, monkeypatch):
    from app.tasks import frontend_quality_tasks as tasks
    monkeypatch.setattr(tasks.dispatch_site_crawl, "delay", lambda *a, **k: None)
    await _with_host(session, scene)
    crawl = await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session, principal=me(scene))
    run = await fq.run_crawl(crawl["id"], None, session=session, principal=me(scene))
    env = (await session.exec(select(ProjectEnvironment))).first()
    await session.delete(env)
    await session.commit()
    assert tasks.dispatch_site_crawl.run(run["id"])["status"] == "refused"
    with sync_session() as s:
        r = s.get(SiteCrawlRun, run["id"])
        assert r.status == "error"
        r.status, r.created_at = "running", datetime.utcnow() - timedelta(hours=2)
        s.commit()
        assert tasks.collect_once(s, FakeRedis(), run["id"]) is True
        assert s.get(SiteCrawlRun, run["id"]).error.startswith("the crawl did not report")


async def test_due_crawls_are_started_by_the_beat_sweep(session, scene, monkeypatch):
    from app.tasks import frontend_quality_tasks as tasks
    started = []
    monkeypatch.setattr(tasks.dispatch_site_crawl, "delay", lambda rid: started.append(rid))
    await _with_host(session, scene)
    crawl = await fq.create_crawl(scene.project_id, _crawl_body(cron_expression="0 * * * *"), None,
                                  session=session, principal=me(scene))
    with sync_session() as s:
        from app.models import SiteCrawl
        c = s.get(SiteCrawl, crawl["id"])
        c.next_run_at = datetime.utcnow() - timedelta(minutes=1)
        s.commit()
    assert tasks.process_due_crawls.run()["started"] == 1
    assert len(started) == 1
    with sync_session() as s:
        from app.models import SiteCrawl
        assert s.get(SiteCrawl, crawl["id"]).next_run_at > datetime.utcnow()


# --- page budgets -----------------------------------------------------------------------

async def test_page_budget_samples_persist_once_and_trend(session, scene):
    from app.services.page_budgets import persist_samples
    runs = await _run(session, scene, viewports=["mobile"])
    sample = {"step_id": "b1", "url": "https://shop.example.com/cart?sid=1", "passed": False,
              "metrics": {"lcp_ms": 3100, "cls": 0.01},
              "checks": [{"metric": "lcp_ms", "value": 3100, "budget": 2500, "passed": False}],
              "seo": [{"check": "title", "passed": True, "severity": "error"}]}
    with sync_session() as s:
        run = s.get(TestRun, runs[0].id)
        persist_samples(s, run, scene.root_cases[0], "cart", [sample])
        persist_samples(s, run, scene.root_cases[0], "cart", [sample])   # re-delivered result
        s.commit()
    rows = (await session.exec(select(PageBudgetSample))).all()
    assert len(rows) == 1 and rows[0].variant == "mobile 375x812" and rows[0].passed is False
    summary = await fq.page_budget_summary(scene.project_id, days=30, session=session, principal=me(scene))
    assert summary[0]["page_key"] == "https://shop.example.com/cart" and summary[0]["failed"] == 1
    trend = await fq.page_budget_trend(scene.project_id, page_key="https://shop.example.com/cart",
                                       variant="mobile 375x812", limit=50, session=session, principal=me(scene))
    assert trend[0]["metrics"]["lcp_ms"] == 3100
    with pytest.raises(HTTPException):
        await fq.page_budget_summary(scene.project_id, days=30, session=session,
                                     principal=await outsider(session))


# --- selector health ----------------------------------------------------------------------

async def test_selector_health_counts_heals_and_failures_for_this_project_only(session, scene):
    case_id = scene.root_cases[0]
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, test_case_id=case_id)
    foreign = TestRun(test_suite_id=scene.root_id, project_id=scene.other_project_id)
    session.add_all([run, foreign])
    await session.flush()
    evidence = {"step_evidence": [{"index": 1, "type": "click", "status": "failed",
                                   "selector": "div > div > button:nth-child(2)", "step_id": "s1",
                                   "dom": '<div><button data-testid="buy" data-traceiq-target="">Buy</button></div>',
                                   "dom_scope": "target"}]}
    session.add_all([
        TestCaseResult(test_run_id=run.id, test_name="cart", status=TestStatus.FAILED, duration_ms=1,
                       test_case_id=case_id, result_payload=evidence),
        TestCaseResult(test_run_id=foreign.id, test_name="x", status=TestStatus.FAILED, duration_ms=1,
                       test_case_id=scene.foreign_case, result_payload=evidence),
        SelectorHealProposal(test_case_id=case_id, step_id="s1", old_selector="div > div > button:nth-child(2)",
                             new_selector='[data-testid="buy"]', confidence=0.75),
    ])
    await session.commit()
    out = await fq.selector_health(scene.project_id, days=90, session=session, principal=me(scene))
    [entry] = out["entries"]
    assert entry["test_case_id"] == case_id and entry["case_name"] == "cart"
    assert entry["heal_count"] == 1 and entry["failure_count"] == 1
    assert entry["suggestions"][0]["locator"] == '[data-testid="buy"]'
    with pytest.raises(HTTPException):
        await fq.selector_health(scene.project_id, days=90, session=session, principal=await outsider(session))


# --- storybook --------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.content = json.dumps(data).encode()

    def json(self):
        return self._data


async def test_storybook_cases_are_generated_and_updated_in_place(session, scene, monkeypatch):
    fetched = []

    async def fake_get(url, **kw):
        fetched.append(url)
        if url.endswith("/index.json"):
            return FakeResp(200, {"v": 5, "entries": {
                "button--primary": {"id": "button--primary", "title": "Button", "name": "Primary", "type": "story"},
                "card--default": {"id": "card--default", "title": "Card", "name": "Default", "type": "story"}}})
        return FakeResp(404, {})
    monkeypatch.setattr("app.core.net_guard.safe_get", fake_get)

    body = fq.StorybookIndexIn(url="https://sb.example.com/")
    with pytest.raises(HTTPException) as exc:     # not one of the project's hosts yet
        await fq.storybook_stories(scene.project_id, body, session=session, principal=me(scene))
    assert exc.value.status_code == 400 and not fetched
    await _with_host(session, scene, "sb.example.com")
    stories = await fq.storybook_stories(scene.project_id, body, session=session, principal=me(scene))
    assert [s["id"] for s in stories["stories"]] == ["button--primary", "card--default"]

    req = fq.StorybookCasesIn(url="https://sb.example.com/", suite_id=scene.sibling_id,
                              story_ids=["button--primary"], a11y=False)
    out = await fq.storybook_cases(scene.project_id, req, None, session=session, principal=me(scene))
    assert len(out["created"]) == 1
    case = await session.get(TestCase, out["created"][0]["id"])
    assert [s["type"] for s in case.steps] == ["goto", "wait-for-selector", "expect-visual-match"]
    again = await fq.storybook_cases(scene.project_id, req.model_copy(update={"a11y": True}), None,
                                     session=session, principal=me(scene))
    assert again["created"] == [] and again["updated"][0]["id"] == case.id
    with pytest.raises(HTTPException) as exc:     # suite of another project
        await fq.storybook_cases(scene.project_id, req.model_copy(update={"suite_id": 999999}), None,
                                 session=session, principal=me(scene))
    assert exc.value.status_code == 400


async def test_scheduled_suite_runs_fan_out_but_monitors_do_not(session, scene):
    from app.models import TestSchedule, TestSuite
    from app.tasks.schedule_tasks import _dispatch_schedule
    suite = await session.get(TestSuite, scene.sibling_id)
    suite.settings = {"viewports": ["mobile", "desktop"]}
    session.add(suite)
    plain = TestSchedule(name="nightly", project_id=scene.project_id, test_suite_id=scene.sibling_id,
                         cron_expression="0 3 * * *", created_by_id=scene.user.id)
    monitor = TestSchedule(name="uptime", project_id=scene.project_id, test_suite_id=scene.sibling_id,
                           cron_expression="*/5 * * * *", is_monitor=True, created_by_id=scene.user.id)
    session.add_all([plain, monitor])
    await session.commit()
    await _dispatch_schedule(plain, session)
    runs = (await session.exec(select(TestRun).where(TestRun.agent_id == f"schedule:{plain.id}"))).all()
    assert sorted(r.emulation["viewport"]["name"] for r in runs) == ["desktop", "mobile"]
    assert len({r.sweep_group for r in runs}) == 1
    await _dispatch_schedule(monitor, session)
    mon = (await session.exec(select(TestRun).where(TestRun.agent_id == f"schedule:{monitor.id}"))).all()
    assert len(mon) == 1 and mon[0].emulation is None


async def test_active_crawl_runs_per_project_are_capped(session, scene, monkeypatch):
    # Each run can hold a shared worker for 15 minutes; a project may not
    # start all its crawls at once.
    from app.tasks import frontend_quality_tasks as tasks
    monkeypatch.setattr(tasks.dispatch_site_crawl, "delay", lambda *a, **k: None)
    monkeypatch.setattr(fq.site_crawl, "MAX_ACTIVE_RUNS_PER_PROJECT", 2)
    await _with_host(session, scene)
    crawls = [await fq.create_crawl(scene.project_id, _crawl_body(), None, session=session,
                                    principal=me(scene)) for _ in range(3)]
    for c in crawls[:2]:
        await fq.run_crawl(c["id"], None, session=session, principal=me(scene))
    with pytest.raises(HTTPException) as exc:
        await fq.run_crawl(crawls[2]["id"], None, session=session, principal=me(scene))
    assert exc.value.status_code == 429
