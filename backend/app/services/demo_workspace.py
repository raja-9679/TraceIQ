"""Seeded demo workspace (WP13c, decision C5).

One click gives an evaluator a workspace that already looks lived-in: two
projects (a web app and an API), suites, environments, personas, a monitor,
requirements and ~30 days of run history with the things reports exist to show
— a mostly-green trend, a few real failures (all grouped into failure
clusters, as the triage task would), one flaky case,
a security finding and flake records.

Rules that matter:

* **Nothing is dispatched.** History is written straight into the tables; no
  worker, no Celery task, no network call. Steps point only at example.com,
  httpbin.org or a local todolite (http://localhost:8080) and are plain text
  until someone presses Run. The monitor is seeded PAUSED (is_active=False) so
  beat never fires it.
* **A demo never notifies anyone.** `Workspace.is_demo` is checked on every
  delivery path (run email/chat, monitor alerts, digests, paging, outbound
  webhooks — see `is_demo_project_sync`), and the projects are also seeded with
  notifications off. Either alone would do; the flag is the one that survives
  someone editing the project settings.
* **One active demo per user** (not per tenant): each evaluator gets their own
  sandbox to break, and a tenant-wide cap would make a colleague's demo block
  yours. The users row is locked for the check so two clicks cannot make two.
* **Delete is the ordinary workspace purge** (app/services/purge.py), so a new
  table added later is covered by the purge-plan test, not by this module.

`build_demo_plan` is pure and deterministic for a (seed, today) pair — the
history is reproducible and unit-testable without a database.
"""
from __future__ import annotations

import random
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from app.services.failure_signature import compute_signature

DEFAULT_SEED = 1307
HISTORY_DAYS = 30
DEMO_WORKSPACE_NAME = "Demo workspace"
TODOLITE_URL = "http://localhost:8080"

#: The only hosts a demo step may name (C5). Asserted by the unit tests.
ALLOWED_HOSTS = ("example.com", "httpbin.org", "localhost:8080")


class DemoExists(Exception):
    """The user already has an active demo workspace."""

    def __init__(self, workspace_id: int):
        super().__init__(f"demo workspace {workspace_id} already exists")
        self.workspace_id = workspace_id


# ---------------------------------------------------------------------------
# Pure plan
# ---------------------------------------------------------------------------

def _step_id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _web_cases(rng: random.Random) -> List[Dict[str, Any]]:
    def s(t: str, **kw: Any) -> Dict[str, Any]:
        return {"id": _step_id(rng), "type": t, **kw}
    return [
        {"key": "home", "name": "Homepage loads", "tags": ["smoke", "ui"], "priority": "critical",
         "description": "The public landing page renders its heading.",
         "steps": [s("goto", value="https://example.com", params={"wait_until": "domcontentloaded"}),
                   s("expect-title", value="Example Domain"),
                   s("expect-text", selector="h1", value="Example Domain"),
                   s("screenshot", value="homepage")]},
        {"key": "a11y", "name": "No serious accessibility violations", "tags": ["a11y"], "priority": "high",
         "description": "axe scan of the landing page at impact 'serious'.",
         "steps": [s("goto", value="https://example.com"),
                   s("check-accessibility", params={"impact": "serious"})]},
        {"key": "login", "name": "Sign in as alice", "tags": ["smoke", "auth"], "priority": "critical",
         "description": "todolite's seeded user can sign in.",
         "steps": [s("goto", value="{{env.BASE_URL}}/login"),
                   s("fill", selector="#login-username", value="alice"),
                   s("fill", selector="#login-password", value="{{secret.ALICE_PASSWORD}}"),
                   s("click", selector="#login-submit"),
                   s("expect-text", selector="#current-user", value="alice")]},
        {"key": "add_todo", "name": "Add a todo", "tags": ["regression"], "priority": "high",
         "description": "Creates a todo and sees it in the list (the demo's flaky case).",
         "steps": [s("goto", value="{{env.BASE_URL}}/login"),
                   s("fill", selector="#login-username", value="alice"),
                   s("fill", selector="#login-password", value="{{secret.ALICE_PASSWORD}}"),
                   s("click", selector="#login-submit"),
                   s("fill", selector="#todo-input", value="Buy milk {{unique.todo}}"),
                   s("click", selector="#todo-create-button"),
                   s("expect-visible", selector="#todo-list li")]},
        {"key": "cookie", "name": "Session cookie is HttpOnly", "tags": ["security"], "priority": "medium",
         "description": "The login cookie carries HttpOnly and SameSite=Lax.",
         "steps": [s("goto", value="{{env.BASE_URL}}/login"),
                   s("fill", selector="#login-username", value="bob"),
                   s("fill", selector="#login-password", value="{{secret.BOB_PASSWORD}}"),
                   s("click", selector="#login-submit"),
                   s("expect-cookie-flags", params={"name": "session", "httpOnly": True, "sameSite": "Lax"})]},
    ]


def _api_cases(rng: random.Random) -> List[Dict[str, Any]]:
    def req(url: str, **params: Any) -> Dict[str, Any]:
        return {"id": _step_id(rng), "type": "http-request", "value": url, "params": params}
    return [
        {"key": "json", "name": "GET /json has a slideshow", "tags": ["contract"], "priority": "critical",
         "description": "Response matches the expected JSON shape.",
         "steps": [req("{{env.BASE_URL}}/json", method="GET",
                       assertions=[{"type": "status", "value": 200}],
                       expect_schema={"schema": {"type": "object", "required": ["slideshow"],
                                                 "properties": {"slideshow": {"type": "object"}}}})]},
        {"key": "echo", "name": "POST /anything echoes the body", "tags": ["contract"], "priority": "high",
         "description": "The echo endpoint returns what was sent.",
         "steps": [req("{{env.BASE_URL}}/anything", method="POST", body={"order": "{{unique.order}}"},
                       assertions=[{"type": "status", "value": 200}],
                       expect_schema={"schema": {"type": "object", "required": ["json"],
                                                 "properties": {"json": {"type": "object",
                                                                         "required": ["order"]}}}})]},
        {"key": "auth", "name": "Basic auth is required", "tags": ["security"], "priority": "high",
         "description": "Without credentials the protected endpoint answers 401.",
         "steps": [req("{{env.BASE_URL}}/basic-auth/demo/demo", method="GET",
                       assertions=[{"type": "status", "value": 401}])]},
        {"key": "delay", "name": "Delayed endpoint answers within 3 s", "tags": ["performance"], "priority": "medium",
         "description": "The slow endpoint stays under the response budget (the demo's failure cluster).",
         "steps": [req("{{env.BASE_URL}}/delay/1", method="GET", timeout=3000,
                       assertions=[{"type": "status", "value": 200}])]},
        {"key": "headers", "name": "Echoed request headers", "tags": ["contract"], "priority": "low",
         "description": "Custom headers reach the server.",
         "steps": [req("{{env.BASE_URL}}/headers", method="GET", headers={"X-Demo": "1"},
                       assertions=[{"type": "json-path", "path": "headers.X-Demo", "operator": "equals",
                                    "value": "1"}])]},
    ]


_CLUSTER = {"project": "api", "key": "delay_timeout", "category": "timeout",
            "signature": "demo:timeout:httpbin-delay",
            "title": "Timeout waiting for /delay response",
            "error": "apiRequestContext.fetch: Timeout 3000ms exceeded.\nCall log:\n  - → GET https://httpbin.org/delay/1"}
_MONITOR_ERROR = "Expected status 200 but got 503"
_FLAKY_ERROR = ("page.waitForSelector: Timeout 5000ms exceeded.\nCall log:\n"
                "  - waiting for locator('#todo-list li') to be visible")
# Error texts are worded exactly as the worker words them
# (execution-engine/src/core/test-executor.ts, security-steps.ts), so triage
# groups and categorises the demo's failures as it would real ones.
_RANDOM_ERRORS = {
    "home": 'Expected text "Example Domain" not found in element "h1"',
    "a11y": "Accessibility check failed: 1 violation(s) — color-contrast (serious, 3)",
    "login": 'Expected text "alice" not found in element "#current-user"',
    "cookie": "Expected cookie session to be set",
    "json": "Expected status 200 but got 502",
    "echo": "Expected json.order to equal 42 but got undefined",
    "auth": "Expected status 401 but got 503",
    "headers": "Expected headers.X-Demo to equal 1 but got undefined",
}


def build_demo_plan(seed: int = DEFAULT_SEED, today: Optional[date] = None,
                    days: int = HISTORY_DAYS, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the demo contains, as plain data. Deterministic per (seed, today).
    Nothing is dated after `now` (default: the end of `today`, else utcnow)."""
    rng = random.Random(seed)
    if now is None:
        now = datetime.combine(today, time.max) if today else datetime.utcnow()
    today = today or now.date()
    first = today - timedelta(days=days - 1)
    projects = [
        {"key": "web", "name": "Demo — Todo web app",
         "description": "DEMO data. A web journey suite against example.com and a local todolite "
                        f"({TODOLITE_URL}). Nothing here has run; the history is generated.",
         "environments": [
             {"name": "local", "base_url": TODOLITE_URL, "is_default": True,
              "variables": {"BASE_URL": TODOLITE_URL}},
             {"name": "public", "base_url": "https://example.com", "is_default": False,
              "variables": {"BASE_URL": "https://example.com"}},
         ],
         # todolite's published demo passwords (README) — stored through the
         # secrets mechanism like any other credential, never in steps.
         "secrets": {"ALICE_PASSWORD": "wonderland", "BOB_PASSWORD": "builder"},
         "suites": [{"key": "web_smoke", "name": "Web smoke (demo)",
                     "description": "UI journeys: landing page, sign-in, todos, cookie flags.",
                     "cases": _web_cases(rng)}],
         "personas": [
             {"name": "alice (todolite)", "description": "Seeded todolite user.",
              "login_steps": [
                  {"id": _step_id(rng), "type": "goto", "value": "{{env.BASE_URL}}/login"},
                  {"id": _step_id(rng), "type": "fill", "selector": "#login-username", "value": "alice"},
                  {"id": _step_id(rng), "type": "fill", "selector": "#login-password",
                   "value": "{{secret.ALICE_PASSWORD}}"},
                  {"id": _step_id(rng), "type": "click", "selector": "#login-submit"}]},
             {"name": "bob (todolite)", "description": "Second seeded todolite user.",
              "login_steps": [
                  {"id": _step_id(rng), "type": "goto", "value": "{{env.BASE_URL}}/login"},
                  {"id": _step_id(rng), "type": "fill", "selector": "#login-username", "value": "bob"},
                  {"id": _step_id(rng), "type": "fill", "selector": "#login-password",
                   "value": "{{secret.BOB_PASSWORD}}"},
                  {"id": _step_id(rng), "type": "click", "selector": "#login-submit"}]},
         ],
         "requirements": [
             {"external_key": "DEMO-101", "title": "A user can sign in", "cases": ["login"]},
             {"external_key": "DEMO-102", "title": "A user can add a todo", "cases": ["add_todo"]},
         ]},
        {"key": "api", "name": "Demo — Public API",
         "description": "DEMO data. API contract checks against httpbin.org. Nothing here has run; "
                        "the history is generated.",
         "environments": [{"name": "httpbin", "base_url": "https://httpbin.org", "is_default": True,
                           "variables": {"BASE_URL": "https://httpbin.org"}}],
         "secrets": {},
         "suites": [{"key": "api_contract", "name": "API contract (demo)",
                     "description": "HTTP checks: JSON shape, echo, auth required, latency, headers.",
                     "cases": _api_cases(rng)}],
         "personas": [],
         "requirements": [
             {"external_key": "DEMO-201", "title": "The public API keeps its JSON contract", "cases": ["json", "echo"]},
         ]},
    ]

    runs: List[Dict[str, Any]] = []
    flaky_outcomes: List[bool] = []
    cluster_days = {days - 9, days - 8, days - 7, days - 6}   # four bad days, ending a week ago
    for d in range(days):
        day = first + timedelta(days=d)
        for proj in projects:
            suite = proj["suites"][0]
            for slot, (hour, trigger) in enumerate(((2, "schedule"), (14, "ci"))):
                at = datetime.combine(day, time(hour, rng.randint(0, 59), rng.randint(0, 59)))
                commit = "%07x" % rng.getrandbits(28) if trigger == "ci" else None
                mark = len(flaky_outcomes)
                results = []
                for case in suite["cases"]:
                    status, error, flaky, retries = "passed", None, False, 0
                    cluster = None
                    if case["key"] == "add_todo":
                        # Flaky: fails about a quarter of the time, often passing on retry.
                        roll = rng.random()
                        if roll < 0.12:
                            status, error = "failed", _FLAKY_ERROR
                        elif roll < 0.28:
                            flaky, retries = True, 1
                        flaky_outcomes.append(status == "passed")
                    elif case["key"] == "delay" and d in cluster_days:
                        status, error, cluster = "failed", _CLUSTER["error"], _CLUSTER["key"]
                    elif rng.random() < 0.015:
                        if case["key"] == "delay":   # the same slow endpoint: the same group
                            status, error, cluster = "failed", _CLUSTER["error"], _CLUSTER["key"]
                        else:
                            status, error = "failed", _RANDOM_ERRORS.get(case["key"], "step failed")
                    base = 900 if proj["key"] == "api" else 4200
                    results.append({"case": case["key"], "status": status, "error": error,
                                    "is_flaky": flaky, "retry_count": retries, "cluster": cluster,
                                    "duration_ms": float(base + rng.randint(0, base // 2)
                                                         + (3000 if cluster else 0))})
                if at > now:
                    # Today's later slot hasn't happened yet. Generated (and the
                    # rng consumed) anyway so the rest of the plan is unchanged.
                    del flaky_outcomes[mark:]
                    continue
                runs.append({"project": proj["key"], "suite": suite["key"], "case": None,
                             "created_at": at, "trigger": trigger, "git_commit": commit,
                             "git_branch": "main" if commit else None, "results": results,
                             "slot": slot})

    # Monitor: GET /json every 6 hours (seeded paused), one short outage.
    monitor = {"project": "api", "case": "json", "name": "Public API health (demo, paused)",
               "cron": "0 */6 * * *", "checks": []}
    outage_day = days - 12
    for d in range(days):
        day = first + timedelta(days=d)
        for hour in (0, 6, 12, 18):
            at = datetime.combine(day, time(hour, 1, rng.randint(0, 59)))
            up = not (d == outage_day and hour in (6, 12))
            duration = float(600 + rng.randint(0, 400))
            if at > now:
                continue
            monitor["checks"].append({"at": at, "up": up, "duration_ms": duration})

    cluster_hits = sum(1 for r in runs for x in r["results"] if x["cluster"])
    cluster_times = [r["created_at"] for r in runs if any(x["cluster"] for x in r["results"])]
    clusters = [{**_CLUSTER, "occurrence_count": cluster_hits,
                 "first_seen_at": min(cluster_times), "last_seen_at": max(cluster_times)}]
    # Every other failure is grouped the way app/tasks/triage_tasks.py groups
    # real ones (same compute_signature, per project), so Failure triage shows
    # what a real project with this history would — not only the scripted one.
    grouped: Dict[str, Dict[str, Any]] = {}

    def group(project: str, error: str, at: datetime) -> str:
        sig, title, category = compute_signature(error)
        key = f"{project}:{sig}"
        c = grouped.setdefault(key, {"project": project, "key": key, "category": category,
                                     "signature": sig, "title": title, "error": error,
                                     "occurrence_count": 0, "first_seen_at": at, "last_seen_at": at})
        c["occurrence_count"] += 1
        c["first_seen_at"], c["last_seen_at"] = min(c["first_seen_at"], at), max(c["last_seen_at"], at)
        return key

    for r in runs:
        for x in r["results"]:
            if x["status"] == "failed" and not x["cluster"]:
                x["cluster"] = group(r["project"], x["error"], r["created_at"])
    for chk in monitor["checks"]:
        chk["cluster"] = None if chk["up"] else group(monitor["project"], _MONITOR_ERROR, chk["at"])
    clusters += list(grouped.values())
    finding_at = datetime.combine(first + timedelta(days=days - 5), time(2, 30))
    findings = [{"project": "web", "category": "missing-header", "severity": "medium",
                 "title": "Missing Content-Security-Policy header",
                 "description": "The landing page response sets no Content-Security-Policy.",
                 "evidence": "GET https://example.com/ -> no content-security-policy header",
                 "target_url": "https://example.com/", "created_at": finding_at,
                 "fingerprint": "missing-header|Missing Content-Security-Policy header|example.com/"}]
    samples = len(flaky_outcomes)
    fails = samples - sum(flaky_outcomes)
    flaky_retries = sum(1 for r in runs for x in r["results"] if x["case"] == "add_todo" and x["is_flaky"])
    flake_score = round(min(1.0, (fails + flaky_retries) / max(samples, 1) * 1.6), 2)
    flakes = [{"project": "web", "case": "add_todo", "flake_score": flake_score,
               "sample_count": samples, "last_failure_message": _FLAKY_ERROR}]
    snapshots = []
    for d in range(days):
        day = first + timedelta(days=d)
        day_runs = [r for r in runs if r["project"] == "web" and r["created_at"].date() == day]
        res = [x for r in day_runs for x in r["results"]]
        score = round(flake_score * min(1.0, 0.4 + d / days), 2)
        snapshots.append({"project": "web", "day": day, "flaky_cases": 1 if score >= 0.33 else 0,
                          "avg_flake_score": score, "results_total": len(res),
                          "results_flaky": sum(1 for x in res if x["is_flaky"]),
                          "results_failed": sum(1 for x in res if x["status"] == "failed"),
                          "case": "add_todo", "case_score": score})
    deployments = [{"project": "web", "deployed_at": datetime.combine(first + timedelta(days=d), time(16, 0)),
                    "version": f"1.{d // 3}.0",
                    "status": "failed" if d == days - 8 else "success"}
                   for d in range(1, days, 3)
                   if datetime.combine(first + timedelta(days=d), time(16, 0)) <= now]
    return {"seed": seed, "today": today, "first_day": first, "projects": projects, "runs": runs,
            "monitor": monitor, "clusters": clusters, "findings": findings, "flakes": flakes,
            "flake_snapshots": snapshots, "deployments": deployments}


def plan_hosts(plan: Dict[str, Any]) -> List[str]:
    """Every URL host any seeded step or environment names (for the C5 check)."""
    from urllib.parse import urlsplit
    found: List[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            text = value.replace("{{env.BASE_URL}}", "")
            for token in text.split():
                if token.startswith(("http://", "https://")):
                    found.append(urlsplit(token).netloc)
        elif isinstance(value, dict):
            for v in value.values():
                visit(v)
        elif isinstance(value, list):
            for v in value:
                visit(v)
    for proj in plan["projects"]:
        visit(proj["environments"])
        visit(proj["suites"])
        visit(proj["personas"])
    for f in plan["findings"]:
        visit(f["target_url"])
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Guards (sync sessions: Celery delivery paths)
# ---------------------------------------------------------------------------

def is_demo_workspace_sync(session: Any, workspace_id: Optional[int]) -> bool:
    if not workspace_id or session is None:
        return False
    from app.models import Workspace
    ws = session.get(Workspace, workspace_id)
    # `is True`, not truthiness: only a real flag counts (a stub session's
    # placeholder object must not read as "demo").
    return ws is not None and getattr(ws, "is_demo", False) is True


def is_demo_project_sync(session: Any, project_id: Optional[int]) -> bool:
    """True when the project lives in a demo workspace: no delivery path may
    email, post to chat, page or call a webhook for it."""
    if not project_id or session is None:
        return False
    from app.models import Project
    project = session.get(Project, project_id)
    if project is None:
        return False
    workspace_id = getattr(project, "workspace_id", None)
    return isinstance(workspace_id, int) and is_demo_workspace_sync(session, workspace_id)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

async def find_demo(session: Any, user_id: int):
    from sqlmodel import select
    from app.models import Workspace
    return (await session.exec(select(Workspace).where(
        Workspace.is_demo == True, Workspace.demo_owner_id == user_id))).first()  # noqa: E712


async def _tenant_for(session: Any, user_id: int) -> Optional[int]:
    from sqlmodel import select
    from app.models import Tenant, UserWorkspace, Workspace
    row = (await session.exec(
        select(Workspace.tenant_id).join(UserWorkspace, UserWorkspace.workspace_id == Workspace.id)
        .where(UserWorkspace.user_id == user_id, Workspace.tenant_id.is_not(None),
               Workspace.is_demo == False).limit(1))).first()  # noqa: E712
    if row:
        return row
    owned = (await session.exec(select(Tenant.id).where(Tenant.owner_id == user_id).limit(1))).first()
    return owned


async def create_demo_workspace(session: Any, user: Any, *, seed: int = DEFAULT_SEED,
                                now: Optional[datetime] = None, request: Any = None) -> Dict[str, Any]:
    """Write the plan for `user`. Raises DemoExists if they already have one."""
    from sqlmodel import select
    from app.core.secrets import encrypt_secret
    from app.models import (
        Deployment, ExecutionMode, FailureCluster, FlakeCaseScoreDaily, FlakeDailySnapshot,
        FlakeRecord, MonitorCheck, Persona, ProjectEnvironment, ProjectSecret, Requirement,
        RequirementLink, RunTrigger, SecurityFinding, TestCase, TestCaseResult, TestRun,
        TestSchedule, TestStatus, TestSuite, User,
    )
    from app.services.audit import record as audit_record
    from app.services.workspace_service import workspace_service

    # Serialise concurrent clicks by the same person on their users row.
    await session.exec(select(User).where(User.id == user.id).with_for_update())
    existing = await find_demo(session, user.id)
    if existing:
        raise DemoExists(existing.id)

    now = now or datetime.utcnow()
    plan = build_demo_plan(seed, now.date(), now=now)
    ws = await workspace_service.create_workspace(
        name=DEMO_WORKSPACE_NAME, owner_id=user.id, session=session, commit=False,
        description="DEMO — generated sample data to explore TraceIQ. Delete it any time.",
        tenant_id=await _tenant_for(session, user.id))
    ws.is_demo = True
    ws.demo_owner_id = user.id
    session.add(ws)

    projects: Dict[str, Any] = {}
    cases: Dict[str, Dict[str, Any]] = {}
    suites: Dict[str, Any] = {}
    envs: Dict[str, Any] = {}
    for spec in plan["projects"]:
        project = await workspace_service.create_project(
            name=spec["name"], workspace_id=ws.id, creator_id=user.id, session=session,
            description=spec["description"], commit=False)
        # Belt and braces with the is_demo guard: notifications off at the source.
        project.notification_settings = {"email_enabled": False, "slack_enabled": False,
                                         "teams_enabled": False}
        session.add(project)
        projects[spec["key"]] = project
        for e in spec["environments"]:
            env = ProjectEnvironment(project_id=project.id, name=e["name"], base_url=e["base_url"],
                                     variables=dict(e["variables"]), is_default=e["is_default"])
            session.add(env)
            if e["is_default"]:
                envs[spec["key"]] = env
        for key, value in spec["secrets"].items():
            session.add(ProjectSecret(project_id=project.id, key=key, value_encrypted=encrypt_secret(value)))
        for p in spec["personas"]:
            session.add(Persona(workspace_id=ws.id, project_id=project.id, name=p["name"],
                                description=p["description"], login_steps=p["login_steps"],
                                auth_headers={}, created_by_id=user.id))
        cases[spec["key"]] = {}
        for sspec in spec["suites"]:
            suite = TestSuite(name=sspec["name"], description=sspec["description"],
                              execution_mode=ExecutionMode.SEPARATE, project_id=project.id,
                              created_by_id=user.id, updated_by_id=user.id,
                              created_at=now - timedelta(days=HISTORY_DAYS + 1))
            session.add(suite)
            await session.flush()
            suites[sspec["key"]] = suite
            for c in sspec["cases"]:
                case = TestCase(name=c["name"], description=c["description"], steps=c["steps"],
                                tags=c["tags"], priority=c["priority"], test_suite_id=suite.id,
                                project_id=project.id, created_by_id=user.id, updated_by_id=user.id,
                                created_at=suite.created_at)
                session.add(case)
                cases[spec["key"]][c["key"]] = case
    await session.flush()

    for spec in plan["projects"]:
        project = projects[spec["key"]]
        for r in spec["requirements"]:
            session.add(Requirement(project_id=project.id, source="jira", external_key=r["external_key"],
                                    title=r["title"], external_status="In Progress",
                                    description="DEMO requirement (not synced from a tracker).",
                                    created_by_id=user.id, last_synced_at=now))
            for ck in r["cases"]:
                session.add(RequirementLink(test_case_id=cases[spec["key"]][ck].id, project_id=project.id,
                                            ref=r["external_key"], source="jira", title=r["title"],
                                            created_by_id=user.id))

    clusters: Dict[str, Any] = {}
    for c in plan["clusters"]:
        row = FailureCluster(project_id=projects[c["project"]].id, signature=c["signature"],
                             title=c["title"], category=c["category"], status="open",
                             occurrence_count=c["occurrence_count"], first_seen_at=c["first_seen_at"],
                             last_seen_at=c["last_seen_at"], sample_error=c["error"],
                             created_at=c["first_seen_at"])
        session.add(row)
        clusters[c["key"]] = row

    # Monitor schedule first: its runs carry agent_id "schedule:<id>".
    mon = plan["monitor"]
    mproject = projects[mon["project"]]
    mcase = cases[mon["project"]][mon["case"]]
    last_check = mon["checks"][-1]
    schedule = TestSchedule(
        name=mon["name"], description="DEMO monitor, paused so it never calls httpbin.org. "
                                      "Its check history is generated.",
        project_id=mproject.id, test_suite_id=mcase.test_suite_id, test_case_id=mcase.id,
        cron_expression=mon["cron"], is_active=False, is_monitor=True, alert_after_failures=2,
        created_by_id=user.id, updated_by_id=user.id, monitor_state="up" if last_check["up"] else "down",
        last_alert_state="up", last_checked_at=last_check["at"], last_run_at=last_check["at"],
        slo_availability_target=99.0, slo_latency_p95_ms=1500, slo_window_days=30)
    session.add(schedule)
    await session.flush()

    run_rows: List[tuple] = []
    for r in plan["runs"]:
        project = projects[r["project"]]
        suite = suites[r["suite"]]
        passed = sum(1 for x in r["results"] if x["status"] == "passed")
        failed = len(r["results"]) - passed
        duration = sum(x["duration_ms"] for x in r["results"])
        run = TestRun(
            test_suite_id=suite.id, project_id=project.id, suite_name=suite.name,
            status=TestStatus.FAILED if failed else TestStatus.PASSED,
            total_tests=len(r["results"]), passed_tests=passed, failed_tests=failed,
            duration_ms=duration, browser="chromium", user_id=user.id,
            environment_id=envs[r["project"]].id, created_at=r["created_at"],
            finalized_at=r["created_at"] + timedelta(milliseconds=duration + 5000),
            triggered_by=RunTrigger.CI if r["trigger"] == "ci" else RunTrigger.SCHEDULE,
            git_commit=r["git_commit"], git_branch=r["git_branch"],
            git_repo="example/demo" if r["git_commit"] else None,
            selection_mode="full", selection_total=len(r["results"]),
            screenshots=[], execution_log=[], network_events=[])
        session.add(run)
        run_rows.append((run, r))
    mon_runs: List[tuple] = []
    for chk in mon["checks"]:
        run = TestRun(
            test_suite_id=mcase.test_suite_id, test_case_id=mcase.id, project_id=mproject.id,
            suite_name=suites["api_contract"].name, test_case_name=mcase.name,
            status=TestStatus.PASSED if chk["up"] else TestStatus.FAILED,
            total_tests=1, passed_tests=1 if chk["up"] else 0, failed_tests=0 if chk["up"] else 1,
            duration_ms=chk["duration_ms"], browser="chromium", user_id=user.id,
            environment_id=envs[mon["project"]].id, created_at=chk["at"],
            finalized_at=chk["at"] + timedelta(milliseconds=chk["duration_ms"] + 2000),
            triggered_by=RunTrigger.SCHEDULE, agent_id=f"schedule:{schedule.id}", selection_mode="case",
            selection_total=1, screenshots=[], execution_log=[], network_events=[])
        session.add(run)
        mon_runs.append((run, chk))
    await session.flush()

    # A cluster points at its newest failing run, whichever loop meets it last.
    newest: Dict[int, datetime] = {}

    def last_hit(cluster: Any, run: Any) -> None:
        if run.created_at >= newest.get(id(cluster), run.created_at):
            newest[id(cluster)] = run.created_at
            cluster.last_run_id = run.id

    result_rows: List[tuple] = []
    for run, r in run_rows:
        for x in r["results"]:
            case = cases[r["project"]][x["case"]]
            cluster = clusters.get(x["cluster"]) if x["cluster"] else None
            result = TestCaseResult(
                test_run_id=run.id, test_name=case.name, test_case_id=case.id,
                status=TestStatus(x["status"]), duration_ms=x["duration_ms"], error_message=x["error"],
                is_flaky=x["is_flaky"], retry_count=x["retry_count"],
                cluster_id=cluster.id if cluster else None, screenshots=[])
            session.add(result)
            result_rows.append((result, run, x))
            if cluster is not None:
                last_hit(cluster, run)
    for run, chk in mon_runs:
        session.add(TestCaseResult(
            test_run_id=run.id, test_name=mcase.name, test_case_id=mcase.id,
            status=TestStatus.PASSED if chk["up"] else TestStatus.FAILED, duration_ms=chk["duration_ms"],
            error_message=None if chk["up"] else _MONITOR_ERROR,
            cluster_id=clusters[chk["cluster"]].id if chk["cluster"] else None, screenshots=[]))
        if chk["cluster"]:
            last_hit(clusters[chk["cluster"]], run)
        session.add(MonitorCheck(schedule_id=schedule.id, run_id=run.id,
                                 status=TestStatus.PASSED if chk["up"] else TestStatus.FAILED,
                                 is_up=chk["up"], checked_at=chk["at"]))

    # The finding hangs off the web project's run nearest its timestamp.
    for f in plan["findings"]:
        project = projects[f["project"]]
        anchor = min((run for run, r in run_rows if r["project"] == f["project"]),
                     key=lambda run: abs((run.created_at - f["created_at"]).total_seconds()))
        session.add(SecurityFinding(
            run_id=anchor.id, project_id=project.id, scan_type="passive", category=f["category"],
            severity=f["severity"], title=f["title"], description=f["description"],
            evidence=f["evidence"], target_url=f["target_url"], created_at=f["created_at"],
            status="open", fingerprint=f["fingerprint"], due_at=f["created_at"] + timedelta(days=90)))
    flake_records: List[Any] = []
    for fl in plan["flakes"]:
        case = cases[fl["project"]][fl["case"]]
        flake = FlakeRecord(test_case_id=case.id, flake_score=fl["flake_score"],
                            sample_count=fl["sample_count"], first_observed_at=plan["runs"][0]["created_at"],
                            last_observed_at=plan["runs"][-1]["created_at"],
                            last_failure_message=fl["last_failure_message"], owner_user_id=user.id)
        session.add(flake)
        flake_records.append(flake)
    for snap in plan["flake_snapshots"]:
        project = projects[snap["project"]]
        session.add(FlakeDailySnapshot(project_id=project.id, day=snap["day"], flaky_cases=snap["flaky_cases"],
                                       avg_flake_score=snap["avg_flake_score"],
                                       results_total=snap["results_total"],
                                       results_flaky=snap["results_flaky"],
                                       results_failed=snap["results_failed"], computed_at=now))
        session.add(FlakeCaseScoreDaily(project_id=project.id, test_case_id=cases[snap["project"]][snap["case"]].id,
                                        day=snap["day"], flake_score=snap["case_score"],
                                        sample_count=snap["results_total"]))
    deployments: List[Any] = []
    for dep in plan["deployments"]:
        deployment = Deployment(project_id=projects[dep["project"]].id, environment_id=envs[dep["project"]].id,
                                version=dep["version"], status=dep["status"], deployed_at=dep["deployed_at"],
                                source="demo", created_by_id=user.id, created_at=dep["deployed_at"])
        session.add(deployment)
        deployments.append(deployment)
    await session.flush()

    # Everything the other pages read (members, proposals, sign-offs, security
    # registers, ...) is layered on by app/services/demo_seed.
    from app.services.demo_seed import DemoContext, seed_all
    await seed_all(DemoContext(
        session=session, user=user, ws=ws, now=now, seed=seed, plan=plan, projects=projects,
        suites=suites, cases=cases, envs=envs, run_rows=run_rows, mon_runs=mon_runs,
        result_rows=result_rows, clusters=clusters, schedule=schedule, deployments=deployments,
        flake_records=flake_records))

    await audit_record(session, entity_type="workspace", entity_id=ws.id, action="demo_create",
                       workspace_id=ws.id, user_id=user.id, request=request,
                       changes={"seed": seed, "runs": len(run_rows) + len(mon_runs)})
    await session.commit()
    return summary(ws, list(projects.values()), runs=len(run_rows) + len(mon_runs))


def summary(ws: Any, projects: List[Any], runs: Optional[int] = None) -> Dict[str, Any]:
    out = {"workspace_id": ws.id, "name": ws.name, "is_demo": True, "created_at": ws.created_at,
           "projects": [{"id": p.id, "name": p.name} for p in projects]}
    if runs is not None:
        out["runs_seeded"] = runs
    return out


async def describe_demo(session: Any, user_id: int) -> Optional[Dict[str, Any]]:
    from sqlmodel import select
    from app.models import Project
    ws = await find_demo(session, user_id)
    if not ws:
        return None
    projects = (await session.exec(select(Project).where(Project.workspace_id == ws.id)
                                   .order_by(Project.id))).all()
    return summary(ws, list(projects))


async def delete_demo_workspace(session: Any, user: Any, request: Any = None) -> Optional[Dict[str, Any]]:
    """Purge the user's demo through the ordinary workspace purge. None if they have none."""
    from app.services.purge import purge_workspace
    ws = await find_demo(session, user.id)
    if not ws:
        return None
    ws_id = ws.id
    report = await purge_workspace(session, ws_id, actor_id=user.id, request=request)
    # The demo's fake members are users rows, which a workspace purge never
    # removes (users outlive workspaces).
    from app.services.demo_seed.setup import delete_members
    removed = await delete_members(session, ws_id)
    await session.commit()
    out = report.as_dict()
    out["demo_members_deleted"] = removed
    return out
