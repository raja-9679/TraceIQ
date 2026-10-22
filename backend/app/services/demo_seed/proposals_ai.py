"""Demo datasets: agent proposals, failure investigation and the value/reporting
history built on them (see the package docstring for the rules).

* per-step evidence on every core result (`result_payload.step_evidence`,
  passed through the aggregator's own `normalise_step_evidence`) — feeds Flake
  trends' failing-step types, Selector health and the failure investigation;
* failure analysis v2 on the newest failed runs, built by the same
  deterministic pieces `analyze_case_failure` uses with no LLM, plus reviewer
  votes on the hypotheses (value report: AI accuracy);
* case proposals — pending update/create/delete for the review queue and a
  decided history (accepted / auto-applied / rejected) with the revisions and
  repair outcomes an applied proposal leaves; a pending selector-heal proposal
  ranked by the real candidate ranker;
* a targeted run (value report: CI time saved), feature flags on recent runs
  plus a flag catalogue, and a two-site benchmark group.

New runs here only ever pass: every failed demo result belongs to a failure
cluster, and these runs are not part of the clustered history.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

#: result_aggregator scores flakiness over the last 20 verdicts of a test
#: (FLAKE_WINDOW, a local there), so a real FlakeRecord never reports more.
FLAKE_WINDOW = 20
#: How many failed runs get a stored failure analysis.
ANALYSED_RUNS = 6
#: analysis_tasks.HISTORY_LIMIT — prior results considered per failure.
HISTORY_LIMIT = 20
DEFAULT_AGENT = "claude-code-demo"

# Index of the step that fails for each case's scripted errors, and what the
# page looked like when it did (sanitised, as the worker's excerpt is).
_FAILING_STEP = {"home": 2, "a11y": 1, "login": 4, "add_todo": 6, "cookie": 4,
                 "json": 0, "echo": 0, "auth": 0, "delay": 0, "headers": 0}
_DOM = {
    "home": ('<div><h1 data-traceiq-target="1">Example Domains</h1>'
             '<p>This domain is for use in illustrative examples.</p></div>', "target"),
    "login": ('<header><nav aria-label="Account"><span id="current-user" data-traceiq-target="1" '
              'data-testid="current-user" aria-label="Signed in user">bob</span>'
              '<button type="button">Sign out</button></nav></header>', "target"),
    # The flaky wait: the list was renamed, so the old locator finds nothing
    # and the worker can only excerpt the page around where it looked.
    "add_todo": ('<main><form id="todo-form"><input id="todo-input" name="title" value="[masked]">'
                 '<button id="todo-create-button" data-testid="add-todo">Add</button></form>'
                 '<ul id="todos" data-testid="todo-list"><li data-testid="todo-item">Buy milk</li></ul></main>',
                 "page"),
    "cookie": ('<header><span id="current-user" data-traceiq-target="1">bob</span></header>', "target"),
}
_API_PATHS = {"json": ("GET", "/json"), "echo": ("POST", "/anything"),
              "auth": ("GET", "/basic-auth/demo/demo"), "delay": ("GET", "/delay/1"),
              "headers": ("GET", "/headers")}


def _project_key(ctx, run) -> str:
    for key, project in ctx.projects.items():
        if project.id == run.project_id:
            return key
    raise KeyError(run.project_id)


# ---------------------------------------------------------------------------
# Per-step evidence
# ---------------------------------------------------------------------------

def raw_step_evidence(case_key: str, steps: List[dict], status: str, error: Optional[str],
                      duration_ms: float, base_url: str, rng) -> List[Dict[str, Any]]:
    """What a worker at capture level `standard` sends for one result."""
    n = len(steps)
    fail_at = _FAILING_STEP.get(case_key, n - 1) if status == "failed" else None
    per_step = max(50, int(duration_ms / max(n, 1)))
    out: List[Dict[str, Any]] = []
    for i, step in enumerate(steps):
        entry: Dict[str, Any] = {"index": i, "type": step.get("type"), "step_id": step.get("id")}
        if step.get("selector"):
            entry["selector"] = step["selector"]
        if fail_at is None or i < fail_at:
            entry.update(status="passed", duration_ms=per_step + rng.randint(-40, 40))
        elif i == fail_at:
            entry.update(status="failed", duration_ms=per_step * 2, error=error)
            if case_key in _DOM:
                entry["dom"], entry["dom_scope"] = _DOM[case_key]
            if case_key == "add_todo" and rng.random() < 0.5:
                # On some failing attempts the create call was slow too.
                entry["requests"] = [{"method": "POST", "url": f"{base_url}/api/todos", "status": 504,
                                      "resource_type": "fetch"}]
                entry["console"] = [{"type": "error", "text": "Failed to load resource: the server "
                                                              "responded with a status of 504 (Gateway Timeout)"}]
            elif case_key in _API_PATHS:
                method, path = _API_PATHS[case_key]
                req: Dict[str, Any] = {"method": method, "url": base_url + path, "resource_type": "fetch"}
                last = (error or "").rsplit(" ", 1)[-1]
                if case_key == "delay":
                    req["failure"] = "Timeout 3000ms exceeded"
                elif last.isdigit():
                    req["status"] = int(last)
                else:
                    req["status"] = 200
                entry["requests"] = [req]
        else:
            entry["status"] = "skipped"
        out.append(entry)
    return out


async def _seed_step_evidence(ctx) -> None:
    from app.services.demo_workspace import _FLAKY_ERROR as FLAKY_ERROR
    from app.tasks.result_aggregator import _attempts, normalise_step_evidence
    rng = ctx.rng_for("step_evidence")
    for result, run, x in ctx.result_rows:
        project = _project_key(ctx, run)
        case = ctx.cases[project][x["case"]]
        raw = raw_step_evidence(x["case"], list(case.steps or []), x["status"], x["error"],
                                x["duration_ms"], ctx.envs[project].base_url, rng)
        payload = dict(result.result_payload or {})
        payload["step_evidence"] = normalise_step_evidence(raw, run.id)
        if x["is_flaky"] and x["retry_count"]:
            # A retried pass: the evidence is the passing attempt's (as the
            # worker sends it); the failed try is in `attempts`.
            first = int(x["duration_ms"] * 0.8)
            payload["attempts"] = _attempts([
                {"attempt": 1, "status": "failed", "error": FLAKY_ERROR, "duration_ms": first},
                {"attempt": 2, "status": "passed", "duration_ms": int(x["duration_ms"]) - first}])
        result.result_payload = payload
        ctx.session.add(result)


# ---------------------------------------------------------------------------
# Failure analysis v2 (no LLM) + reviewer votes
# ---------------------------------------------------------------------------

def _analyse(ctx, run, result, case, history_rows) -> Any:
    """`failure_analysis.analyze_case_failure` up to its provider branch — the
    report a null provider returns. Assembled here rather than called so a
    demo can never reach whatever LLM the instance has configured."""
    from app.schemas.failure_report import ClusterRef, HistoryEntry, Hypothesis
    from app.services import failure_analysis as fa
    from app.services import failure_investigation as inv
    from app.services.failure_signature import compute_signature

    steps = [dict(s) for s in (case.steps or [])]
    error = result.error_message or ""
    signature = compute_signature(error, result.test_name)[0]
    entries = []
    for prior, prior_run, px in history_rows:
        failed = px["status"] in ("failed", "error")
        entries.append(HistoryEntry(
            run_id=prior_run.id, result_id=prior.id, status=px["status"],
            git_commit=prior_run.git_commit, git_branch=prior_run.git_branch,
            created_at=prior_run.created_at.isoformat(),
            error_excerpt=((px["error"] or "")[:200] or None) if failed else None,
            same_signature=failed and compute_signature(px["error"], prior.test_name)[0] == signature))
    cluster = None
    for c in ctx.clusters.values():
        if c.id == result.cluster_id:
            cluster = ClusterRef(id=c.id, title=c.title[:200], status=c.status,
                                 occurrence_count=c.occurrence_count)
    history, change = inv.summarise_history(entries, run.git_commit, run.id, run.git_repo, cluster)

    failing_idx, failing_step = fa._find_failing_step(steps, error)
    index = inv.build_evidence_index(step_evidence=(result.result_payload or {}).get("step_evidence"),
                                     error_message=error, failing_step_guess=failing_idx)
    if index.has_steps and index.failing is not None:
        failing_idx = index.failing["index"]
        failing_step = steps[failing_idx] if 0 <= failing_idx < len(steps) else failing_step
    inv.add_history_items(index, history)
    evidence = fa.FailureEvidence(error_message=error[:2000], failing_step_index=failing_idx,
                                  failing_step=failing_step,
                                  selector=(failing_step or {}).get("selector"))
    base = fa.heuristic_report(error, evidence)
    observed = inv.observe(index, history, change)
    hypotheses = inv.heuristic_hypotheses(
        index, error,
        Hypothesis(category=base.root_cause_category, fix_target=base.fix_target,
                   summary=base.summary, confidence=base.confidence, source="heuristic"),
        history, change)
    return fa._with_investigation(base, hypotheses, observed, index, history, change,
                                  result.id, result.test_name)


async def _seed_analyses(ctx) -> List[Tuple[Any, Any, Any]]:
    """Analyse the newest failed runs; (run, result, report) per analysed result."""
    from app.services.failure_analysis import build_run_rollup
    by_run: Dict[int, List[tuple]] = {}
    for result, run, x in ctx.result_rows:
        by_run.setdefault(run.id, []).append((result, x))
    failed_runs = sorted({run.id: run for _, run, x in ctx.result_rows
                          if x["status"] == "failed" and run.created_at <= ctx.now}.values(),
                         key=lambda r: r.created_at, reverse=True)[:ANALYSED_RUNS]
    analysed = []
    for run in failed_runs:
        project = _project_key(ctx, run)
        reports = []
        failed = [(res, x) for res, x in by_run[run.id] if x["status"] == "failed"]
        for result, x in failed[:5]:
            prior = sorted(((res, r, px) for res, r, px in ctx.result_rows
                            if r.id < run.id and r.project_id == run.project_id and px["case"] == x["case"]),
                           key=lambda t: t[1].id, reverse=True)[:HISTORY_LIMIT]
            report = _analyse(ctx, run, result, ctx.cases[project][x["case"]], prior)
            result.ai_analysis = report.model_dump_json()
            ctx.session.add(result)
            reports.append(report)
            analysed.append((run, result, report))
        run.ai_analysis = json.loads(build_run_rollup(reports, failed_total=len(failed)).model_dump_json())
        ctx.session.add(run)
    return analysed


async def _seed_votes(ctx, analysed) -> None:
    from app.models import HypothesisFeedback
    from app.services.value_report import hypothesis_key
    voters = [ctx.user] + [m for m in ctx.members.values() if getattr(m, "id", None)][:1]
    for n, (run, result, report) in enumerate(analysed):
        hyps = [h.model_dump() for h in report.hypotheses]
        if not hyps:
            continue
        # Mostly right, sometimes not: a reviewed share should read like a
        # real pilot's, not 100 %.
        votes = [(0, "incorrect" if n % 4 == 3 else "correct")]
        if len(hyps) > 1 and n % 3 == 0:
            votes.append((1, "incorrect"))
        for index, verdict in votes:
            for user in voters[: 2 if n == 0 else 1]:
                at = ctx.past(run.created_at + timedelta(hours=1 + n))
                ctx.session.add(HypothesisFeedback(
                    project_id=run.project_id, run_id=run.id, result_id=result.id,
                    run_created_at=run.created_at, hypothesis_index=index,
                    hypothesis_key=hypothesis_key(hyps[index]),
                    category=str(hyps[index].get("category") or "unknown")[:40],
                    source=str(hyps[index].get("source") or "heuristic")[:80],
                    verdict=verdict, user_id=user.id, created_at=at, updated_at=at))


# ---------------------------------------------------------------------------
# Case + heal proposals
# ---------------------------------------------------------------------------

def _latest_run_with(ctx, project: str, case_key: str, status: str) -> Optional[Any]:
    best = None
    for _, run, x in ctx.result_rows:
        if (x["case"] == case_key and x["status"] == status and run.created_at <= ctx.now
                and _project_key(ctx, run) == project
                and (best is None or run.created_at > best.created_at)):
            best = run
    return best


async def _revise(ctx, case, source: str, at: datetime, user_id: Optional[int],
                  agent_id: Optional[str]) -> None:
    """The real revision writer, back-dated to when the change happened."""
    from app.services.case_revisions import record_revision
    await ctx.session.flush()
    rev = await record_revision(ctx.session, case, source, user_id=user_id, agent_id=agent_id)
    if rev is not None:
        rev.created_at = at
    await ctx.session.flush()


async def _seed_case_proposals(ctx) -> None:
    from app.models import CaseProposal, CaseProposalAction, RepairOutcome
    agent = ctx.extra.get("agent_id", DEFAULT_AGENT)
    # The setup module's agent session (if any) filed today's pending queue;
    # the decided history predates it.
    agent_session = ctx.extra.get("agent_session")
    web, api = ctx.projects["web"], ctx.projects["api"]
    wcases, acases = ctx.cases["web"], ctx.cases["api"]
    reviewer = next((m for m in ctx.members.values() if getattr(m, "id", None)), ctx.user)

    def proposal(project, action, *, suite=None, target=None, payload, rationale, confidence,
                 created, run=None, session_id=None, **kw) -> Any:
        # Shaped as POST /case-proposals writes it: the human behind the
        # agent's key is created_by_id (separation of duties).
        p = CaseProposal(project_id=project.id, test_suite_id=suite.id if suite else None,
                         target_case_id=target.id if target else None, action=action,
                         payload=payload, rationale=rationale, ai_confidence=confidence,
                         agent_id=agent, created_by_agent_id=agent, agent_session_id=session_id,
                         created_by_id=ctx.user.id, source_run_id=run.id if run else None,
                         created_at=created, **kw)
        ctx.session.add(p)
        return p

    # --- decided history (value report: outcomes + revert check) ---
    # Accepted by a person: the flaky wait gets more time. The case really
    # changes, with a revision either side as an accept leaves.
    todo = wcases["add_todo"]
    await _revise(ctx, todo, "create", todo.created_at, ctx.user.id, None)
    steps = copy.deepcopy(list(todo.steps))
    steps[-1] = {**steps[-1], "params": {**(steps[-1].get("params") or {}), "timeout": 10000}}
    created, decided = ctx.ago(days=10, hours=3), ctx.ago(days=9, hours=20)
    p = proposal(web, CaseProposalAction.UPDATE, target=todo, payload={"steps": steps},
                 rationale="#todo-list li appears only after the create request returns; on slow runs "
                           "that takes longer than the 5 s default. Wait up to 10 s on this step only.",
                 confidence=0.78, created=created, run=_latest_run_with(ctx, "web", "add_todo", "failed"),
                 status="accepted", decided_at=decided, decided_by_id=reviewer.id,
                 decision_note="Matches what the traces show.")
    await ctx.session.flush()
    todo.steps = steps
    todo.updated_at, todo.updated_by_id = decided, reviewer.id
    todo.last_human_reviewed_at, todo.last_human_reviewed_by_id = decided, reviewer.id
    ctx.session.add(todo)
    await _revise(ctx, todo, "proposal", decided, reviewer.id, agent)
    ctx.session.add(RepairOutcome(project_id=web.id, proposal_kind="case", proposal_id=p.id,
                                  event="accepted", validated=False, user_id=reviewer.id, created_at=decided))

    # Auto-applied under the workspace threshold: no human decider.
    echo = acases["echo"]
    await _revise(ctx, echo, "create", echo.created_at, ctx.user.id, None)
    steps = copy.deepcopy(list(echo.steps))
    steps[0]["params"] = {**steps[0]["params"], "assertions": list(steps[0]["params"]["assertions"]) + [
        {"type": "json-path", "path": "headers.Content-Type", "operator": "contains",
         "value": "application/json"}]}
    created = ctx.ago(days=6, hours=5)
    p = proposal(api, CaseProposalAction.UPDATE, target=echo, payload={"steps": steps},
                 rationale="Consumers parse this response as JSON; check the echo shows the body was "
                           "sent as JSON, not just that a body came back.",
                 confidence=0.93, created=created, status="accepted", decided_at=created,
                 decided_by_id=None, decision_note="auto-applied: confidence 0.93 >= workspace threshold 0.90")
    await ctx.session.flush()
    echo.steps = steps
    echo.updated_at = created
    ctx.session.add(echo)
    await _revise(ctx, echo, "proposal", created, None, agent)
    ctx.session.add(RepairOutcome(project_id=api.id, proposal_kind="case", proposal_id=p.id,
                                  event="auto_applied", validated=False, created_at=created))

    # Rejected, with the reviewer's reason.
    home = wcases["home"]
    created, decided = ctx.ago(days=15, hours=2), ctx.ago(days=14, hours=22)
    loose = copy.deepcopy(list(home.steps))
    loose[1] = {**loose[1], "value": "Example"}
    p = proposal(web, CaseProposalAction.UPDATE, target=home, payload={"steps": loose},
                 rationale="The title check failed once; loosen it to a prefix match.",
                 confidence=0.55, created=created, run=_latest_run_with(ctx, "web", "home", "failed"),
                 status="rejected", decided_at=decided, decided_by_id=reviewer.id,
                 decision_note="The title is part of the contract — that failure was a bad deploy, not the test.")
    await ctx.session.flush()
    ctx.session.add(RepairOutcome(project_id=web.id, proposal_kind="case", proposal_id=p.id,
                                  event="rejected", validated=False, user_id=reviewer.id, created_at=decided))
    created, decided = ctx.ago(days=20, hours=4), ctx.ago(days=19, hours=23)
    proposal(api, CaseProposalAction.CREATE, suite=ctx.suites["api_contract"],
             payload={"name": "GET /status/418 is a teapot", "tags": ["contract"], "priority": "low",
                      "steps": [{"type": "http-request", "value": "{{env.BASE_URL}}/status/418",
                                 "params": {"method": "GET", "assertions": [{"type": "status", "value": 418}]}}]},
             rationale="Uncovered endpoint found while mapping the app surface.", confidence=0.61,
             created=created, status="rejected", decided_at=decided, decided_by_id=reviewer.id,
             decision_note="Not part of the API we ship.")

    # --- pending review queue ---
    session_id = getattr(agent_session, "agent_session_id", None)
    if agent_session is not None:
        agent_session.proposals = (agent_session.proposals or 0) + 3
        ctx.session.add(agent_session)
    login = wcases["login"]
    new_login = copy.deepcopy(list(login.steps))
    new_login[-1] = {**new_login[-1], "selector": '[data-testid="current-user"]'}
    proposal(web, CaseProposalAction.UPDATE, target=login, payload={"steps": new_login},
             rationale="The header now exposes data-testid=\"current-user\"; #current-user is an id the "
                       "redesign is likely to drop. Same assertion, sturdier locator.",
             confidence=0.82, created=ctx.ago(hours=5), run=_latest_run_with(ctx, "web", "login", "passed"),
             session_id=session_id)
    proposal(web, CaseProposalAction.CREATE, suite=ctx.suites["web_smoke"],
             payload={"name": "Delete a todo", "tags": ["regression"], "priority": "medium",
                      "description": "Creates a todo, deletes it, and checks it is gone.",
                      "steps": [
                          {"type": "goto", "value": "{{env.BASE_URL}}/login"},
                          {"type": "fill", "selector": "#login-username", "value": "alice"},
                          {"type": "fill", "selector": "#login-password", "value": "{{secret.ALICE_PASSWORD}}"},
                          {"type": "click", "selector": "#login-submit"},
                          {"type": "fill", "selector": "#todo-input", "value": "Delete me {{unique.todo}}"},
                          {"type": "click", "selector": "#todo-create-button"},
                          {"type": "click", "selector": '[data-testid="todo-item"] >> text=Delete'},
                          {"type": "expect-hidden", "selector": '[data-testid="todo-item"] >> text=Delete me'},
                      ]},
             rationale="Deleting a todo has no test, and the change to the todo routes touched its handler.",
             confidence=0.71, created=ctx.ago(hours=4), session_id=session_id)
    proposal(api, CaseProposalAction.DELETE, target=acases["headers"], payload={},
             rationale="Redundant: POST /anything already proves custom headers reach the server, and "
                       "this case has never failed for a reason of its own.",
             confidence=0.58, created=ctx.ago(hours=3), session_id=session_id)


async def _seed_heal(ctx) -> None:
    from app.models import SelectorHealProposal
    from app.services.heal_candidates import choose_candidate, rank_candidates
    todo = ctx.cases["web"]["add_todo"]
    step = list(todo.steps)[-1]
    dom, _ = _DOM["add_todo"]
    intent = "the first item in the todo list"
    # Ranked against the deterministic strategies on the same snapshot, as
    # heal_tasks does with the LLM's pick.
    ranked = rank_candidates(dom, step["selector"], intent, '[data-testid="todo-item"]', 0.86)
    chosen = choose_candidate(ranked)
    if chosen is None:
        return
    run = _latest_run_with(ctx, "web", "add_todo", "failed")
    ctx.session.add(SelectorHealProposal(
        test_case_id=todo.id, step_id=step["id"], old_selector=step["selector"],
        new_selector=chosen["selector"], intent=intent, confidence=chosen["score"],
        source_run_id=run.id if run else None, status="pending",
        rationale="#todo-list no longer exists; the list is now ul#todos with data-testid "
                  "attributes on each item.",
        created_at=ctx.ago(hours=6), candidates=ranked, chosen_selector=chosen["selector"]))


# ---------------------------------------------------------------------------
# Runs: targeted, flags, benchmark
# ---------------------------------------------------------------------------

def _run_like(ctx, project_key: str, suite, at: datetime, **kw):
    from app.models import TestRun, TestStatus
    return TestRun(test_suite_id=suite.id, project_id=ctx.projects[project_key].id, suite_name=suite.name,
                   status=TestStatus.PASSED, browser="chromium", user_id=ctx.user.id,
                   environment_id=ctx.envs[project_key].id, created_at=at,
                   screenshots=[], execution_log=[], network_events=[], **kw)


async def _seed_targeted(ctx) -> None:
    """An agent ran only the cases its diff touched (value report: CI time
    saved against the newest earlier full run of the suite). Like
    POST /runs with case_ids: one run per case, one batch."""
    from app.models import RunTrigger, TestCaseResult, TestStatus
    suite = ctx.suites["web_smoke"]
    total = len(ctx.cases["web"])
    rng = ctx.rng_for("targeted")
    start = ctx.ago(hours=2, minutes=10)
    commit = "%07x" % rng.getrandbits(28)
    for n, key in enumerate(("login", "add_todo")):
        case = ctx.cases["web"][key]
        at = start + timedelta(seconds=20 * n)
        duration = float(4200 + rng.randint(0, 1800))
        run = _run_like(ctx, "web", suite, at, test_case_id=case.id, test_case_name=case.name,
                        total_tests=1, passed_tests=1, failed_tests=0, duration_ms=duration,
                        finalized_at=at + timedelta(milliseconds=duration + 4000),
                        triggered_by=RunTrigger.API_AGENT, agent_id=ctx.extra.get("agent_id", DEFAULT_AGENT),
                        git_commit=commit, git_branch="feature/todo-routes", git_repo="example/demo",
                        selection_mode="targeted", selection_total=total)
        ctx.session.add(run)
        await ctx.session.flush()
        ctx.session.add(TestCaseResult(test_run_id=run.id, test_name=case.name, test_case_id=case.id,
                                       status=TestStatus.PASSED, duration_ms=duration, screenshots=[]))


async def _seed_flags(ctx) -> None:
    from app.models import FlagCatalogEntry
    web = ctx.projects["web"]
    for name, values, desc in (
            ("checkout_v2", ["on", "off", "holdout"], "New checkout flow (A/B); 'holdout' is the 5 % control."),
            ("new_onboarding", ["true", "false"], "Guided first-run tour.")):
        ctx.session.add(FlagCatalogEntry(project_id=web.id, name=name, values=values, description=desc,
                                         created_by_id=ctx.user.id, created_at=ctx.ago(days=20),
                                         updated_at=ctx.ago(days=20)))
    # The ten newest CI runs declared the flags they ran with. Nothing ever
    # exercised the holdout or onboarding off — the gaps the report is for.
    ci_runs = sorted((run for run, r in ctx.run_rows if r["project"] == "web" and r["trigger"] == "ci"
                      and run.created_at <= ctx.now), key=lambda r: r.created_at)
    for n, run in enumerate(ci_runs[-10:]):
        run.flags = {"checkout_v2": "on" if n % 3 else "off", "new_onboarding": True}
        ctx.session.add(run)


async def _seed_benchmark(ctx) -> None:
    from app.models import BenchmarkGroup, RunTrigger, TestCaseResult, TestStatus
    from app.services.benchmarks import clean_targets
    suite = ctx.suites["web_smoke"]
    rng = ctx.rng_for("benchmark")
    targets = clean_targets([{"label": "Local todolite", "base_url": "http://localhost:8080"},
                             {"label": "Public (example.com)", "base_url": "https://example.com"}])
    at = ctx.ago(days=2, hours=1)
    group = BenchmarkGroup(project_id=ctx.projects["web"].id, test_suite_id=suite.id,
                           name="Web smoke: local vs public", targets=targets,
                           browser="chromium", created_by_id=ctx.user.id, created_at=at)
    ctx.session.add(group)
    await ctx.session.flush()
    cases = list(ctx.cases["web"].values())
    for n, t in enumerate(targets):
        slow = 1.0 if n == 0 else 1.6
        durations = [float(int((3500 + rng.randint(0, 1500)) * slow)) for _ in cases]
        start = at + timedelta(seconds=5 * n)
        run = _run_like(ctx, "web", suite, start, total_tests=len(cases), passed_tests=len(cases),
                        failed_tests=0, duration_ms=sum(durations),
                        finalized_at=start + timedelta(milliseconds=sum(durations) + 5000),
                        triggered_by=RunTrigger.HUMAN, target_url=t["base_url"],
                        benchmark_group_id=group.id, selection_mode="full",
                        selection_total=len(cases))
        ctx.session.add(run)
        await ctx.session.flush()
        for case, d in zip(cases, durations):
            ctx.session.add(TestCaseResult(test_run_id=run.id, test_name=case.name, test_case_id=case.id,
                                           status=TestStatus.PASSED, duration_ms=d, screenshots=[]))


# ---------------------------------------------------------------------------

async def seed(ctx) -> None:
    for flake in ctx.flake_records:
        flake.sample_count = min(flake.sample_count, FLAKE_WINDOW)
        ctx.session.add(flake)
    await _seed_step_evidence(ctx)
    await ctx.session.flush()
    analysed = await _seed_analyses(ctx)
    await _seed_votes(ctx, analysed)
    await _seed_case_proposals(ctx)
    await _seed_heal(ctx)
    await _seed_targeted(ctx)
    await _seed_flags(ctx)
    await _seed_benchmark(ctx)
