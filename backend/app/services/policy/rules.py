"""The registered policy rules. Each wraps an existing implementation — the
legacy function stays where it was and remains the source of truth, so a
parity test can compare the two directly (tests/test_policy_parity.py).

Actions:
  run.dispatch        create (API) / admit (dispatcher) / configure (job build)
  ai.use              every LLM call, server side; worker side via the job flag
  agent.write         any write by an identified agent (kill switch)
  proposal.create     an agent/human files a case proposal
  proposal.approve    a person accepts a proposal (separation of duties)
  proposal.auto_apply policy applies a proposal with no human
  security.scan       a DAST scan (or a retest) of a target
  backend.fetch       documented only: net_guard, enforced inline
"""
from __future__ import annotations

from typing import Optional

from app.services import agent_controls as ac
from app.services.policy import Decision, PolicyContext, Rule, register


# ---------------------------------------------------------------------------
# Agent kill switch + per-session budgets (W3)
# ---------------------------------------------------------------------------

def _blocked_row(agent_row, default_row):
    for row in (agent_row, default_row):
        if row is not None and row.blocked:
            return row
    return None


def _kill_decision(ctx: PolicyContext, row) -> Decision:
    detail = ac.blocked_detail(ctx.agent_id, row.blocked_reason)
    return Decision.deny("agent.kill_switch", detail["message"], status_code=403,
                         detail=detail, data={"agent_id": ctx.agent_id,
                                              "scope": "all agents" if row.agent_id == ac.WILDCARD
                                              else "this agent"})


def _kill_applies(ctx: PolicyContext) -> bool:
    return bool(ctx.agent_id and ctx.workspace_id and ctx.is_write)


async def _agent_kill_async(ctx: PolicyContext) -> Optional[Decision]:
    if not _kill_applies(ctx) or ctx.session is None:
        return None
    row = _blocked_row(*await ac.load_controls(ctx.session, ctx.workspace_id, ctx.agent_id))
    return _kill_decision(ctx, row) if row is not None else None


def _sync_session(ctx: PolicyContext):
    """The caller's sync Session, else a short-lived one on the metering engine
    (providers and Celery tasks evaluate ai.use without one)."""
    from sqlmodel import Session
    if ctx.session is not None and hasattr(ctx.session, "get_bind"):
        return ctx.session, False
    from app.services.llm_usage import _engine
    return Session(_engine), True


def _agent_kill_sync(ctx: PolicyContext) -> Optional[Decision]:
    if not _kill_applies(ctx):
        return None
    session, own = _sync_session(ctx)
    try:
        row = _blocked_row(*ac.load_controls_sync(session, ctx.workspace_id, ctx.agent_id))
    except Exception as exc:  # noqa: BLE001 — fail open like the other agent checks
        print(f"[Policy] kill-switch lookup failed (allowing): {exc}")
        return None
    finally:
        if own:
            session.close()
    return _kill_decision(ctx, row) if row is not None else None


def _budget_applies(ctx: PolicyContext) -> bool:
    return bool(ctx.agent_id and ctx.agent_session_id and ctx.workspace_id)


def _budget_decision(ctx: PolicyContext, limits, usage, kind) -> Optional[Decision]:
    verdict = ac.budget_exceeded(limits, usage, kind)
    if verdict is None:
        return None
    detail = ac.budget_detail(verdict, ctx.agent_id, ctx.agent_session_id)
    return Decision.deny("agent.budget", detail["message"], status_code=429, detail=detail,
                         data={**verdict, "kind": kind})


def _agent_budget_async(kind: str):
    async def check(ctx: PolicyContext) -> Optional[Decision]:
        if not _budget_applies(ctx) or ctx.session is None:
            return None
        agent_row, default_row = await ac.load_controls(ctx.session, ctx.workspace_id, ctx.agent_id)
        limits = ac.resolve_limits(agent_row, default_row, ac.instance_limits())
        # Touch the session even with no limits: it starts the wall clock and
        # is what the Agents page lists.
        try:
            usage = await ac.bump_session(ctx.session.bind, ctx.workspace_id, ctx.agent_id,
                                          ctx.agent_session_id, user_id=ctx.user_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[Policy] agent session touch failed (allowing): {exc}")
            return None
        return _budget_decision(ctx, limits, usage, kind)
    return check


def _agent_budget_sync(kind: str):
    def check(ctx: PolicyContext) -> Optional[Decision]:
        if not _budget_applies(ctx):
            return None
        session, own = _sync_session(ctx)
        try:
            agent_row, default_row = ac.load_controls_sync(session, ctx.workspace_id, ctx.agent_id)
            limits = ac.resolve_limits(agent_row, default_row, ac.instance_limits())
            usage = ac.bump_session_sync(session.get_bind(), ctx.workspace_id, ctx.agent_id,
                                         ctx.agent_session_id, user_id=ctx.user_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[Policy] agent budget lookup failed (allowing): {exc}")
            return None
        finally:
            if own:
                session.close()
        return _budget_decision(ctx, limits, usage, kind)
    return check


_KILL_DESC = ("A workspace admin blocked this agent id (or all agents, via the '*' row). "
              "Blocked agents get 403 on writes; reads still work.")
_KILL_WHERE = "Agents page → kill switch (workspace admin)"
_BUDGET_WHERE = ("Agents page → budgets (workspace admin); instance defaults "
                 "AGENT_SESSION_MAX_* in Instance settings → Policies")


def _register_agent_rules(action: str, kind: Optional[str], stages=None, order_base=10):
    register(Rule(name="agent.kill_switch", action=action, description=_KILL_DESC,
                  configured=_KILL_WHERE, acheck=_agent_kill_async, check=_agent_kill_sync,
                  stages=stages, enforced_in="app/services/agent_controls.py", order=order_base))
    if kind:
        register(Rule(
            name="agent.budget", action=action,
            description=(f"Per agent session (X-Agent-Session-Id) cap on {ac._LABELS[ac.KIND_LIMIT[kind][0]]} "
                         "and wall-clock minutes. Exceeding returns 429 with a machine-readable reason."),
            configured=_BUDGET_WHERE, acheck=_agent_budget_async(kind), check=_agent_budget_sync(kind),
            stages=stages, enforced_in="app/services/agent_controls.py", order=order_base + 20))


# ---------------------------------------------------------------------------
# run.dispatch
# ---------------------------------------------------------------------------

async def _run_quota(ctx: PolicyContext) -> Optional[Decision]:
    """Wraps billing.check_quota("runs"). facts["planned"]: number of runs this
    request will create when the caller refuses on "would exceed" (benchmarks);
    absent = refuse only once the quota is already spent (run creation)."""
    if not ctx.workspace_id:
        return None
    from app.services import billing
    allowed, used, limit = await billing.check_quota(ctx.session, ctx.workspace_id, "runs")
    planned = ctx.facts.get("planned")
    data = {"used": used, "limit": limit}
    if planned is None:
        if allowed:
            return None
        return Decision.deny(
            "plan.run_quota", status_code=402, data=data,
            reason=f"Monthly run quota reached ({used}/{limit}). Upgrade the workspace plan to run more.")
    if allowed and not (limit and used + planned > limit):
        return None
    return Decision.deny("plan.run_quota", status_code=402, data={**data, "planned": planned},
                         reason=f"Monthly run quota would be exceeded ({used}+{planned}/{limit}).")


def _concurrency(ctx: PolicyContext) -> Optional[Decision]:
    """Wraps app.worker._workspace_at_capacity (fails open there)."""
    from app.worker import _workspace_at_capacity
    if _workspace_at_capacity(ctx.facts["run"], ctx.session):
        return Decision.defer("workspace.max_concurrent_runs",
                              "workspace at its max_concurrent_runs cap; retrying",
                              data={"retry_seconds": 30})
    return None


def _capture(ctx: PolicyContext) -> Optional[Decision]:
    from app.worker import _load_data_policy
    return Decision.with_data(data_policy=_load_data_policy(ctx.project_id, ctx.session))


def _egress(ctx: PolicyContext) -> Optional[Decision]:
    from app.worker import _load_egress_policy
    return Decision.with_data(egress_policy=_load_egress_policy(
        ctx.project_id, ctx.facts.get("environment"), ctx.session))


def _ai_job_flag(ctx: PolicyContext) -> Optional[Decision]:
    """The job's `ai_allowed` flag: ai.use evaluated for the run's workspace and
    agent session, without logging (a probe — nothing is refused yet)."""
    from app.services.policy import ai_allowed_sync
    try:
        verdict = ai_allowed_sync(ctx.workspace_id, agent_id=ctx.agent_id,
                                  agent_session_id=ctx.agent_session_id,
                                  project_id=ctx.project_id, log=False)
        return Decision.with_data(ai_allowed=verdict.allowed,
                                  ai_blocked_by=None if verdict.allowed else verdict.rule)
    except Exception as exc:  # noqa: BLE001 — the flag must never stop a run
        print(f"[Policy] ai flag evaluation failed (allowing): {exc}")
        return Decision.with_data(ai_allowed=True, ai_blocked_by=None)


register(Rule(
    name="plan.run_quota", action="run.dispatch", stages=("create",), acheck=_run_quota,
    description="The workspace plan's monthly run allowance (0 = unlimited). 402 when spent.",
    configured="Billing page / plan limits (monthly_runs)",
    enforced_in="app/services/billing.py check_quota", order=20))
_register_agent_rules("run.dispatch", "run", stages=("create",))
register(Rule(
    name="workspace.max_concurrent_runs", action="run.dispatch", stages=("admit",), check=_concurrency,
    description="Runs over the workspace's RUNNING cap stay PENDING and are retried every 30 s "
                "(a deferral, not a deny — not logged).",
    configured="Workspace settings → max concurrent runs (0 = unlimited)",
    enforced_in="app/worker.py _workspace_at_capacity", outcome="defer", order=10))
register(Rule(
    name="data.capture_ceiling", action="run.dispatch", stages=("configure",), check=_capture,
    description="Resolves the run's capture policy: the project's level clamped to the instance "
                "MAX_CAPTURE_LEVEL ceiling, applied at dispatch so a job cannot exceed it.",
    configured="Project settings → data policy; Instance settings → MAX_CAPTURE_LEVEL",
    enforced_in="app/services/data_policy.py resolve_for_project", outcome="resolve", order=20))
register(Rule(
    name="network.egress_allowlist", action="run.dispatch", stages=("configure",), check=_egress,
    description="Resolves the worker egress allowlist (project policy or the instance switch); "
                "the worker aborts requests to hosts outside it.",
    configured="Project settings → network policy; Instance settings → EGRESS_ALLOWLIST_ENFORCED",
    enforced_in="app/services/network_policy.py effective_for_project", outcome="resolve", order=30))
register(Rule(
    name="ai.job_flag", action="run.dispatch", stages=("configure",), check=_ai_job_flag,
    description="Sets the job's ai_allowed flag from ai.use, so workers skip heal and failure "
                "analysis LLM calls for opted-out or over-budget workspaces.",
    configured="Policies page → AI use (workspace admin)",
    enforced_in="execution-engine/src/core/ai-gate.ts", outcome="resolve", order=40))


# ---------------------------------------------------------------------------
# ai.use
# ---------------------------------------------------------------------------

def _opt_out(ctx: PolicyContext) -> Optional[Decision]:
    if not ctx.workspace_id:
        return None
    session, own = _sync_session(ctx)
    try:
        from app.models import Workspace
        ws = session.get(Workspace, ctx.workspace_id)
        enabled = True if ws is None else bool(getattr(ws, "ai_enabled", True))
    except Exception as exc:  # noqa: BLE001 — same fail-open stance as the token quota
        print(f"[Policy] AI opt-out lookup failed (allowing): {exc}")
        return None
    finally:
        if own:
            session.close()
    if enabled:
        return None
    return Decision.deny("ai.workspace_opt_out", status_code=403,
                         reason="AI features are switched off for this workspace by a workspace admin.")


def _token_quota(ctx: PolicyContext) -> Optional[Decision]:
    """Wraps llm_usage.llm_tokens_quota_exceeded (fails open there)."""
    from app.services.llm_usage import llm_tokens_quota_exceeded
    if not ctx.workspace_id:
        return None
    if llm_tokens_quota_exceeded(ctx.workspace_id):
        return Decision.deny("plan.llm_token_quota", status_code=402,
                             reason="workspace over monthly token quota")
    return None


register(Rule(
    name="ai.workspace_opt_out", action="ai.use", check=_opt_out, order=5,
    description="A workspace admin switched off ALL LLM use for the workspace: failure analysis, "
                "case generation, heal (backend and worker) and the assistant.",
    configured="Policies page → AI use (workspace admin); default on",
    enforced_in="app/ai/providers.py (every call) + job flag for workers"))
_register_agent_rules("ai.use", "llm")
register(Rule(
    name="plan.llm_token_quota", action="ai.use", check=_token_quota, order=50,
    description="The workspace plan's monthly LLM token cap (monthly_llm_tokens, 0 = unlimited). "
                "Calls are skipped and features fall back to their non-AI behaviour.",
    configured="Billing page / plan limits (monthly_llm_tokens)",
    enforced_in="app/services/llm_usage.py llm_tokens_quota_exceeded"))


# ---------------------------------------------------------------------------
# agent.write / proposals
# ---------------------------------------------------------------------------

_register_agent_rules("agent.write", None)
_register_agent_rules("proposal.create", "proposal")


async def _separation(ctx: PolicyContext) -> Optional[Decision]:
    """Wraps proposal_policy.separation_required + approver_conflict."""
    from app.models import Project, Workspace
    from app.services.proposal_policy import approver_conflict, separation_required
    proposal = ctx.facts["proposal"]
    project = await ctx.session.get(Project, proposal.project_id)
    workspace = await ctx.session.get(Workspace, project.workspace_id) if project else None
    required = separation_required(
        workspace_flag=bool(getattr(workspace, "require_separate_approver", False)))
    if approver_conflict(created_by_id=proposal.created_by_id,
                         approver_id=ctx.facts.get("approver_id"), required=required):
        return Decision.deny(
            "proposal.separation_of_duties", status_code=403, data={"proposal_id": proposal.id},
            reason="You filed this proposal, so you cannot accept it. This "
                   "workspace requires a second person to approve agent changes.")
    return None


def _auto_apply_kill(ctx: PolicyContext) -> Optional[Decision]:
    from app.services.proposal_policy import auto_apply_disabled
    if auto_apply_disabled():
        return Decision.deny("proposal.auto_apply_disabled", status_code=403,
                             data={"proposal_id": getattr(ctx.facts.get("proposal"), "id", None)},
                             reason="auto-apply is switched off instance-wide (AUTO_APPLY_DISABLED)")
    return None


register(Rule(
    name="proposal.separation_of_duties", action="proposal.approve", acheck=_separation,
    description="Whoever filed a proposal cannot accept it, when the workspace (or the instance, "
                "as a floor) requires a second approver.",
    configured="Proposals → policy (workspace admin); Instance settings → REQUIRE_SEPARATE_APPROVER",
    enforced_in="app/services/proposal_policy.py"))
register(Rule(
    name="proposal.auto_apply_disabled", action="proposal.auto_apply", check=_auto_apply_kill,
    description="Instance-wide kill switch for policy-driven (human-free) application of agent "
                "proposals. Evaluated only when a proposal would otherwise auto-apply.",
    configured="Instance settings → AUTO_APPLY_DISABLED",
    enforced_in="app/services/proposal_policy.py auto_apply_disabled"))


# ---------------------------------------------------------------------------
# security.scan
# ---------------------------------------------------------------------------

_SCAN_GATES = (
    ("disabled for this project", "security.scanning_enabled"),
    ("allowlist", "security.domain_allowlist"),
    ("Active scanning", "security.active_scan"),
)


async def _scan_gate(ctx: PolicyContext) -> Optional[Decision]:
    """Wraps app.api.security._scan_gate_reason; the rule name records which of
    its gates refused."""
    from app.api.security import _scan_gate_reason
    reason = await _scan_gate_reason(ctx.session, ctx.facts["project"], ctx.facts["target_url"],
                                     ctx.facts["scan_type"])
    if not reason:
        return None
    gate = next((name for needle, name in _SCAN_GATES if needle in reason), "security.scan_gate")
    return Decision.deny("security.scan_gate", reason, status_code=403,
                         data={"gate": gate, "scan_type": ctx.facts["scan_type"]})


register(Rule(
    name="security.scan_gate", action="security.scan", acheck=_scan_gate,
    description="Scans need project scanning enabled and the target on the project's "
                "authorized-domain allowlist; active scans also need the workspace (or "
                "SECURITY_ACTIVE_SCAN_ENABLED) and project active-scan switches.",
    configured="Project → Security settings; Workspace → active scanning; env SECURITY_ACTIVE_SCAN_ENABLED",
    enforced_in="app/api/security.py _scan_gate_reason"))


# ---------------------------------------------------------------------------
# Documented only
# ---------------------------------------------------------------------------

register(Rule(
    name="network.ssrf_guard", action="backend.fetch", enforced=False,
    description="Every URL the backend itself fetches (webhooks, OpenAPI specs, target probes) "
                "is refused when it resolves to link-local/metadata, private or loopback "
                "addresses unless the instance allows private targets. A low-level guard, "
                "enforced inline and not through the engine; listed so the catalog is complete.",
    configured="Instance settings → ALLOW_PRIVATE_NETWORK_TARGETS / OUTBOUND_ALLOWED_HOSTS",
    enforced_in="app/core/net_guard.py"))
