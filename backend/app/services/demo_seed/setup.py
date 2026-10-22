"""Demo datasets: workspace setup and administration pages.

Members, a team with a project grant, a pending invitation, API keys, the
API project's OpenAPI history, an outbound webhook with its delivery log, a
(disabled) Jira connection, ~30 days of LLM usage, a few policy denials and a
coding-agent session.

Fake members are real `users` rows (membership needs one), which the workspace
purge never touches — users outlive workspaces. Their e-mail names the demo
workspace (`<name>.demo<ws id>@demo.traceiq.invalid`) so `delete_members`
can find exactly this demo's people after the purge and remove them. They have
a random password nobody knows, and `.invalid` can never receive a reset mail.
"""
from __future__ import annotations

import copy
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

MEMBER_DOMAIN = "demo.traceiq.invalid"
AGENT_ID = "claude-code"

_MEMBERS = (
    # key, full name, workspace role, last login (days ago)
    ("maya", "Maya Rao (demo)", "Workspace Admin", 1),
    ("li", "Li Chen (demo)", "Workspace Member", 3),
    ("sam", "Sam Okafor (demo)", "Workspace Member", 12),
)


def member_email(key: str, workspace_id: int) -> str:
    return f"{key}.demo{workspace_id}@{MEMBER_DOMAIN}"


def _member_emails(workspace_id: int) -> List[str]:
    # Exact addresses, not a LIKE pattern: a wildcard would also catch any
    # self-registered account that happened to end in this demo's domain.
    return [member_email(key, workspace_id) for key, *_ in _MEMBERS]


_SPEC_V1: Dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Demo public API (httpbin)", "version": "1.0.0"},
    "servers": [{"url": "https://httpbin.org"}],
    "paths": {
        "/json": {"get": {"operationId": "getJson", "summary": "A sample JSON document",
                          "responses": {"200": {"description": "OK", "content": {"application/json": {
                              "schema": {"type": "object", "required": ["slideshow"],
                                         "properties": {"slideshow": {"type": "object"}}}}}}}}},
        "/anything": {"post": {"operationId": "echoAnything", "summary": "Echo the request",
                               "requestBody": {"content": {"application/json": {"schema": {
                                   "type": "object", "properties": {"order": {"type": "string"}}}}}},
                               "responses": {"200": {"description": "OK", "content": {"application/json": {
                                   "schema": {"type": "object", "required": ["json"],
                                              "properties": {"json": {"type": "object"}}}}}}}}},
        "/headers": {"get": {"operationId": "getHeaders", "summary": "Echo request headers",
                             "responses": {"200": {"description": "OK"}}}},
        "/basic-auth/{user}/{passwd}": {"get": {
            "operationId": "basicAuth", "summary": "Requires HTTP basic auth",
            "parameters": [{"name": "user", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": "passwd", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "Authorised"}, "401": {"description": "Unauthorised"}}}},
        "/ip": {"get": {"operationId": "getIp", "summary": "Caller's IP address",
                        "responses": {"200": {"description": "OK"}}}},
    },
}


def _spec_v2() -> Dict[str, Any]:
    """v1.1: /delay added, /ip removed and the echo body gains a required
    field — so the version diff shows an addition and two breaking changes."""
    spec = copy.deepcopy(_SPEC_V1)
    spec["info"]["version"] = "1.1.0"
    del spec["paths"]["/ip"]
    spec["paths"]["/delay/{n}"] = {"get": {
        "operationId": "getDelayed", "summary": "Respond after n seconds",
        "parameters": [{"name": "n", "in": "path", "required": True, "schema": {"type": "integer"}}],
        "responses": {"200": {"description": "OK"}}}}
    body = spec["paths"]["/anything"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    body["required"] = ["order"]
    return spec


async def seed(ctx) -> None:
    rng = ctx.rng_for("setup")
    await _members_and_team(ctx, rng)
    await _api_keys(ctx, rng)
    await _openapi_versions(ctx)
    await _webhook(ctx, rng)
    _tracker(ctx)
    _agent_session(ctx, rng)
    events = _llm_usage(ctx, rng)
    _policy_denials(ctx)
    _usage_records(ctx, events)


# ---------------------------------------------------------------------------

async def _role_id(ctx, name: str):
    from app.services.rbac_service import rbac_service
    role = await rbac_service.get_role_by_name(ctx.session, name)
    return role.id if role else None


async def _members_and_team(ctx, rng) -> None:
    from app.core.auth import get_password_hash
    from app.models import (
        Team, TeamProjectAccess, User, UserProjectAccess, UserTeam, UserWorkspace,
        WorkspaceInvitation,
    )
    s, ws = ctx.session, ctx.ws
    for key, name, role, login_days in _MEMBERS:
        user = User(email=member_email(key, ws.id), full_name=name,
                    # Random and never shown: these people cannot sign in.
                    hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                    is_verified=True, last_login_at=ctx.ago(days=login_days, hours=rng.randint(1, 9)))
        s.add(user)
        await s.flush()
        s.add(UserWorkspace(user_id=user.id, workspace_id=ws.id,
                            role="admin" if role == "Workspace Admin" else "member",
                            role_id=await _role_id(ctx, role)))
        ctx.members[key] = user

    team = Team(name="QA engineers", workspace_id=ws.id)
    s.add(team)
    await s.flush()
    for key in ("maya", "li"):
        s.add(UserTeam(user_id=ctx.members[key].id, team_id=team.id))
    s.add(TeamProjectAccess(team_id=team.id, project_id=ctx.projects["web"].id, access_level="editor",
                            role_id=await _role_id(ctx, "Project Editor")))
    # Sam is a plain member with a direct read-only grant on the API project.
    s.add(UserProjectAccess(user_id=ctx.members["sam"].id, project_id=ctx.projects["api"].id,
                            access_level="viewer", role_id=await _role_id(ctx, "Project Viewer")))
    ctx.extra["team"] = team

    sent = ctx.ago(days=2, hours=3)
    s.add(WorkspaceInvitation(
        email=f"priya.demo{ws.id}@{MEMBER_DOMAIN}", workspace_id=ws.id, role="member",
        invited_by_id=ctx.user.id,
        # Never handed out (a real token is only in the inviting POST's
        # response), and the address can never sign up.
        token=f"demo{ctx.ws.id}-{uuid.UUID(int=rng.getrandbits(128)).hex}",
        expires_at=sent + timedelta(days=7), created_at=sent,
        project_id=ctx.projects["web"].id, project_role="viewer"))


def _dead_key_hash(rng) -> str:
    # Not the hash of any `tiq_` key: there is no raw key to leak or reuse.
    return hashlib.sha256(f"demo-key-without-a-secret:{rng.getrandbits(64)}".encode()).hexdigest()


async def _api_keys(ctx, rng) -> None:
    from app.models import ApiKey
    member_role = await _role_id(ctx, "Workspace Member")
    ctx.session.add(ApiKey(
        workspace_id=ctx.ws.id, project_id=ctx.projects["web"].id, name="GitHub Actions (rotated)",
        prefix="tiq_demo" + "%04x" % rng.getrandbits(16), hashed_key=_dead_key_hash(rng),
        role_id=member_role, created_by_id=ctx.members["maya"].id, created_at=ctx.ago(days=28),
        last_used_at=ctx.ago(days=4, hours=2), revoked_at=ctx.ago(days=4)))
    ctx.session.add(ApiKey(
        workspace_id=ctx.ws.id, name="Claude Code — trial week",
        prefix="tiq_demo" + "%04x" % rng.getrandbits(16), hashed_key=_dead_key_hash(rng),
        role_id=member_role, created_by_id=ctx.user.id, created_at=ctx.ago(days=26),
        last_used_at=ctx.ago(days=19, hours=5), expires_at=ctx.ago(days=19)))


async def _openapi_versions(ctx) -> None:
    """Through the real store path, so ProjectApiSpec + the numbered history
    are exactly what PUT /projects/{id}/openapi-spec writes."""
    from sqlmodel import select
    from app.api.developer import store_project_spec_versioned
    from app.models import ProjectApiSpecVersion
    project = ctx.projects["api"]
    url = "https://httpbin.org/spec.json"
    _, v1 = await store_project_spec_versioned(ctx.session, project.id, _SPEC_V1, ctx.user.id, url, "put")
    await ctx.session.flush()
    row, v2 = await store_project_spec_versioned(ctx.session, project.id, _spec_v2(),
                                                 ctx.members["li"].id, url, "put")
    row.updated_at = ctx.ago(days=6)
    for version, at in ((v1, ctx.ago(days=27)), (v2, ctx.ago(days=6))):
        v = (await ctx.session.exec(select(ProjectApiSpecVersion).where(
            ProjectApiSpecVersion.project_id == project.id, ProjectApiSpecVersion.version == version))).one()
        v.created_at = at
        ctx.session.add(v)


def _trigger(run) -> str:
    return str(getattr(run.triggered_by, "value", run.triggered_by))


async def _webhook(ctx, rng) -> None:
    from app.core.secrets import encrypt_secret
    from app.models import TestStatus, WebhookDelivery, WorkspaceWebhook
    hook = WorkspaceWebhook(
        workspace_id=ctx.ws.id, name="CI status (demo)", url="https://hooks.example.invalid/traceiq/ci",
        secret=encrypt_secret(secrets.token_urlsafe(32)), event_filter="run.failed,run.passed",
        # A demo workspace never delivers (is_demo guard); inactive as well.
        is_active=False, created_by_id=ctx.user.id, created_at=ctx.ago(days=29))
    ctx.session.add(hook)
    await ctx.session.flush()
    ctx.extra["webhook"] = hook
    last = None
    for i, run in enumerate([r for r in ctx.runs_of("web") if _trigger(r) == "ci"][-8:]):
        event = "run.failed" if run.status == TestStatus.FAILED else "run.passed"
        event_id = str(uuid.UUID(int=rng.getrandbits(128)))
        delivery_id = str(uuid.UUID(int=rng.getrandbits(128)))
        at = run.finalized_at or run.created_at
        payload = {"event": event, "event_id": event_id,
                   "run": {"id": run.id, "status": getattr(run.status, "value", run.status),
                           "suite_name": run.suite_name, "git_commit": run.git_commit,
                           "triggered_by": "ci"}}
        # One receiver outage: a 502, retried a minute later and accepted.
        attempts = [(502, "failed", "HTTP 502 from receiver")] if i == 3 else []
        attempts.append((204 if i == 5 else 200, "ok", None))
        for n, (code, status, error) in enumerate(attempts, start=1):
            started = ctx.past(at + timedelta(minutes=n - 1))
            ctx.session.add(WebhookDelivery(
                webhook_id=hook.id, workspace_id=ctx.ws.id, run_id=run.id, event=event, event_id=event_id,
                delivery_id=delivery_id, attempt=n, status=status, status_code=code,
                duration_ms=120 + rng.randint(0, 300), error=error, payload=payload,
                created_at=started, completed_at=started))
            last = (started, code)
    if last:
        hook.last_delivery_at, hook.last_delivery_status = last
        ctx.session.add(hook)


def _tracker(ctx) -> None:
    from app.core.secrets import encrypt_secret
    from app.models import IssueTrackerConfig
    ctx.session.add(IssueTrackerConfig(
        workspace_id=ctx.ws.id, provider="jira", name="Jira (demo, disabled)",
        base_url="https://demo-team.atlassian.example.invalid", auth_user="qa-bot@example.invalid",
        auth_secret_encrypted=encrypt_secret("not-a-real-token"),
        settings={"project_key": "DEMO", "issue_type": "Bug"}, enabled=False,
        created_by_id=ctx.user.id, created_at=ctx.ago(days=20)))


def _agent_session(ctx, rng) -> None:
    """One claude-code session that ran the web suite from a feature branch."""
    from app.models import AgentSession, RunTrigger
    session_id = f"demo-{uuid.UUID(int=rng.getrandbits(128)).hex[:12]}"
    runs = [r for r in ctx.runs_of("web") if _trigger(r) == "ci"][-6:-3]
    for run in runs:
        run.agent_id, run.agent_session_id = AGENT_ID, session_id
        run.triggered_by = RunTrigger.API_AGENT
        run.git_branch = "feat/todo-due-dates"
        ctx.session.add(run)
    first = runs[0].created_at if runs else ctx.ago(days=3)
    last = runs[-1].created_at if runs else ctx.ago(days=2)
    row = AgentSession(workspace_id=ctx.ws.id, agent_id=AGENT_ID, agent_session_id=session_id,
                       user_id=ctx.user.id, runs=len(runs), proposals=0, llm_tokens=0, denials=0,
                       first_seen_at=first - timedelta(minutes=4), last_seen_at=ctx.past(last))
    ctx.session.add(row)
    ctx.extra["agent_id"] = AGENT_ID
    ctx.extra["agent_session"] = row
    ctx.extra["agent_runs"] = runs


_PROVIDERS = (("anthropic", "claude-sonnet-4-5"), ("ollama", "qwen2.5-coder:14b"))
# feature, share of calls, input token range, output token range
_FEATURES = (
    ("failure_analysis", 0.40, (2500, 6000), (300, 900)),
    ("selector_heal", 0.25, (1500, 3500), (80, 250)),
    ("case_generation", 0.20, (3000, 7000), (800, 2200)),
    ("requirement_generation", 0.10, (2000, 4000), (600, 1600)),
    ("assistant", 0.05, (1200, 5000), (200, 900)),
)
_LLM_ERRORS = ("rate limited (HTTP 429)", "timed out after 60s", "connection refused (ollama)")


def _llm_usage(ctx, rng) -> List[Any]:
    from app.models import LLMUsageEvent, TestStatus
    failed_runs: Dict[Any, Any] = {}
    for res, run, _x in ctx.result_rows:
        if res.status == TestStatus.FAILED and run.created_at <= ctx.now:
            failed_runs.setdefault(run.created_at.date(), run)
    sess = ctx.extra["agent_session"]
    events: List[Any] = []
    for d in range(30):
        for _ in range(rng.randint(3, 9)):
            at = ctx.ago(days=d, hours=rng.randint(1 if d == 0 else 0, 23), minutes=rng.randint(0, 59))
            roll, acc, feat = rng.random(), 0.0, _FEATURES[-1]
            for f in _FEATURES:
                acc += f[1]
                if roll < acc:
                    feat = f
                    break
            name, _, (i_lo, i_hi), (o_lo, o_hi) = feat
            # Heal and the assistant use the local model; the rest mostly the hosted one.
            local = name in ("selector_heal", "assistant") or rng.random() < 0.2
            provider, model = _PROVIDERS[1] if local else _PROVIDERS[0]
            ok = rng.random() > 0.05
            inp = rng.randint(i_lo, i_hi) if ok else rng.randint(i_lo, i_hi) // 3
            out = rng.randint(o_lo, o_hi) if ok else 0
            run = failed_runs.get(at.date()) if name == "failure_analysis" else None
            events.append(LLMUsageEvent(
                workspace_id=ctx.ws.id, project_id=(run.project_id if run else ctx.projects["web"].id),
                run_id=run.id if run else None, provider=provider, model=model, feature=name,
                source="backend", input_tokens=inp, output_tokens=out, total_tokens=inp + out,
                latency_ms=rng.randint(2500, 14000) if local else rng.randint(900, 4000),
                success=ok, error=None if ok else rng.choice(_LLM_ERRORS), created_at=at))
    # The agent session's own generations.
    for run in ctx.extra["agent_runs"]:
        inp, out = rng.randint(3000, 6000), rng.randint(900, 2000)
        events.append(LLMUsageEvent(
            workspace_id=ctx.ws.id, project_id=run.project_id, provider=_PROVIDERS[0][0],
            model=_PROVIDERS[0][1], feature="case_generation", source="backend", input_tokens=inp,
            output_tokens=out, total_tokens=inp + out, latency_ms=rng.randint(1500, 3500),
            success=True, created_at=ctx.past(run.created_at - timedelta(minutes=2)),
            agent_id=sess.agent_id, agent_session_id=sess.agent_session_id))
        sess.llm_tokens += inp + out
    ctx.session.add_all(events)
    return events


def _policy_denials(ctx) -> None:
    from app.models import PolicyDecisionLog
    sess = ctx.extra["agent_session"]
    web, api = ctx.projects["web"].id, ctx.projects["api"].id
    # Reasons are worded as app/services/policy/rules.py words them.
    rows = [
        ("run.dispatch", "agent.budget",
         "Agent session budget exhausted: 20/20 runs. Stop this task; a workspace admin can raise "
         "the budget on the Agents page.", web, ctx.user.id, sess.agent_id, sess.agent_session_id,
         {"limit": "max_runs", "used": 20, "max": 20, "kind": "run"}, ctx.past(sess.last_seen_at)),
        ("proposal.approve", "proposal.separation_of_duties",
         "You filed this proposal, so you cannot accept it. This workspace requires a second "
         "person to approve agent changes.", web, ctx.members["li"].id, None, None, None,
         ctx.ago(days=9, hours=4)),
        ("ai.use", "plan.llm_token_quota", "workspace over monthly token quota", api, None,
         None, None, None, ctx.ago(days=15, hours=2)),
        ("ai.use", "ai.workspace_opt_out",
         "AI features are switched off for this workspace by a workspace admin.", web,
         ctx.members["sam"].id, None, None, None, ctx.ago(days=21, hours=6)),
    ]
    for action, rule, reason, project_id, user_id, agent_id, agent_session_id, data, at in rows:
        ctx.session.add(PolicyDecisionLog(
            action=action, rule=rule, reason=reason, workspace_id=ctx.ws.id, project_id=project_id,
            user_id=user_id, agent_id=agent_id, agent_session_id=agent_session_id, data=data,
            created_at=at))
        if agent_session_id:
            sess.denials += 1


def _usage_records(ctx, events: List[Any]) -> None:
    """Monthly roll-ups, as record_llm_usage / run creation keep them."""
    from app.models import UsageRecord
    totals: Dict[tuple, int] = {}

    def add(at: datetime, metric: str, n: int) -> None:
        key = (at.strftime("%Y-%m"), metric)
        totals[key] = totals.get(key, 0) + n

    for ev in events:
        add(ev.created_at, "llm_tokens", ev.total_tokens)
        if ev.feature in ("case_generation", "requirement_generation") and ev.success:
            add(ev.created_at, "ai_generations", 1)
    for run, _ in list(ctx.run_rows) + list(ctx.mon_runs):
        if run.created_at <= ctx.now:
            add(run.created_at, "runs", 1)
    for (period, metric), count in sorted(totals.items()):
        ctx.session.add(UsageRecord(workspace_id=ctx.ws.id, period=period, metric=metric, count=count))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_members(session, workspace_id: int) -> int:
    """Remove this demo's fake members after the workspace purge.

    Only users whose e-mail names this demo AND who no longer belong to any
    workspace. Anything still pointing at them lies outside the workspace
    purge (role rows, tokens, settings, rows in other tables that record
    who did something): nullable references are cleared, the rest deleted.
    Caller commits."""
    from sqlalchemy import text
    from sqlmodel import SQLModel, select
    from app.models import User, UserWorkspace

    candidates = (await session.exec(select(User.id).where(
        User.email.in_(_member_emails(workspace_id))))).all()
    ids = [u for u in candidates
           if not (await session.exec(select(UserWorkspace).where(UserWorkspace.user_id == u))).first()]
    if not ids:
        return 0
    for table in reversed(SQLModel.metadata.sorted_tables):   # dependents first
        if table.name in ("users", "auditlog"):
            continue
        for col in table.columns:
            if not any(fk.column.table.name == "users" for fk in col.foreign_keys):
                continue
            if col.nullable:
                sql = f'UPDATE "{table.name}" SET "{col.name}" = NULL WHERE "{col.name}" = ANY(:ids)'
            else:
                sql = f'DELETE FROM "{table.name}" WHERE "{col.name}" = ANY(:ids)'
            await session.exec(text(sql), params={"ids": ids})
    await session.exec(text("DELETE FROM users WHERE id = ANY(:ids)"), params={"ids": ids})
    return len(ids)
