"""Demo datasets: delivery, incidents, dependencies, journeys and releases.

Pages this fills (user-guide gaps, info/GUIDE_FOLLOWUPS.md):

* Reports → Delivery (DORA): deployments carry the commit a CI run tested and
  its commit time (lead time), and incidents start within 24 h of some of
  them (change failures, time to restore).
* Incidents: a recurrence pair, an escaped defect with its regression case,
  one matched to a failure cluster and a case by the real matcher.
* Reports → Dependencies: third-party responses in the web runs'
  `network_events`, rolled up into VendorHealthDaily with the same pure
  functions the hourly task uses.
* Reports → Journey coverage + Business journeys: an analytics export parsed
  by the real parser, and a "Sign in and manage todos" journey.
* Release readiness / Quality gate / Requirements / Manual testing: a gate
  policy that blocks (an active quarantine), JUnit reports, a manual suite with
  two manual runs and two sign-offs, requirements with parsed acceptance
  criteria in every coverage state, and a proposal generated from one.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional

# Semver labels for the core plan's deployments (oldest first). The newest is
# the release the manual sign-offs are about.
_VERSIONS = ["1.8.0", "1.8.1", "1.9.0", "2.0.0", "2.0.1", "2.1.0", "2.2.0", "2.2.1", "2.3.0", "2.4.0"]
RELEASE = "2.4.0"
_SERVICE = "todolite-web"
_TODOLITE = "http://localhost:8080"


async def seed(ctx) -> None:
    rng = ctx.rng_for("releases_reports")
    _delivery(ctx, rng)
    _vendor_traffic(ctx, rng)
    _journeys(ctx)
    manual = await _manual_release(ctx)
    await _requirements(ctx, manual)
    await _incidents(ctx)
    await _gate(ctx)


def _sid(rng) -> str:
    from app.services.demo_workspace import _step_id
    return _step_id(rng)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _delivery(ctx, rng) -> None:
    web_id = ctx.projects["web"].id
    deps = [d for d in ctx.deployments if d.project_id == web_id]
    ci_runs = [run for run in ctx.runs_of("web") if run.git_commit]
    labels = _VERSIONS[-len(deps):] if len(deps) <= len(_VERSIONS) else None
    for i, dep in enumerate(deps):
        tested = [run for run in ci_runs if run.created_at <= dep.deployed_at]
        if not tested:
            continue
        # Mostly that afternoon's CI commit; every third one sat in review for
        # a day, so lead time is not one flat value.
        run = tested[-2] if i % 3 == 2 and len(tested) > 1 else tested[-1]
        dep.git_commit = run.git_commit
        dep.git_branch = run.git_branch or "main"
        dep.git_repo = "example/demo"
        dep.commit_at = run.created_at - timedelta(minutes=rng.randint(15, 140))
        dep.external_id = f"demo-deploy-{i + 1}"
        if labels:
            dep.version = labels[i]
        ctx.session.add(dep)
    ctx.extra["web_deployments"] = deps


# ---------------------------------------------------------------------------
# Third-party dependencies
# ---------------------------------------------------------------------------

_DOC_URL = {"home": "https://example.com/", "a11y": "https://example.com/"}


def _events_for(case: Any, key: str, failed: bool, rng, start_ms: int) -> List[Dict[str, Any]]:
    """What the worker's network interceptor records for one case (minus
    headers): its document plus, on todolite pages, Google Fonts and Stripe."""
    doc = _DOC_URL.get(key, f"{_TODOLITE}/login")
    out: List[Dict[str, Any]] = []
    clock = [start_ms]

    def ev(url: str, kind: str, status: int, duration: int) -> None:
        out.append({"testCaseId": case.id, "testCaseName": case.name, "url": url, "method": "GET",
                    "resourceType": kind, "status": status, "startTime": clock[0],
                    "endTime": clock[0] + duration, "duration": duration})
        clock[0] += max(1, duration // 3)

    ev(doc, "document", 200, rng.randint(80, 260))
    if doc.startswith(_TODOLITE):
        ev(f"{_TODOLITE}/static/app.css", "stylesheet", 200, rng.randint(10, 40))
        ev("https://fonts.googleapis.com/css2", "stylesheet", 200, rng.randint(40, 160))
        ev("https://fonts.gstatic.com/s/inter/v13/inter-latin.woff2", "font", 200, rng.randint(30, 120))
        # Stripe's script often answered 5xx on runs that failed — the
        # co-occurrence the Dependencies page is there to show.
        bad = rng.random() < (0.45 if failed else 0.04)
        ev("https://js.stripe.com/v3/", "script", rng.choice((502, 503)) if bad else 200,
           rng.randint(900, 2600) if bad else rng.randint(60, 240))
    return out


def _vendor_traffic(ctx, rng) -> None:
    """Record third-party responses on the web runs, then roll them up per day
    exactly as app.tasks.reliability_tasks.aggregate_day does."""
    from app.models import VendorHealthDaily
    from app.services import vendor_health

    by_run: Dict[int, List] = {}
    for result, run, x in ctx.result_rows:
        by_run.setdefault(run.id, []).append((result, x))
    env_url = ctx.envs["web"].base_url
    days: Dict[Any, Dict[str, Any]] = {}
    for run, r in ctx.run_rows:
        if r["project"] != "web":
            continue
        events: List[Dict[str, Any]] = []
        for n, (_result, x) in enumerate(by_run.get(run.id, [])):
            events.extend(_events_for(ctx.cases["web"][x["case"]], x["case"],
                                      x["status"] == "failed", rng, n * 5000))
        run.network_events = events
        ctx.session.add(run)
        if run.created_at > ctx.now:
            continue
        vendors = vendor_health.vendors_in_run(events, (run.target_url, env_url))
        if not vendors:
            continue
        passed, failed = vendor_health.run_outcomes(
            [(res.status, 1) for res, _x in by_run.get(run.id, [])])
        vendor_health.add_run(days.setdefault(run.created_at.date(), {}), vendors, passed, failed)
    for day, per_vendor in days.items():
        for vendor, agg in per_vendor.items():
            ctx.session.add(VendorHealthDaily(project_id=ctx.projects["web"].id, day=day, vendor=vendor,
                                              computed_at=ctx.now, **vendor_health.row_values(agg)))


# ---------------------------------------------------------------------------
# Journey coverage + business journeys
# ---------------------------------------------------------------------------

_PAGE_VIEWS = [("/", 48210), ("/login", 21950), ("/todos", 19870), ("/signup", 6420),
               ("/pricing", 3980), ("/settings", 2210), ("/todos/archive", 960), ("/help", 610)]
_FUNNELS = [("Sign in and manage todos", [("/", 21950), ("/login", 18400), ("/todos", 16120)]),
            ("Sign up", [("/", 6420), ("/signup", 3110), ("/welcome", 2280)])]


def _journey_csv(first: str, last: str) -> bytes:
    lines = ["path,views,journey,step,date"]
    for i, (path, views) in enumerate(_PAGE_VIEWS):
        lines.append(f"{path},{views},,,{first if i % 2 == 0 else last}")
    for journey, steps in _FUNNELS:
        for n, (path, views) in enumerate(steps, 1):
            lines.append(f"{path},{views},{journey},{n},{last}")
    return ("\n".join(lines) + "\n").encode()


def _journeys(ctx) -> None:
    from app.models import BusinessJourney, JourneyImport
    from app.services import journey_coverage as jc

    web = ctx.projects["web"]
    first = (ctx.now - timedelta(days=30)).date().isoformat()
    last = (ctx.now - timedelta(days=1)).date().isoformat()
    parsed = jc.parse_import(_journey_csv(first, last), "csv", "todolite-pageviews.csv")
    ctx.session.add(JourneyImport(
        project_id=web.id, label="Last 30 days (demo analytics export)",
        filename="todolite-pageviews.csv", source_format=parsed.source_format,
        row_count=parsed.row_count, skipped_rows=parsed.skipped_rows, path_count=len(parsed.rows),
        total_views=parsed.total_views, period_start=parsed.period_start,
        period_end=parsed.period_end, rows=parsed.rows, warnings=parsed.warnings,
        created_by_id=ctx.user.id, created_at=ctx.ago(hours=20)))
    cases = ctx.cases["web"]
    ctx.session.add(BusinessJourney(
        project_id=web.id, name="Sign in and manage todos",
        description="The core loop: a returning user signs in and adds a todo.",
        owner_id=ctx.user.id, criticality="critical",
        case_ids=[cases["login"].id, cases["add_todo"].id],
        requirement_refs=["DEMO-101", "DEMO-102"], traffic_journey="Sign in and manage todos",
        created_by_id=ctx.user.id, created_at=ctx.ago(days=12), updated_at=ctx.ago(days=12)))


# ---------------------------------------------------------------------------
# Manual release checklist + sign-offs
# ---------------------------------------------------------------------------

_MANUAL = [
    ("checkout", "Upgrade to Pro with a test card", [
        ("Open Settings → Billing and choose Pro", "The card form appears"),
        ("Pay with the Stripe test card 4242 4242 4242 4242", "Payment succeeds; the plan shows Pro"),
        ("Open the receipt email", "Amount and plan are correct")]),
    ("decline", "Declined card keeps the free plan", [
        ("Upgrade with the Stripe decline test card 4000 0000 0000 0002",
         "The decline message shows and the plan stays Free")]),
    ("mobile_layout", "Todo list is usable on a phone", [
        ("Open the todo list at 375px width", "No horizontal scrolling"),
        ("Add, complete and delete a todo", "Every control is reachable with a thumb")]),
    ("screen_reader", "Sign-in works with a screen reader", [
        ("Sign in with VoiceOver on", "Every field is announced with its label"),
        ("Submit a wrong password", "The error is announced")]),
]


async def _manual_release(ctx) -> Dict[str, Any]:
    """Two manual passes over release 2.4.0: the first fails the payment
    checks and is rejected, the re-test of the upgrade passes and is approved.
    The declined-card check was not re-tested, so its latest result is still
    the failure (the Requirements page's "failing" row)."""
    from app.models import (
        ExecutionMode, ExecutorType, ReleaseSignoff, RunTrigger, TestCase, TestCaseResult, TestRun,
        TestStatus, TestSuite,
    )
    from app.services import manual_testing as mt

    rng = ctx.rng_for("releases_reports:manual")
    web = ctx.projects["web"]
    created = ctx.ago(days=20)
    suite = TestSuite(name="Release checklist (manual)", project_id=web.id,
                      description="Checks a person runs and signs off for each release.",
                      execution_mode=ExecutionMode.SEPARATE, created_by_id=ctx.user.id,
                      updated_by_id=ctx.user.id, created_at=created)
    ctx.session.add(suite)
    await ctx.session.flush()
    cases: Dict[str, Any] = {}
    for key, name, steps in _MANUAL:
        case = TestCase(name=name, description="Manual check (demo).", project_id=web.id,
                        test_suite_id=suite.id, executor=ExecutorType.MANUAL, priority="high",
                        tags=["manual", "release"],
                        steps=[{"id": _sid(rng), "type": mt.MANUAL_STEP, "value": text,
                                "params": {"expected": expected}} for text, expected in steps],
                        created_by_id=ctx.user.id, updated_by_id=ctx.user.id, created_at=created)
        ctx.session.add(case)
        cases[key] = case
    await ctx.session.flush()

    members = [m for m in ctx.members.values() if getattr(m, "id", None)]
    tester = members[0] if members else ctx.user
    deps = ctx.extra.get("web_deployments") or []
    ci = [run for run in ctx.runs_of("web") if run.git_commit]
    commit = (deps[-1].git_commit if deps and deps[-1].git_commit
              else (ci[-1].git_commit if ci else None))

    async def manual_run(at: datetime, picks: List[tuple]) -> Any:
        run = TestRun(test_suite_id=suite.id, project_id=web.id, suite_name=suite.name,
                      executor=ExecutorType.MANUAL, status=TestStatus.PENDING, browser="manual",
                      total_tests=len(picks), user_id=tester.id, triggered_by=RunTrigger.HUMAN,
                      selection_mode="targeted" if len(picks) < len(cases) else "full",
                      selection_total=len(cases), git_commit=commit, git_branch="main",
                      domain_settings={"release_label": RELEASE}, created_at=at,
                      screenshots=[], execution_log=[], network_events=[])
        ctx.session.add(run)
        await ctx.session.flush()
        outcomes, total = [], 0.0
        for n, (case, outcome, note) in enumerate(picks):
            snapshot = [{"instruction": s["value"], "expected": s["params"]["expected"]} for s in case.steps]
            step_results = [{"outcome": outcome if i == len(snapshot) - 1 else "passed",
                             "note": note if i == len(snapshot) - 1 else None}
                            for i in range(len(snapshot))]
            duration = float(rng.randint(3, 14) * 60_000)
            total += duration
            ctx.session.add(TestCaseResult(
                test_run_id=run.id, test_case_id=case.id, test_name=case.name,
                status=TestStatus(mt.outcome_status(outcome)), duration_ms=duration,
                result_kind=mt.RESULT_KIND, screenshots=[],
                error_message=((note or f"Marked {outcome} by tester")[:2000]
                               if outcome in ("failed", "blocked") else None),
                result_payload={"outcome": outcome, "steps": snapshot, "step_results": step_results,
                                "notes": note, "tester_id": tester.id,
                                "recorded_at": (at + timedelta(minutes=12 * (n + 1))).isoformat()}))
            outcomes.append(outcome)
        summary = mt.summarize(outcomes)
        run.status = TestStatus(summary["status"])
        run.passed_tests, run.failed_tests = summary["passed"], summary["failed"]
        run.duration_ms = total
        run.finalized_at = at + timedelta(hours=1)
        ctx.session.add(run)
        return run

    c = cases
    first = await manual_run(ctx.ago(days=2, hours=3), [
        (c["checkout"], "failed", "Card form never loaded: js.stripe.com answered 503."),
        (c["decline"], "failed", "Plan switched to Pro although the card was declined."),
        (c["mobile_layout"], "passed", None),
        (c["screen_reader"], "passed", None)])
    second = await manual_run(ctx.ago(days=1, hours=5), [(c["checkout"], "passed", None)])
    ctx.session.add(ReleaseSignoff(
        project_id=web.id, release_label=RELEASE, git_commit=commit, run_ids=[first.id],
        verdict="reject", note="Both payment checks failed. Hold the release until they are re-tested.",
        signed_by_id=ctx.user.id, signed_at=first.finalized_at + timedelta(minutes=30)))
    ctx.session.add(ReleaseSignoff(
        project_id=web.id, release_label=RELEASE, git_commit=commit,
        run_ids=[first.id, second.id], case_ids=[c["checkout"].id, c["mobile_layout"].id,
                                                 c["screen_reader"].id],
        verdict="approve",
        note="Upgrade re-tested and passing. Declined-card handling ships behind the billing flag; "
             "tracked in DEMO-104.",
        signed_by_id=ctx.user.id, signed_at=second.finalized_at + timedelta(minutes=15)))
    ctx.extra["manual_suite"] = suite
    ctx.extra["manual_cases"] = cases
    return {"suite": suite, "cases": cases}


# ---------------------------------------------------------------------------
# Requirements, traceability, a requirement-generated proposal
# ---------------------------------------------------------------------------

_REQ_TEXT = {
    "DEMO-101": ("A user can sign in", """As a returning user I want to sign in so I can see my todos.

Acceptance criteria:
Scenario: Sign in with valid credentials
Given I am on the login page
When I sign in as alice with the correct password
Then I see "alice" in the header

Scenario: Wrong password
Given I am on the login page
When I sign in as alice with a wrong password
Then I see the error "Invalid username or password"
"""),
    "DEMO-102": ("A user can add a todo", """Acceptance criteria:
- Typing a title and pressing Add shows the todo at the top of the list
- An empty title cannot be added and the Add button is disabled
- The todo list should feel fast and intuitive
"""),
    "DEMO-103": ("A user can reset a forgotten password", """Acceptance criteria:
1. The login page links to "Forgot password"
2. Submitting a known email sends a reset link that expires after 30 minutes
3. Submitting an unknown email shows the same confirmation message
"""),
    "DEMO-104": ("Paying for Pro works end to end", """Acceptance criteria:
- [ ] A user on the free plan can upgrade to Pro with a card
- [ ] A declined card shows the bank's message and the plan is unchanged
"""),
    "DEMO-105": ("Users can export their todos as CSV", """Acceptance criteria:
AC1: The Settings page has an "Export todos" button
AC2: The download is a CSV with title, done and created columns
"""),
}


async def _requirements(ctx, manual: Dict[str, Any]) -> None:
    """Coverage states on the Requirements page: DEMO-101 covered, DEMO-103
    untested (linked case never ran), DEMO-104 failing (declined-card manual
    check), DEMO-105 not covered (a pending proposal generated from it)."""
    from sqlmodel import select

    from app.models import CaseProposal, CaseProposalAction, Requirement, RequirementLink, TestCase
    from app.services import acceptance_criteria as ac
    from app.services.generation_proposals import GENERATED_CONFIDENCE, case_name

    web = ctx.projects["web"]
    rng = ctx.rng_for("releases_reports:requirements")
    existing = {r.external_key: r for r in (await ctx.session.exec(
        select(Requirement).where(Requirement.project_id == web.id))).all()}
    reqs: Dict[str, Any] = {}
    for key, (title, description) in _REQ_TEXT.items():
        req = existing.get(key) or Requirement(project_id=web.id, source="jira", external_key=key,
                                               title=title, created_by_id=ctx.user.id)
        req.title = title
        req.description = description
        req.url = f"https://demo-tracker.example.invalid/browse/{key}"
        req.external_status = "Done" if key in ("DEMO-101", "DEMO-104") else "In Progress"
        req.acceptance_criteria = ac.analyse(description)
        req.last_synced_at = ctx.ago(hours=2)
        req.external_updated_at = ctx.ago(days=3)
        req.created_at = ctx.ago(days=25)
        ctx.session.add(req)
        reqs[key] = req
    await ctx.session.flush()

    # Exists, never run: DEMO-103 reads "untested", traceability shows a gap.
    reset = TestCase(
        name="Reset password email is sent", project_id=web.id,
        test_suite_id=ctx.suites["web_smoke"].id, priority="medium", tags=["auth"],
        description="Requests a reset link for alice (not run yet).",
        steps=[{"id": _sid(rng), "type": "goto", "value": "{{env.BASE_URL}}/forgot-password"},
               {"id": _sid(rng), "type": "fill", "selector": "#email", "value": "alice@example.com"},
               {"id": _sid(rng), "type": "click", "selector": "#send-reset"},
               {"id": _sid(rng), "type": "expect-text", "selector": ".notice",
                "value": "If that address has an account, a reset link is on its way"}],
        created_by_id=ctx.user.id, updated_by_id=ctx.user.id, created_at=ctx.ago(days=2))
    ctx.session.add(reset)
    await ctx.session.flush()
    ctx.extra["never_run_case"] = reset

    mc = manual["cases"]
    for key, case in (("DEMO-103", reset), ("DEMO-104", mc["checkout"]), ("DEMO-104", mc["decline"])):
        ctx.session.add(RequirementLink(test_case_id=case.id, project_id=web.id, ref=key, source="jira",
                                        title=reqs[key].title, url=reqs[key].url,
                                        created_by_id=ctx.user.id, created_at=ctx.ago(days=2)))
    # A hand-made traceability link (no tracker) from a product doc.
    ctx.session.add(RequirementLink(test_case_id=ctx.cases["web"]["cookie"].id, project_id=web.id,
                                    ref="PRD-Security-3", source="manual",
                                    title="Session cookies are HttpOnly and SameSite",
                                    created_by_id=ctx.user.id, created_at=ctx.ago(days=9)))

    # DEMO-105: nothing linked yet; its first criterion has a pending proposal
    # shaped like app/services/generation_proposals.propose writes one.
    req = reqs["DEMO-105"]
    criteria = [dict(c) for c in req.acceptance_criteria]
    crit = criteria[0]
    n = crit["index"] + 1
    proposal = CaseProposal(
        project_id=web.id, test_suite_id=ctx.suites["web_smoke"].id, action=CaseProposalAction.CREATE,
        payload={"name": case_name(f"{req.external_key} AC{n}:", crit["text"]),
                 "steps": [{"type": "goto", "value": "{{env.BASE_URL}}/settings",
                            "intent": "Open Settings"},
                           {"type": "expect-visible", "selector": "button#export-todos",
                            "intent": "The Export todos button is shown"}],
                 "code_paths": [], "tags": [f"req:{req.external_key}"], "needs_authoring": False,
                 "requirement_id": req.id, "criterion_index": crit["index"]},
        rationale=f"{req.external_key} AC{n}: {crit['text']}"[:500],
        ai_confidence=GENERATED_CONFIDENCE, created_by_id=ctx.user.id, created_at=ctx.ago(hours=6))
    ctx.session.add(proposal)
    await ctx.session.flush()
    crit["proposal_id"] = proposal.id
    req.acceptance_criteria = criteria
    ctx.session.add(req)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

async def _incidents(ctx) -> None:
    from app.api.incidents import ingest
    from app.models import CaseProposal, CaseProposalAction, TestCase
    from app.services.demo_workspace import HISTORY_DAYS

    web, api = ctx.projects["web"], ctx.projects["api"]
    deps = ctx.extra.get("web_deployments") or []
    rng = ctx.rng_for("releases_reports:incidents")

    def dep(i: int) -> Optional[Any]:
        return deps[i] if -len(deps) <= i < len(deps) else None

    async def add(project, data: Dict[str, Any]):
        data = dict(data)
        data["started_at"] = ctx.past(data["started_at"])
        if data.get("resolved_at"):
            data["resolved_at"] = ctx.past(data["resolved_at"])
        # The webhook/create path itself: fingerprint, recurrence link and
        # cluster/case matching are computed exactly as for a real incident.
        inc, _ = await ingest(ctx.session, project, data, user_id=ctx.user.id)
        inc.created_at = inc.updated_at = data["started_at"]
        ctx.session.add(inc)
        return inc

    made = []
    # 1 + 2: the same outage twice (a recurrence), each within 24 h of a deploy.
    d1, d2 = dep(-3), dep(-1)
    if d1 and d2:
        t1 = d1.deployed_at + timedelta(hours=3, minutes=12)
        made.append(await add(web, {
            "source": "pagerduty", "external_id": "DEMO-PD-1042",
            "title": "Sign-in page returns 502 at 19:12", "severity": "high", "status": "resolved",
            "service": _SERVICE, "affected_url": f"{_TODOLITE}/login",
            "url": "https://demo-pager.example.invalid/incidents/DEMO-PD-1042",
            "description": "Login POST answered 502 for about 6% of requests after the deploy.",
            "started_at": t1, "resolved_at": t1 + timedelta(hours=1, minutes=35),
            "git_commit": d1.git_commit, "release": d1.version}))
        t2 = d2.deployed_at + timedelta(hours=2, minutes=40)
        made.append(await add(web, {
            "source": "pagerduty", "external_id": "DEMO-PD-1107",
            "title": "Sign-in page returns 503 at 18:40", "severity": "high", "status": "resolved",
            "service": _SERVICE, "affected_url": f"{_TODOLITE}/login",
            "url": "https://demo-pager.example.invalid/incidents/DEMO-PD-1107",
            "description": "Same symptom as DEMO-PD-1042: the session store ran out of connections.",
            "started_at": t2, "resolved_at": t2 + timedelta(minutes=48),
            "git_commit": d2.git_commit, "release": d2.version}))

    # 3: an escaped defect — the todo test should have caught it; a regression
    # test was added afterwards (accepted proposal → new case).
    d3 = dep(-5)
    if d3:
        t3 = d3.deployed_at + timedelta(hours=6, minutes=5)
        inc = await add(web, {
            "source": "manual", "title": "Todos disappear after adding a second item",
            "severity": "medium", "status": "resolved", "service": _SERVICE,
            "affected_url": f"{_TODOLITE}/todos",
            "description": "Adding a second todo replaced the first instead of appending it.",
            "started_at": t3, "resolved_at": t3 + timedelta(hours=5, minutes=20),
            "git_commit": d3.git_commit, "release": d3.version})
        added_at = ctx.past(t3 + timedelta(hours=8))
        case = TestCase(
            name="Regression: two todos stay in the list", project_id=web.id,
            test_suite_id=ctx.suites["web_smoke"].id, priority="high", tags=["regression", "incident"],
            description="Added after the 'todos disappear' incident.",
            steps=[{"id": _sid(rng), "type": "goto", "value": "{{env.BASE_URL}}/login"},
                   {"id": _sid(rng), "type": "fill", "selector": "#login-username", "value": "alice"},
                   {"id": _sid(rng), "type": "fill", "selector": "#login-password",
                    "value": "{{secret.ALICE_PASSWORD}}"},
                   {"id": _sid(rng), "type": "click", "selector": "#login-submit"},
                   {"id": _sid(rng), "type": "fill", "selector": "#todo-input", "value": "first {{unique.a}}"},
                   {"id": _sid(rng), "type": "click", "selector": "#todo-create-button"},
                   {"id": _sid(rng), "type": "fill", "selector": "#todo-input", "value": "second {{unique.b}}"},
                   {"id": _sid(rng), "type": "click", "selector": "#todo-create-button"},
                   {"id": _sid(rng), "type": "expect-text", "selector": "#todo-list li:nth-child(2)",
                    "value": "first"}],
            created_by_id=ctx.user.id, updated_by_id=ctx.user.id, created_at=added_at)
        ctx.session.add(case)
        await ctx.session.flush()
        proposal = CaseProposal(
            project_id=web.id, test_suite_id=case.test_suite_id, action=CaseProposalAction.CREATE,
            payload={"name": case.name, "steps": case.steps, "code_paths": [],
                     "tags": ["incident", "regression"], "needs_authoring": False, "incident_id": inc.id},
            rationale=f"Regression test for production incident: {inc.title}."[:500],
            ai_confidence=0.7, status="accepted", created_by_id=ctx.user.id,
            created_at=added_at - timedelta(hours=1), decided_at=added_at,
            decided_by_id=ctx.user.id, decision_note="Reviewed; reproduces the incident's symptom.")
        ctx.session.add(proposal)
        await ctx.session.flush()
        inc.regression_proposal_id = proposal.id
        inc.regression_status = "accepted"
        inc.regression_case_id = case.id
        inc.escaped_defect = True
        inc.escaped_case_id = ctx.cases["web"]["add_todo"].id
        inc.escaped_note = "'Add a todo' only checks that one item is visible."
        inc.escaped_marked_at = ctx.past(t3 + timedelta(hours=6))
        inc.escaped_marked_by_id = ctx.user.id
        ctx.session.add(inc)
        made.append(inc)

    # 4: the API latency incident during the timeout cluster's bad days — the
    # matcher links it to that cluster and to the delay case.
    t4 = datetime.combine(ctx.plan["first_day"] + timedelta(days=HISTORY_DAYS - 8), dtime(9, 20))
    made.append(await add(api, {
        "source": "opsgenie", "external_id": "DEMO-OG-311",
        "title": "Delayed endpoint timeout waiting on /delay", "severity": "medium",
        "status": "resolved", "service": "public-api", "affected_url": "https://httpbin.org/delay/1",
        "description": "Upstream latency spike; p95 above 3 s for four days.",
        "started_at": t4, "resolved_at": t4 + timedelta(days=2, hours=3)}))

    # 5: open right now, matched to the homepage case by name.
    made.append(await add(web, {
        "source": "generic", "title": "Homepage loads slowly for visitors in APAC",
        "severity": "low", "status": "acknowledged", "service": "marketing-site",
        "affected_url": "https://example.com/",
        "description": "Synthetic checks from Singapore report LCP above 4 s.",
        "started_at": ctx.ago(hours=5)}))
    ctx.extra["incidents"] = made


# ---------------------------------------------------------------------------
# Gate policy, external reports, an active quarantine
# ---------------------------------------------------------------------------

def _junit(failing: bool) -> bytes:
    cases = [
        ("TodoList renders empty state", "web.unit.TodoList", None),
        ("TodoList adds item", "web.unit.TodoList", None),
        ("formatDue handles timezones", "web.unit.dates", None),
        ("api client retries 503", "web.unit.api", "expected 2 calls, got 1" if failing else None),
        ("session cookie parser", "web.unit.auth", None),
    ]
    body = []
    for name, cls, failure in cases:
        if failure:
            body.append(f'<testcase name="{name}" classname="{cls}" time="0.12">'
                        f'<failure message="{failure}">AssertionError: {failure}</failure></testcase>')
        else:
            body.append(f'<testcase name="{name}" classname="{cls}" time="0.05"/>')
    return (f'<testsuites><testsuite name="web unit tests (vitest)" tests="{len(cases)}" time="3.4">'
            + "".join(body) + "</testsuite></testsuites>").encode()


async def _gate(ctx) -> None:
    from app.api.external_results import parse_junit_xml
    from app.models import ExternalTestReport, QualityGatePolicy
    from app.services import flake_policy, gate_policy_versions

    web = ctx.projects["web"]
    # Written the way PUT .../quality-gate/policy writes it: the JSON on the
    # project plus the version row that every later verdict names.
    policy = QualityGatePolicy(min_pass_rate=90.0, max_high_severity_findings=0,
                               require_external_tests_pass=True, max_flaky_cases=0,
                               require_critical_passed=True, max_evidence_age_days=7)
    web.quality_gate_policy = policy.model_dump()
    ctx.session.add(web)
    version = await gate_policy_versions.ensure_current(
        ctx.session, web, policy, created_by_id=ctx.user.id, commit=False)
    version.created_at = ctx.ago(days=14)
    ctx.session.add(version)

    ci = [run for run in ctx.runs_of("web") if run.git_commit]
    for run, failing in (((ci[-2], True), (ci[-1], False)) if len(ci) >= 2 else ()):
        parsed = parse_junit_xml(_junit(failing))
        ctx.session.add(ExternalTestReport(
            project_id=web.id, source="junit", suite_name=parsed["suite_name"],
            git_commit=run.git_commit, git_branch=run.git_branch, tests=parsed["tests"],
            failures=parsed["failures"], errors=parsed["errors"], skipped=parsed["skipped"],
            time_seconds=round(parsed["time_seconds"], 3), failed_cases=parsed["failed_cases"],
            uploaded_by="api-key:ci (demo)", created_at=ctx.past(run.created_at + timedelta(minutes=4))))

    # An active manual quarantine on the flaky case: with max_flaky_cases 0
    # the readiness verdict is "not ready" and says why.
    add_todo = ctx.cases["web"]["add_todo"].id
    for flake in ctx.flake_records:
        if flake.test_case_id == add_todo:
            flake_policy.quarantine(flake, source=flake_policy.SOURCE_MANUAL, days=14,
                                    reason="Times out waiting for the list to refresh; tracked in DEMO-102.",
                                    by_user_id=ctx.user.id, now=ctx.ago(days=3))
            ctx.session.add(flake)
