"""Agent kill switch and per-session budgets (WP14b, decision W3).

Agents identify themselves with `X-Agent-Id` and, per task, `X-Agent-Session-Id`
(app/core/auth.py). A workspace admin can:

* **block an agent id** (`AgentControl.blocked`) — the agent gets 403 on writes
  in that workspace; reads keep working so it can still report what happened;
* **cap each session** of an agent: runs started, LLM tokens, proposals filed,
  wall-clock minutes since the session's first budgeted action. None by
  default. Resolution per limit: the agent's own row, then the workspace's
  "*" row, then the instance default (`AGENT_SESSION_MAX_*`); `None` inherits,
  `0` means no limit.

Exceeding a budget is a 429 with a machine-readable body (`budget_detail`) so a
cooperative agent can stop instead of retrying. Budgets bound well-behaved
automation that has gone off the rails; they are not a security boundary — an
agent that omits its session header is simply not session-budgeted, and the
hard stop for a misbehaving key is the kill switch or revoking the key.

Usage lives in `AgentSession` counters, bumped with an upsert so two requests
of one session racing cannot lose an increment. The pure functions here
(`resolve_limits`, `budget_exceeded`, `budget_detail`) hold all the decisions;
the policy engine's `agent.*` rules (app/services/policy/rules.py) call them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

#: The workspace-default row's agent id.
WILDCARD = "*"

LIMIT_FIELDS = ("max_runs", "max_llm_tokens", "max_proposals", "max_minutes")

INSTANCE_KEYS = {
    "max_runs": "AGENT_SESSION_MAX_RUNS",
    "max_llm_tokens": "AGENT_SESSION_MAX_LLM_TOKENS",
    "max_proposals": "AGENT_SESSION_MAX_PROPOSALS",
    "max_minutes": "AGENT_SESSION_MAX_MINUTES",
}

#: What each budgeted kind of action is counted against.
KIND_LIMIT = {
    "run": ("max_runs", "runs"),
    "proposal": ("max_proposals", "proposals"),
    "llm": ("max_llm_tokens", "llm_tokens"),
}

_LABELS = {
    "max_runs": "runs started",
    "max_llm_tokens": "LLM tokens",
    "max_proposals": "proposals filed",
    "max_minutes": "minutes of wall-clock time",
}

MAX_ID_LEN = 200


def clean_id(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text[:MAX_ID_LEN] or None


# ---------------------------------------------------------------------------
# Pure decisions
# ---------------------------------------------------------------------------

def _as_limit(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def instance_limits() -> Dict[str, int]:
    """Instance-wide default caps per session (0 = none). Fails open."""
    out = {f: 0 for f in LIMIT_FIELDS}
    try:
        from app.services.instance_settings import effective
        for field, key in INSTANCE_KEYS.items():
            out[field] = _as_limit(effective(key))
    except Exception:  # noqa: BLE001 — settings DB down must not block agents
        pass
    return out


def resolve_limits(agent_row: Any, default_row: Any, instance: Dict[str, int]) -> Dict[str, int]:
    """Effective per-session limits: agent row > workspace "*" row > instance.
    A row's None inherits; 0 is an explicit "no limit"."""
    out: Dict[str, int] = {}
    for field in LIMIT_FIELDS:
        value = None
        for row in (agent_row, default_row):
            if row is not None and getattr(row, field, None) is not None:
                value = getattr(row, field)
                break
        out[field] = _as_limit(value if value is not None else instance.get(field, 0))
    return out


def budget_exceeded(limits: Dict[str, int], usage: Dict[str, Any], kind: str,
                    now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The limit an action of `kind` would break, or None.

    `usage` has runs / proposals / llm_tokens / first_seen_at. A limit is hit
    once the counter has reached it (the next action is refused), the same
    shape as the plan quotas. Wall-clock applies to every kind.
    """
    now = now or datetime.utcnow()
    minutes = limits.get("max_minutes") or 0
    first_seen = usage.get("first_seen_at")
    if minutes and first_seen is not None:
        elapsed = now - first_seen
        if elapsed >= timedelta(minutes=minutes):
            return {"limit": "max_minutes", "used": int(elapsed.total_seconds() // 60),
                    "max": minutes}
    field, counter = KIND_LIMIT[kind]
    cap = limits.get(field) or 0
    used = int(usage.get(counter) or 0)
    if cap and used >= cap:
        return {"limit": field, "used": used, "max": cap}
    return None


def budget_detail(verdict: Dict[str, Any], agent_id: Optional[str],
                  agent_session_id: Optional[str]) -> Dict[str, Any]:
    """The 429 body. Stable keys: agents branch on `error` and `limit`."""
    label = _LABELS.get(verdict["limit"], verdict["limit"])
    return {
        "error": "agent_budget_exceeded",
        "rule": "agent.budget",
        "limit": verdict["limit"],
        "used": verdict["used"],
        "max": verdict["max"],
        "agent_id": agent_id,
        "agent_session_id": agent_session_id,
        "message": (f"Agent session budget exhausted: {verdict['used']}/{verdict['max']} "
                    f"{label}. Stop this task; a workspace admin can raise the budget "
                    f"on the Agents page."),
    }


def blocked_detail(agent_id: Optional[str], reason: Optional[str]) -> Dict[str, Any]:
    return {
        "error": "agent_blocked",
        "rule": "agent.kill_switch",
        "agent_id": agent_id,
        "message": ("This agent is blocked in this workspace by a workspace admin"
                    + (f": {reason}" if reason else "") + ". Reads still work; writes are refused."),
    }


def usage_of(row: Any) -> Dict[str, Any]:
    if row is None:
        return {"runs": 0, "proposals": 0, "llm_tokens": 0, "first_seen_at": None}
    return {"runs": row.runs, "proposals": row.proposals, "llm_tokens": row.llm_tokens,
            "first_seen_at": row.first_seen_at}


# ---------------------------------------------------------------------------
# Storage — async (API) and sync (providers, Celery) twins
# ---------------------------------------------------------------------------

def _controls_query(workspace_id: int, agent_id: str):
    from sqlmodel import select
    from app.models import AgentControl
    return select(AgentControl).where(
        AgentControl.workspace_id == workspace_id,
        AgentControl.agent_id.in_([agent_id, WILDCARD]))


def _split(rows) -> Tuple[Any, Any]:
    agent_row = next((r for r in rows if r.agent_id != WILDCARD), None)
    default_row = next((r for r in rows if r.agent_id == WILDCARD), None)
    return agent_row, default_row


async def load_controls(session, workspace_id: int, agent_id: str) -> Tuple[Any, Any]:
    return _split((await session.exec(_controls_query(workspace_id, agent_id))).all())


def load_controls_sync(session, workspace_id: int, agent_id: str) -> Tuple[Any, Any]:
    return _split(session.exec(_controls_query(workspace_id, agent_id)).all())


def _bump_stmt(workspace_id: int, agent_id: str, agent_session_id: str,
               user_id: Optional[int], runs: int, proposals: int, llm_tokens: int,
               denials: int):
    """INSERT .. ON CONFLICT DO UPDATE: creates the session on first sight
    (starting its wall clock) and adds to its counters atomically."""
    from sqlalchemy.dialects.postgresql import insert
    from app.models import AgentSession
    now = datetime.utcnow()
    stmt = insert(AgentSession).values(
        workspace_id=workspace_id, agent_id=agent_id, agent_session_id=agent_session_id,
        user_id=user_id, runs=runs, proposals=proposals, llm_tokens=llm_tokens,
        denials=denials, first_seen_at=now, last_seen_at=now)
    table = AgentSession.__table__
    return stmt.on_conflict_do_update(
        constraint="uq_agentsession_ws_agent_session",
        set_={"runs": table.c.runs + runs, "proposals": table.c.proposals + proposals,
              "llm_tokens": table.c.llm_tokens + llm_tokens,
              "denials": table.c.denials + denials, "last_seen_at": now},
    ).returning(table.c.runs, table.c.proposals, table.c.llm_tokens, table.c.first_seen_at)


def _row_usage(row) -> Dict[str, Any]:
    return {"runs": row[0], "proposals": row[1], "llm_tokens": row[2], "first_seen_at": row[3]}


async def bump_session(bind, workspace_id: int, agent_id: str, agent_session_id: str, *,
                       user_id: Optional[int] = None, runs: int = 0, proposals: int = 0,
                       llm_tokens: int = 0, denials: int = 0) -> Dict[str, Any]:
    """Touch / add to a session's counters in its own short transaction, so
    the caller's transaction (which may yet raise) cannot roll the count back
    or be disturbed by it. Returns the usage after the bump."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    async with AsyncSession(bind, expire_on_commit=False) as own:
        row = (await own.execute(_bump_stmt(workspace_id, agent_id, agent_session_id, user_id,
                                            runs, proposals, llm_tokens, denials))).first()
        await own.commit()
    return _row_usage(row)


def bump_session_sync(bind, workspace_id: int, agent_id: str, agent_session_id: str, *,
                      user_id: Optional[int] = None, runs: int = 0, proposals: int = 0,
                      llm_tokens: int = 0, denials: int = 0) -> Dict[str, Any]:
    from sqlmodel import Session
    with Session(bind) as own:
        row = own.execute(_bump_stmt(workspace_id, agent_id, agent_session_id, user_id,
                                     runs, proposals, llm_tokens, denials)).first()
        own.commit()
    return _row_usage(row)


def add_session_usage_sync(session, workspace_id: int, agent_id: str, agent_session_id: str,
                           *, llm_tokens: int = 0) -> None:
    """Inside the caller's sync transaction (record_llm_usage commits it)."""
    session.execute(_bump_stmt(workspace_id, clean_id(agent_id), clean_id(agent_session_id),
                               None, 0, 0, int(llm_tokens or 0), 0))


async def record_usage(session, principal, workspace_id: Optional[int], *,
                       runs: int = 0, proposals: int = 0) -> None:
    """After a budgeted action succeeded: count it against the caller's agent
    session. Best-effort — accounting must not fail the action it measures."""
    agent_id = clean_id(getattr(principal, "agent_id", None))
    sess = clean_id(getattr(principal, "agent_session_id", None))
    if not (workspace_id and agent_id and sess) or not (runs or proposals):
        return
    try:
        await bump_session(session.bind, workspace_id, agent_id, sess,
                           user_id=getattr(getattr(principal, "user", None), "id", None),
                           runs=runs, proposals=proposals)
    except Exception as exc:  # noqa: BLE001
        print(f"[AgentBudget] usage not recorded for {agent_id}/{sess}: {exc}")


async def enforce_agent_write(session, principal, project_id: Optional[int]) -> None:
    """Kill switch at an agent write path for a JWT caller sending X-Agent-Id
    (API-key writes are already gated in auth, where the key names the
    workspace). Raises the 403 when the agent is blocked in the project's
    workspace."""
    if not getattr(principal, "agent_id", None) or getattr(principal, "api_key", None) is not None:
        return
    if not project_id:
        return
    from app.models import Project
    from app.services.policy import PolicyContext, evaluate
    project = await session.get(Project, project_id)
    if project is None:
        return
    decision = await evaluate("agent.write", PolicyContext.from_principal(
        principal, workspace_id=project.workspace_id, project_id=project_id, session=session))
    if not decision.allowed:
        raise decision.http_exception()
