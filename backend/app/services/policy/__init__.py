"""Policy engine — one evaluation point for "may this happen?" (E22, decision W4).

Before this, every gate lived at its call site: the plan run quota in three
endpoints, the workspace concurrency cap in the Celery dispatcher, the capture
ceiling and egress policy in two dispatcher helpers, the LLM token quota inside
each provider, separation of duties and the auto-apply kill switch in the
proposal API, the scan gate in the security API. Nobody could answer "what are
all the rules, and what did they refuse last week?".

    decision = await evaluate("run.dispatch", PolicyContext(...))
    decision = evaluate_sync("ai.use", PolicyContext(...))   # sync callers

Rules are registered per action (rules.py). Each WRAPS the existing
implementation rather than re-implementing it, so outcomes are unchanged —
tests/test_policy_parity.py proves it table by table. A rule returns None (no
opinion), an allow with `data` (resolution rules such as the capture ceiling
contribute values the caller uses), or a deny. The first deny wins; data from
every rule that ran is merged into the result.

Every DENY is written to `PolicyDecisionLog` with the rule that fired (allows
are not — volume). Two exceptions, both deliberate: a `defer` (the concurrency
cap: the run waits and is retried every 30 s, so it would log the same "deny"
dozens of times for one run that then succeeds) and probes made with
`log=False` (dispatch asks "is AI allowed?" to set a job flag — nothing was
refused). An identical deny repeating within `DEDUPE_SECONDS` in the same
process is logged once, so a loop of LLM calls in an opted-out workspace does
not write a row per call.

`stage` scopes rules within an action: run.dispatch is evaluated at run
creation (`create`: quota, agent controls — async API), when the dispatcher
admits a run (`admit`: concurrency), and when it builds the job (`configure`:
capture ceiling, egress, AI flag). A rule declares the stages it belongs to.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

ALLOW = "allow"
DENY = "deny"
DEFER = "defer"

DEDUPE_SECONDS = 60.0


@dataclass
class Decision:
    allowed: bool = True
    rule: Optional[str] = None
    reason: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    # HTTP mapping for API callers. `detail` is what the endpoint returns —
    # for wrapped legacy checks it is the exact legacy message, so clients see
    # no change; new rules return a machine-readable dict.
    status_code: int = 403
    detail: Any = None
    effect: str = ALLOW

    @classmethod
    def deny(cls, rule: str, reason: str, *, status_code: int = 403, detail: Any = None,
             data: Optional[Dict[str, Any]] = None) -> "Decision":
        return cls(allowed=False, rule=rule, reason=reason, status_code=status_code,
                   detail=detail if detail is not None else reason, data=dict(data or {}),
                   effect=DENY)

    @classmethod
    def defer(cls, rule: str, reason: str, *, data: Optional[Dict[str, Any]] = None) -> "Decision":
        return cls(allowed=False, rule=rule, reason=reason, status_code=429, detail=reason,
                   data=dict(data or {}), effect=DEFER)

    @classmethod
    def with_data(cls, **data: Any) -> "Decision":
        return cls(allowed=True, data=data)

    def http_exception(self):
        from fastapi import HTTPException
        return HTTPException(status_code=self.status_code,
                             detail=self.detail if self.detail is not None else self.reason)


@dataclass
class PolicyContext:
    workspace_id: Optional[int] = None
    project_id: Optional[int] = None
    user_id: Optional[int] = None
    api_key_id: Optional[int] = None
    agent_id: Optional[str] = None
    agent_session_id: Optional[str] = None
    is_write: bool = True
    stage: Optional[str] = None
    # AsyncSession for evaluate(), sync Session for evaluate_sync(). Rules that
    # need the database without one open their own (see rules.py).
    session: Any = None
    # Action-specific inputs (the run, the scan target, ...).
    facts: Dict[str, Any] = field(default_factory=dict)
    log: bool = True

    @classmethod
    def from_principal(cls, principal: Any, **kw: Any) -> "PolicyContext":
        from app.services.agent_controls import clean_id
        user = getattr(principal, "user", None)
        key = getattr(principal, "api_key", None)
        return cls(user_id=getattr(user, "id", None), api_key_id=getattr(key, "id", None),
                   agent_id=clean_id(getattr(principal, "agent_id", None)),
                   agent_session_id=clean_id(getattr(principal, "agent_session_id", None)),
                   **kw)


RuleFn = Callable[[PolicyContext], Optional[Decision]]
AsyncRuleFn = Callable[[PolicyContext], Awaitable[Optional[Decision]]]


@dataclass(frozen=True)
class Rule:
    name: str
    action: str
    description: str
    configured: str                    # where an operator changes it
    check: Optional[RuleFn] = None     # sync implementation
    acheck: Optional[AsyncRuleFn] = None
    stages: Optional[tuple] = None     # None = every stage
    enforced: bool = True              # False = documented here, enforced inline elsewhere
    enforced_in: str = ""              # code location, for the Policies page
    outcome: str = "deny"              # deny | defer | resolve (data only)
    order: int = 100

    def applies(self, stage: Optional[str]) -> bool:
        return self.enforced and (self.stages is None or stage in self.stages)


_RULES: Dict[str, List[Rule]] = {}
_lock = threading.Lock()


def register(rule: Rule) -> Rule:
    with _lock:
        bucket = [r for r in _RULES.get(rule.action, []) if r.name != rule.name]
        bucket.append(rule)
        bucket.sort(key=lambda r: r.order)
        _RULES[rule.action] = bucket
    return rule


def rules_for(action: str) -> List[Rule]:
    _ensure_loaded()
    return list(_RULES.get(action, []))


def catalog() -> List[Dict[str, Any]]:
    """Every registered rule, for the Policies page."""
    _ensure_loaded()
    out = []
    for action in sorted(_RULES):
        for r in _RULES[action]:
            out.append({"name": r.name, "action": r.action, "description": r.description,
                        "configured": r.configured, "stages": list(r.stages or []),
                        "enforced_by_engine": r.enforced, "enforced_in": r.enforced_in,
                        "outcome": r.outcome})
    return out


_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        from app.services.policy import rules  # noqa: F401 — registers on import
        _loaded = True


def _select(action: str, ctx: PolicyContext, only: Optional[Iterable[str]]) -> List[Rule]:
    wanted = set(only) if only else None
    return [r for r in rules_for(action)
            if r.applies(ctx.stage) and (wanted is None or r.name in wanted)]


def _finish(action: str, ctx: PolicyContext, merged: Dict[str, Any],
            denied: Optional[Decision]) -> Decision:
    if denied is None:
        return Decision(allowed=True, data=merged)
    denied.data = {**merged, **denied.data}
    return denied


async def evaluate(action: str, ctx: PolicyContext, *,
                   only: Optional[Iterable[str]] = None) -> Decision:
    """Async evaluation. Async rules are awaited; sync-only rules run in a
    thread when they open their own database connection."""
    merged: Dict[str, Any] = {}
    for rule in _select(action, ctx, only):
        if rule.acheck is not None:
            result = await rule.acheck(ctx)
        elif rule.check is not None:
            result = await asyncio.to_thread(rule.check, ctx)
        else:
            continue
        if result is None:
            continue
        if result.allowed:
            merged.update(result.data)
            continue
        result.rule = result.rule or rule.name
        if result.effect == DENY and ctx.log:
            await _log_async(action, ctx, result)
        return _finish(action, ctx, merged, result)
    return _finish(action, ctx, merged, None)


def evaluate_sync(action: str, ctx: PolicyContext, *,
                  only: Optional[Iterable[str]] = None) -> Decision:
    """Sync evaluation for the Celery dispatcher and the LLM providers."""
    merged: Dict[str, Any] = {}
    for rule in _select(action, ctx, only):
        if rule.check is None:
            raise RuntimeError(f"policy rule {rule.name} has no sync implementation "
                               f"(stage {ctx.stage!r})")
        result = rule.check(ctx)
        if result is None:
            continue
        if result.allowed:
            merged.update(result.data)
            continue
        result.rule = result.rule or rule.name
        if result.effect == DENY and ctx.log:
            _log_sync(action, ctx, result)
        return _finish(action, ctx, merged, result)
    return _finish(action, ctx, merged, None)


# ---------------------------------------------------------------------------
# Deny log
# ---------------------------------------------------------------------------

_recent: Dict[tuple, float] = {}
_recent_lock = threading.Lock()


def _dedupe_key(action: str, ctx: PolicyContext, d: Decision) -> tuple:
    return (action, d.rule, ctx.workspace_id, ctx.project_id, ctx.user_id,
            ctx.agent_id, ctx.agent_session_id, d.reason)


def _first_recently(key: tuple) -> bool:
    now = time.monotonic()
    with _recent_lock:
        last = _recent.get(key)
        if last is not None and now - last < DEDUPE_SECONDS:
            return False
        _recent[key] = now
        if len(_recent) > 5000:   # bounded: drop the stale half
            for k, t in list(_recent.items()):
                if now - t >= DEDUPE_SECONDS:
                    _recent.pop(k, None)
        return True


def reset_dedupe() -> None:
    with _recent_lock:
        _recent.clear()


def _json_safe(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (data or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


def _row(action: str, ctx: PolicyContext, d: Decision):
    from app.models import PolicyDecisionLog
    return PolicyDecisionLog(
        action=action, rule=d.rule or "", reason=(d.reason or "")[:1000],
        workspace_id=ctx.workspace_id, project_id=ctx.project_id, user_id=ctx.user_id,
        api_key_id=ctx.api_key_id, agent_id=ctx.agent_id,
        agent_session_id=ctx.agent_session_id, data=_json_safe(d.data) or None)


def _count_denial(ctx: PolicyContext) -> bool:
    return bool(ctx.workspace_id and ctx.agent_id and ctx.agent_session_id)


async def _log_async(action: str, ctx: PolicyContext, d: Decision) -> None:
    if not _first_recently(_dedupe_key(action, ctx, d)):
        return
    bind = getattr(ctx.session, "bind", None) if ctx.session is not None else None
    if bind is None or not hasattr(bind, "sync_engine"):
        await asyncio.to_thread(_write_sync, None, action, ctx, d)
        return
    try:
        from sqlmodel.ext.asyncio.session import AsyncSession
        # Own transaction: the caller is about to raise, which would roll back
        # anything written through its session.
        async with AsyncSession(bind) as own:
            own.add(_row(action, ctx, d))
            await own.commit()
        if _count_denial(ctx):
            from app.services.agent_controls import bump_session
            await bump_session(bind, ctx.workspace_id, ctx.agent_id, ctx.agent_session_id,
                               denials=1)
    except Exception as exc:  # noqa: BLE001 — logging must never change the outcome
        print(f"[Policy] deny not logged ({action}/{d.rule}): {exc}")


def _log_sync(action: str, ctx: PolicyContext, d: Decision) -> None:
    if not _first_recently(_dedupe_key(action, ctx, d)):
        return
    bind = None
    if ctx.session is not None and hasattr(ctx.session, "get_bind"):
        try:
            bind = ctx.session.get_bind()
        except Exception:  # noqa: BLE001
            bind = None
    _write_sync(bind, action, ctx, d)


def _write_sync(bind, action: str, ctx: PolicyContext, d: Decision) -> None:
    try:
        from sqlmodel import Session
        if bind is None:
            from app.services.llm_usage import _engine as bind
        with Session(bind) as own:
            own.add(_row(action, ctx, d))
            own.commit()
        if _count_denial(ctx):
            from app.services.agent_controls import bump_session_sync
            bump_session_sync(bind, ctx.workspace_id, ctx.agent_id, ctx.agent_session_id,
                              denials=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[Policy] deny not logged ({action}/{d.rule}): {exc}")


# ---------------------------------------------------------------------------
# Convenience API for callers
# ---------------------------------------------------------------------------

def ai_allowed_sync(workspace_id: Optional[int], *, agent_id: Optional[str] = None,
                    agent_session_id: Optional[str] = None, user_id: Optional[int] = None,
                    project_id: Optional[int] = None, log: bool = True,
                    only: Optional[Iterable[str]] = None) -> Decision:
    """May an LLM be called for this workspace (and agent session) right now?

    Covers the workspace AI opt-out, the plan's monthly token quota and the
    calling agent's kill switch / token budget. Every LLM call already passes
    through this inside the providers; feature code calls it up front only to
    tell the user why the feature is off."""
    return evaluate_sync("ai.use", PolicyContext(
        workspace_id=workspace_id, project_id=project_id, user_id=user_id,
        agent_id=agent_id, agent_session_id=agent_session_id, log=log), only=only)


async def ai_allowed(session: Any, workspace_id: Optional[int], *, principal: Any = None,
                     project_id: Optional[int] = None, log: bool = True,
                     only: Optional[Iterable[str]] = None) -> Decision:
    """Async form of ai_allowed_sync for request handlers (e.g. the assistant:
    `if not (await ai_allowed(session, ws_id, principal=principal)).allowed`).
    The ai.use rules use their own sync connection, so this runs them in a
    thread rather than on the event loop."""
    ctx = (PolicyContext.from_principal(principal) if principal is not None else PolicyContext())
    ctx.workspace_id, ctx.project_id, ctx.log = workspace_id, project_id, log
    return await asyncio.to_thread(lambda: evaluate_sync("ai.use", ctx, only=only))


__all__ = [
    "ALLOW", "DENY", "DEFER", "Decision", "PolicyContext", "Rule", "register", "rules_for",
    "catalog", "evaluate", "evaluate_sync", "ai_allowed", "ai_allowed_sync", "reset_dedupe",
]
