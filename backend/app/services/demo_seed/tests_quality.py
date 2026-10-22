"""Demo datasets for the Tests area: ownership, a nightly schedule, a nested
suite with its audit history, a site crawl, page budgets, the accessibility
register, an exclusive environment and a triaged-and-resolved failure group.

Rows are shaped by the same normalisers the ingestion paths use
(`site_crawl.normalise_result`, `page_budgets.normalise_samples`,
`accessibility.normalise_results`), fed with reports worded the way the worker
words them, so the pages read them exactly as they would read real ones.

Skipped on purpose: HAR archives and mobile app builds. Both rows are only
pointers to an object-store file (download/replay and install presign
`object_key`/`file_key`), and the demo writes no objects — a row whose file
404s is worse than an empty state.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List

NIGHTLY_CRON = "0 2 * * *"
LANDING = "https://example.com/"
_AXE_HELP = "https://dequeuniversity.com/rules/axe/4.8/{}"


def _people(ctx) -> List[Any]:
    """Owner first, then the setup module's fake members (if it ran)."""
    members = [m for _, m in sorted(ctx.members.items()) if getattr(m, "id", None)]
    return [ctx.user] + members


def _step_id(rng) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


async def seed(ctx) -> None:
    rng = ctx.rng_for("tests_quality")
    _ownership(ctx)
    await _nightly_schedule(ctx)
    await _nested_suite(ctx, rng)
    _exclusive_environment(ctx)
    _resolved_cluster(ctx)
    await _site_crawls(ctx)
    _page_budgets(ctx, rng)
    _accessibility(ctx)


# --- ownership --------------------------------------------------------------

def _ownership(ctx) -> None:
    """Owners on most cases, a backup on the critical ones, reviews of
    different ages — and the a11y case left unreviewed so that gap shows too."""
    people = _people(ctx)

    def person(i: int) -> Any:
        return people[i % len(people)]

    plan = {  # web case key -> (owner idx, backup idx or None, reviewed days ago or None)
        "home": (0, 1, 6), "login": (1, 0, 12), "add_todo": (2, 1, 27), "a11y": (0, None, None),
    }
    for key, (owner, backup, reviewed) in plan.items():
        case = ctx.cases["web"][key]
        case.owner_user_id = person(owner).id
        if backup is not None and person(backup).id != case.owner_user_id:
            case.backup_owner_user_id = person(backup).id
        if reviewed is not None:
            case.last_reviewed_at = ctx.ago(days=reviewed, hours=3)
            case.last_reviewed_by_id = case.owner_user_id
        ctx.session.add(case)
    for key in ("json", "echo", "auth"):
        case = ctx.cases["api"][key]
        case.owner_user_id = people[-1].id
        case.last_reviewed_at = ctx.ago(days=9)
        case.last_reviewed_by_id = people[-1].id
        ctx.session.add(case)


# --- nightly schedule -------------------------------------------------------

async def _nightly_schedule(ctx) -> None:
    """An ACTIVE nightly suite schedule on the web project. Safe because
    schedule_tasks never dispatches a demo-workspace schedule (it only moves
    next_run_at on)."""
    from app.models import RunTrigger, TestSchedule
    from app.services.schedule_time import next_run_utc

    suite = ctx.suites["web_smoke"]
    nightly = [run for run in ctx.runs_of("web") if run.triggered_by == RunTrigger.SCHEDULE]
    sched = TestSchedule(
        name="Nightly web smoke (demo)",
        description="Runs the web smoke suite every night at 02:00 UTC. DEMO: schedules in a "
                    "demo workspace never dispatch.",
        project_id=ctx.projects["web"].id, test_suite_id=suite.id, cron_expression=NIGHTLY_CRON,
        timezone="UTC", is_active=True, next_run_at=next_run_utc(NIGHTLY_CRON, "UTC", after=ctx.now),
        last_run_at=nightly[-1].created_at if nightly else None,
        created_at=suite.created_at, updated_at=suite.created_at,
        created_by_id=ctx.user.id, updated_by_id=ctx.user.id)
    ctx.session.add(sched)
    await ctx.session.flush()
    # The history's nightly runs become this schedule's runs, tagged the way
    # schedule_tasks tags what it dispatches.
    for run in nightly:
        run.agent_id = f"schedule:{sched.id}"
        ctx.session.add(run)
    ctx.extra["nightly_schedule"] = sched


# --- nested suite + suite audit history ------------------------------------

async def _nested_suite(ctx, rng) -> None:
    from app.models import ExecutionMode, TestCase, TestSuite
    from app.services.audit import record

    parent = ctx.suites["web_smoke"]
    project = ctx.projects["web"]
    created = ctx.ago(days=18, hours=5)

    def s(t: str, **kw: Any) -> Dict[str, Any]:
        return {"id": _step_id(rng), "type": t, **kw}

    def login() -> List[Dict[str, Any]]:
        return [s("goto", value="{{env.BASE_URL}}/login"),
                s("fill", selector="#login-username", value="alice"),
                s("fill", selector="#login-password", value="{{secret.ALICE_PASSWORD}}"),
                s("click", selector="#login-submit")]

    child = TestSuite(name="Todo editing (demo)", parent_id=parent.id, project_id=project.id,
                      description="Sub-module: completing and renaming todos once signed in.",
                      execution_mode=ExecutionMode.SEPARATE, created_by_id=ctx.user.id,
                      updated_by_id=ctx.user.id, created_at=created, updated_at=ctx.ago(days=4))
    ctx.session.add(child)
    await ctx.session.flush()
    specs = [
        ("Complete a todo", "Ticks the first todo and sees it marked done.", ["regression"], "medium",
         login() + [s("click", selector="#todo-list li:first-child input[type=checkbox]"),
                    s("expect-visible", selector="#todo-list li.completed")]),
        ("Add a draft todo", "A new todo shows its text in the list.", ["regression"], "low",
         login() + [s("fill", selector="#todo-input", value="Draft {{unique.todo}}"),
                    s("click", selector="#todo-create-button"),
                    s("expect-text", selector="#todo-list", value="Draft")]),
    ]
    new_cases = []
    for name, desc, tags, prio, steps in specs:
        case = TestCase(name=name, description=desc, steps=steps, tags=tags, priority=prio,
                        test_suite_id=child.id, project_id=project.id, created_by_id=ctx.user.id,
                        updated_by_id=ctx.user.id, created_at=created, updated_at=created)
        ctx.session.add(case)
        new_cases.append(case)
    await ctx.session.flush()
    ctx.extra["nested_suite"] = child

    # Audit rows as the suite/case endpoints write them (entity 'suite' /
    # 'case' — what SuiteDetails' history tab reads). They are dated now: the
    # audit chain stamps its own time, and they say they came from the seed.
    entries = [
        ("suite", child.id, "create", {"name": child.name, "parent_id": parent.id,
                                       "execution_mode": "separate", "source": "demo seed"}),
        ("case", new_cases[0].id, "create", {"name": new_cases[0].name, "source": "demo seed"}),
        ("case", new_cases[1].id, "create", {"name": new_cases[1].name, "source": "demo seed"}),
        ("suite", parent.id, "update", {"description": {
            "old": "UI journeys: landing page and sign-in.", "new": parent.description}}),
        ("suite", child.id, "update", {"description": {
            "old": "Editing todos.", "new": child.description}}),
    ]
    for entity_type, entity_id, action, changes in entries:
        await record(ctx.session, entity_type=entity_type, entity_id=entity_id, action=action,
                     user_id=ctx.user.id, workspace_id=ctx.ws.id, changes=changes)
        await ctx.session.flush()      # each row chains on the one before


# --- environment, triage ----------------------------------------------------

def _exclusive_environment(ctx) -> None:
    from app.models import ProjectEnvironment
    ctx.session.add(ProjectEnvironment(
        project_id=ctx.projects["web"].id, name="shared-staging", base_url="https://example.com",
        variables={"BASE_URL": "https://example.com"}, is_default=False, exclusive=True,
        created_at=ctx.ago(days=20), updated_at=ctx.ago(days=20)))


def _resolved_cluster(ctx) -> None:
    """One failure group triaged and resolved inside the window, so MTTR has
    a value. The scripted httpbin timeout ended a week ago — use it when its
    last hit is old enough, else the group whose last hit is oldest."""
    candidates = [c for c in ctx.clusters.values() if c.last_seen_at <= ctx.ago(days=3)]
    if not candidates:
        return
    cluster = ctx.clusters.get("delay_timeout")
    if cluster not in candidates:
        cluster = min(candidates, key=lambda c: c.last_seen_at)
    cluster.status = "resolved"
    cluster.assignee_id = _people(ctx)[-1].id
    cluster.first_triaged_at = ctx.past(cluster.first_seen_at + timedelta(hours=3))
    cluster.resolved_at = ctx.past(cluster.last_seen_at + timedelta(hours=26))
    cluster.resolution_note = ("Slow responses from the upstream edge; raised the client timeout "
                               "budget and pinned the region. (DEMO note)")
    ctx.session.add(cluster)


# --- site crawl -------------------------------------------------------------

def _crawl_report(fixed: bool) -> Dict[str, Any]:
    """A worker report (execution-engine core/frontend-quality/crawler.ts shape)."""
    pages = [
        {"url": LANDING, "depth": 0, "status": 200, "load_ms": 420, "links": 6, "issues": 2},
        {"url": "https://example.com/about", "depth": 1, "status": 200, "load_ms": 380, "links": 4,
         "issues": 1},
        {"url": "https://example.com/pricing", "depth": 1, "status": 200, "load_ms": 3650, "links": 5,
         "issues": 1},
        {"url": "https://example.com/blog", "depth": 1, "status": 200, "load_ms": 510, "links": 9,
         "issues": 0},
        {"url": "https://example.com/blog/release-notes", "depth": 2, "status": 200, "load_ms": 460,
         "links": 3, "issues": 0 if fixed else 1},
        {"url": "https://example.com/contact", "depth": 1, "status": 200, "load_ms": 300, "links": 2,
         "issues": 0},
    ]
    issues = [
        {"page_url": LANDING, "target_url": "https://example.com/careers", "element": "a",
         "category": "broken", "status_code": 404, "duration_ms": 140, "detail": "HTTP 404"},
        {"page_url": LANDING, "target_url": "http://example.com/static/legacy.css", "element": "css",
         "category": "mixed_content", "status_code": 200, "duration_ms": 90,
         "detail": "http:// stylesheet on an https page"},
        {"page_url": "https://example.com/about", "target_url": "https://example.com/team",
         "element": "a", "category": "redirect_chain", "status_code": 200, "duration_ms": 260,
         "redirect_chain": [{"url": "https://example.com/team", "status": 301},
                            {"url": "https://example.com/about/team", "status": 302},
                            {"url": "https://example.com/about/people", "status": 200}],
         "detail": "2 redirects"},
        {"page_url": "https://example.com/pricing", "target_url": "https://example.com/pricing",
         "element": "page", "category": "slow", "status_code": 200, "duration_ms": 3650,
         "detail": "load took 3650 ms (threshold 3000 ms)"},
        {"page_url": "https://example.com/contact", "target_url": "https://maps.example.net/embed",
         "element": "a", "category": "not_checked", "detail": "external host not checked"},
    ]
    if not fixed:
        issues.append({"page_url": "https://example.com/blog/release-notes",
                       "target_url": "https://example.com/img/diagram.png", "element": "img",
                       "category": "broken", "status_code": 404, "duration_ms": 120,
                       "detail": "HTTP 404"})
    return {"status": "ok", "truncated": False, "links_checked": 31 if fixed else 33,
            "pages": pages, "robots": {"respected": True, "fetched": True, "skipped": 1,
                                       "crawl_delay_ms": None},
            "issues": issues}


async def _site_crawls(ctx) -> None:
    from app.models import SiteCrawl, SiteCrawlIssue, SiteCrawlRun
    from app.services.site_crawl import normalise_result, snapshot

    last_done = ctx.ago(days=1) + timedelta(minutes=4)
    # No cron: a manual crawl, so the crawl beat never picks it up.
    crawl = SiteCrawl(project_id=ctx.projects["web"].id, name="example.com link check (demo)",
                      start_url=LANDING, max_depth=2, max_pages=50, check_external=True,
                      created_by_id=ctx.user.id, created_at=ctx.ago(days=12),
                      updated_at=ctx.ago(days=12), last_run_at=last_done)
    ctx.session.add(crawl)
    await ctx.session.flush()
    for days_ago, fixed in ((8, False), (1, True)):
        started = ctx.ago(days=days_ago)
        fields, issues = normalise_result(_crawl_report(fixed))
        run = SiteCrawlRun(crawl_id=crawl.id, project_id=crawl.project_id, trigger="manual",
                           config=snapshot(crawl), requested_by_id=ctx.user.id, created_at=started,
                           completed_at=started + timedelta(minutes=4), **fields)
        ctx.session.add(run)
        await ctx.session.flush()
        for item in issues:
            ctx.session.add(SiteCrawlIssue(crawl_run_id=run.id, **item))


# --- page budgets -----------------------------------------------------------

def _page_budgets(ctx, rng) -> None:
    """`check-page-budget` on the landing-page case (record-only, so it never
    fails the case), with one sample per run of it: a mostly-green trend and a
    heavy-hero-image regression that was fixed."""
    from app.models import PageBudgetSample
    from app.services.page_budgets import normalise_samples

    case = ctx.cases["web"]["home"]
    budgets = {"lcp_ms": 2500, "cls": 0.1, "page_weight_kb": 1500, "request_count": 80}
    step = {"id": _step_id(rng), "type": "check-page-budget",
            "params": {"budgets": budgets, "seo": "warn", "fail": False}}
    case.steps = list(case.steps or []) + [step]
    ctx.session.add(case)

    heavy_from, heavy_to = ctx.ago(days=16), ctx.ago(days=12)
    for _result, run, x in ctx.result_rows:
        if x["case"] != "home" or run.created_at > ctx.now:
            continue
        heavy = heavy_from <= run.created_at <= heavy_to
        metrics = {
            "lcp_ms": (3100 if heavy else 1500) + rng.randint(0, 600),
            "cls": round(rng.uniform(0.0, 0.06), 3),
            "fcp_ms": 700 + rng.randint(0, 300), "ttfb_ms": 120 + rng.randint(0, 120),
            "dom_content_loaded_ms": 800 + rng.randint(0, 300), "load_ms": 1400 + rng.randint(0, 600),
            "page_weight_kb": (1840 if heavy else 820) + rng.randint(0, 120),
            "request_count": 22 + rng.randint(0, 6),
            "image_weight_kb": (1350 if heavy else 330) + rng.randint(0, 80),
            "largest_image_kb": 1100 if heavy else 210, "script_weight_kb": 240 + rng.randint(0, 30),
            "third_party_requests": 3,
        }
        checks = [{"metric": k, "value": metrics[k], "budget": v, "passed": metrics[k] <= v}
                  for k, v in budgets.items()]
        seo = [{"check": "title", "passed": True, "severity": "info", "value": "Example Domain"},
               {"check": "meta_description", "passed": False, "severity": "warning",
                "detail": "no meta description"},
               {"check": "h1", "passed": True, "severity": "info", "value": "1 h1"},
               {"check": "lang", "passed": True, "severity": "info", "value": "en"}]
        for sample in normalise_samples([{"url": LANDING, "step_id": step["id"], "metrics": metrics,
                                          "checks": checks, "seo": seo}]):
            ctx.session.add(PageBudgetSample(
                project_id=run.project_id, test_run_id=run.id, test_case_id=case.id,
                test_name=case.name, variant=None,
                created_at=ctx.past(run.created_at + timedelta(seconds=40)), **sample))


# --- accessibility ----------------------------------------------------------

def _accessibility(ctx) -> None:
    """One axe check per run of the a11y case. Moderate findings ride along on
    every run (the case only fails at 'serious'); the colour-contrast failures
    in the history carry the serious violation their error message names."""
    from app.models import AccessibilityCheck, AccessibilityViolation, AccessibilityWaiver
    from app.services.accessibility import normalise_results

    case = ctx.cases["web"]["a11y"]
    step_id = case.steps[1]["id"]
    landmark_fixed = ctx.ago(days=10)
    for _result, run, x in ctx.result_rows:
        if x["case"] != "a11y" or run.created_at > ctx.now:
            continue
        violations = [{"id": "region", "impact": "moderate", "node_count": 2,
                       "help_url": _AXE_HELP.format("region"),
                       "targets": [{"selector": "body > div > p", "nodes": 2}]}]
        if run.created_at < landmark_fixed:
            violations.append({"id": "landmark-one-main", "impact": "moderate", "node_count": 1,
                               "help_url": _AXE_HELP.format("landmark-one-main"),
                               "targets": [{"selector": "html", "nodes": 1}]})
        if x["status"] == "failed":
            violations.append({"id": "color-contrast", "impact": "serious", "node_count": 3,
                               "help_url": _AXE_HELP.format("color-contrast"),
                               "targets": [{"selector": "p > a", "nodes": 3}]})
        for c in normalise_results([{"url": LANDING, "step_id": step_id, "violations": violations}]):
            ctx.session.add(AccessibilityCheck(
                project_id=run.project_id, test_run_id=run.id, test_case_id=case.id,
                test_name=case.name, step_id=c["step_id"], page_key=c["page_key"],
                browser=run.browser or "chromium", device=run.device,
                violation_count=len(c["violations"]), created_at=run.created_at))
            for v in c["violations"]:
                ctx.session.add(AccessibilityViolation(
                    project_id=run.project_id, test_run_id=run.id, test_case_id=case.id,
                    test_name=case.name, step_id=c["step_id"], page_key=c["page_key"],
                    created_at=run.created_at, **v))
    ctx.session.add(AccessibilityWaiver(
        project_id=ctx.projects["web"].id, rule_id="region", selector_pattern="body > div *",
        reason="Static marketing copy outside landmarks; moves into <main> with the redesign (DEMO).",
        owner_user_id=ctx.user.id, created_by_id=ctx.user.id,
        created_at=ctx.ago(days=20), updated_at=ctx.ago(days=20),
        expires_at=ctx.now + timedelta(days=40)))
