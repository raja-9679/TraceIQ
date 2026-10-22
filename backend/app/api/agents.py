"""Agents page API (WP14b, E21 / decision W3) — workspace admin only.

    GET    /api/workspaces/{id}/agents                       agents seen + their controls
    GET    /api/workspaces/{id}/agents/sessions?agent_id=    sessions with usage counters
    GET    /api/workspaces/{id}/agents/timeline?agent_id=&agent_session_id=
                                                             what one session did, newest first
    GET    /api/workspaces/{id}/agent-controls               controls + instance defaults
    PUT    /api/workspaces/{id}/agent-controls               upsert one agent's (or "*") controls
    DELETE /api/workspaces/{id}/agent-controls?agent_id=     remove them (back to inherited)

The activity view spans every project in the workspace, so it is a workspace
admin read (like the audit history): an editor with access to one project must
not see what an agent did in another. Every query is pinned to the workspace's
projects. Control changes are humans-only and audited — an agent must not be
able to unblock itself or raise its own budget.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.policies import require_workspace_admin
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    AgentControl, AgentSession, CaseProposal, LLMUsageEvent, PolicyDecisionLog, Project,
    TestCase, TestCaseRevision, TestRun, TestSuite,
)
from app.services import agent_controls as ac
from app.services.audit import record as audit_record

router = APIRouter()

TIMELINE_LIMIT = 500
_LIMIT_MAX = 1_000_000_000


def _is_pseudo_agent(agent_id: str) -> bool:
    """TestRun.agent_id values that are not agents: scheduled runs are tagged
    `schedule:<id>` (monitor_tasks reads that back), and demo workspaces
    seeded before 2026-10-22 tagged their runs `demo-seed`."""
    return agent_id.startswith("schedule:") or agent_id == "demo-seed"


def _projects(workspace_id: int):
    return select(Project.id).where(Project.workspace_id == workspace_id)


def _control_read(row: Optional[AgentControl]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"agent_id": row.agent_id, "blocked": row.blocked, "blocked_reason": row.blocked_reason,
            **{f: getattr(row, f) for f in ac.LIMIT_FIELDS},
            "updated_at": row.updated_at, "updated_by_id": row.updated_by_id}


@router.get("/workspaces/{workspace_id}/agents")
async def list_agents(workspace_id: int, session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    projects = _projects(workspace_id)
    agents: Dict[str, Dict[str, Any]] = {}

    def slot(agent_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not agent_id:
            return None
        return agents.setdefault(agent_id, {
            "agent_id": agent_id, "sessions": 0, "runs": 0, "proposals": 0, "llm_tokens": 0,
            "cases_created": 0, "denials": 0, "last_seen_at": None, "control": None})

    def seen(entry, at):
        if at and (entry["last_seen_at"] is None or at > entry["last_seen_at"]):
            entry["last_seen_at"] = at

    # Sessions carry the budget counters.
    for row in (await session.exec(select(
            AgentSession.agent_id, func.count(), func.sum(AgentSession.llm_tokens),
            func.sum(AgentSession.denials), func.max(AgentSession.last_seen_at))
            .where(AgentSession.workspace_id == workspace_id)
            .group_by(AgentSession.agent_id))).all():
        entry = slot(row[0])
        entry["sessions"], entry["llm_tokens"], entry["denials"] = row[1], int(row[2] or 0), int(row[3] or 0)
        seen(entry, row[4])
    # Records are the truth for what was done (they include work from before
    # sessions were tracked, and from callers that send no session id).
    for agent_id, n, last in (await session.exec(select(
            TestRun.agent_id, func.count(), func.max(TestRun.created_at))
            .where(TestRun.project_id.in_(projects), TestRun.agent_id.is_not(None))
            .group_by(TestRun.agent_id))).all():
        if _is_pseudo_agent(agent_id):
            continue
        entry = slot(agent_id)
        entry["runs"] = n
        seen(entry, last)
    for agent_id, n, last in (await session.exec(select(
            CaseProposal.created_by_agent_id, func.count(), func.max(CaseProposal.created_at))
            .where(CaseProposal.project_id.in_(projects), CaseProposal.created_by_agent_id.is_not(None))
            .group_by(CaseProposal.created_by_agent_id))).all():
        entry = slot(agent_id)
        entry["proposals"] = n
        seen(entry, last)
    for agent_id, n, last in (await session.exec(select(
            TestCase.created_by_agent_id, func.count(), func.max(TestCase.created_at))
            .where(TestCase.project_id.in_(projects), TestCase.created_by_agent_id.is_not(None))
            .group_by(TestCase.created_by_agent_id))).all():
        entry = slot(agent_id)
        entry["cases_created"] = n
        seen(entry, last)
    controls = (await session.exec(select(AgentControl).where(
        AgentControl.workspace_id == workspace_id))).all()
    for row in controls:
        if row.agent_id == ac.WILDCARD:
            continue
        slot(row.agent_id)["control"] = _control_read(row)
    default_row = next((r for r in controls if r.agent_id == ac.WILDCARD), None)
    instance = ac.instance_limits()
    for agent_id, entry in agents.items():
        agent_row = next((r for r in controls if r.agent_id == agent_id), None)
        entry["effective_limits"] = ac.resolve_limits(agent_row, default_row, instance)
        entry["blocked"] = bool((agent_row and agent_row.blocked) or (default_row and default_row.blocked))
    items = sorted(agents.values(), key=lambda e: e["last_seen_at"] or datetime.min, reverse=True)
    return {"workspace_id": workspace_id, "agents": items,
            "workspace_default": _control_read(default_row), "instance_defaults": instance}


@router.get("/workspaces/{workspace_id}/agents/sessions")
async def list_sessions(workspace_id: int, agent_id: Optional[str] = None,
                        limit: int = Query(100, ge=1, le=500),
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    query = select(AgentSession).where(AgentSession.workspace_id == workspace_id)
    if agent_id:
        query = query.where(AgentSession.agent_id == agent_id)
    rows = (await session.exec(query.order_by(AgentSession.last_seen_at.desc()).limit(limit))).all()
    controls = (await session.exec(select(AgentControl).where(
        AgentControl.workspace_id == workspace_id))).all()
    default_row = next((r for r in controls if r.agent_id == ac.WILDCARD), None)
    instance = ac.instance_limits()
    out = []
    for r in rows:
        agent_row = next((c for c in controls if c.agent_id == r.agent_id), None)
        limits = ac.resolve_limits(agent_row, default_row, instance)
        usage = ac.usage_of(r)
        exhausted = [k for k in ("run", "proposal", "llm") if ac.budget_exceeded(limits, usage, k)]
        out.append({"agent_id": r.agent_id, "agent_session_id": r.agent_session_id,
                    "runs": r.runs, "proposals": r.proposals, "llm_tokens": r.llm_tokens,
                    "denials": r.denials, "first_seen_at": r.first_seen_at,
                    "last_seen_at": r.last_seen_at, "limits": limits, "exhausted": exhausted})
    return {"workspace_id": workspace_id, "sessions": out}


def _item(kind: str, at, title: str, *, entity_type: str, entity_id, project_id=None,
          link: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"kind": kind, "at": at, "title": title, "entity_type": entity_type,
            "entity_id": entity_id, "project_id": project_id, "link": link, **(extra or {})}


@router.get("/workspaces/{workspace_id}/agents/timeline")
async def session_timeline(workspace_id: int, agent_id: str, agent_session_id: str,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    """Everything one agent session did in this workspace, newest first."""
    await require_workspace_admin(session, principal, workspace_id)
    projects = _projects(workspace_id)
    items: List[Dict[str, Any]] = []
    sess = (await session.exec(select(AgentSession).where(
        AgentSession.workspace_id == workspace_id, AgentSession.agent_id == agent_id,
        AgentSession.agent_session_id == agent_session_id))).first()

    for r in (await session.exec(select(TestRun).where(
            TestRun.project_id.in_(projects), TestRun.agent_session_id == agent_session_id,
            TestRun.agent_id == agent_id).order_by(TestRun.created_at.desc())
            .limit(TIMELINE_LIMIT))).all():
        status = getattr(r.status, "value", r.status)
        items.append(_item("run_started", r.created_at,
                           f"Run #{r.id} {r.test_case_name or r.suite_name or ''}".strip(),
                           entity_type="run", entity_id=r.id, project_id=r.project_id,
                           link=f"/runs/{r.id}", extra={"status": status,
                                                        "git_commit": r.git_commit}))
    for c in (await session.exec(select(TestCase).where(
            TestCase.project_id.in_(projects), TestCase.agent_session_id == agent_session_id)
            .limit(TIMELINE_LIMIT))).all():
        items.append(_item("case_created", c.created_at, f"Case “{c.name}” created",
                           entity_type="case", entity_id=c.id, project_id=c.project_id,
                           link=f"/suites/{c.test_suite_id}/cases/{c.id}/edit"))
    for s in (await session.exec(select(TestSuite).where(
            TestSuite.project_id.in_(projects), TestSuite.agent_session_id == agent_session_id)
            .limit(TIMELINE_LIMIT))).all():
        items.append(_item("suite_created", s.created_at, f"Suite “{s.name}” created",
                           entity_type="suite", entity_id=s.id, project_id=s.project_id,
                           link=f"/suites/{s.id}"))
    for p in (await session.exec(select(CaseProposal).where(
            CaseProposal.project_id.in_(projects), CaseProposal.agent_session_id == agent_session_id)
            .limit(TIMELINE_LIMIT))).all():
        action = getattr(p.action, "value", p.action)
        items.append(_item("proposal_made", p.created_at, f"Proposal #{p.id} ({action})",
                           entity_type="proposal", entity_id=p.id, project_id=p.project_id,
                           link="/proposals", extra={"status": p.status}))
        if p.decided_at:
            how = "auto-applied" if p.status == "accepted" and p.decided_by_id is None else p.status
            items.append(_item("proposal_decided", p.decided_at, f"Proposal #{p.id} {how}",
                               entity_type="proposal", entity_id=p.id, project_id=p.project_id,
                               link="/proposals", extra={"status": p.status}))
    # Case edits carry the agent id but not the session: attribute the ones
    # made by this agent inside the session's lifetime.
    if sess is not None:
        rev_rows = (await session.exec(
            select(TestCaseRevision, TestCase.project_id, TestCase.test_suite_id, TestCase.name)
            .join(TestCase, TestCase.id == TestCaseRevision.test_case_id)
            .where(TestCase.project_id.in_(projects),
                   TestCaseRevision.changed_by_agent_id == agent_id,
                   TestCaseRevision.change_source != "create",
                   TestCaseRevision.created_at >= sess.first_seen_at,
                   TestCaseRevision.created_at <= sess.last_seen_at)
            .limit(TIMELINE_LIMIT))).all()
        for rev, project_id, suite_id, name in rev_rows:
            items.append(_item("case_edited", rev.created_at,
                               f"Case “{name}” edited (revision {rev.revision_number}, {rev.change_source})",
                               entity_type="case", entity_id=rev.test_case_id, project_id=project_id,
                               link=f"/suites/{suite_id}/cases/{rev.test_case_id}/edit"))
    llm = (await session.exec(select(LLMUsageEvent).where(
        LLMUsageEvent.workspace_id == workspace_id,
        LLMUsageEvent.agent_session_id == agent_session_id,
        LLMUsageEvent.agent_id == agent_id).order_by(LLMUsageEvent.created_at.desc())
        .limit(TIMELINE_LIMIT))).all()
    for e in llm:
        items.append(_item("llm_call", e.created_at,
                           f"LLM {e.feature}: {e.total_tokens} tokens ({e.provider})",
                           entity_type="llm_usage", entity_id=e.id, project_id=e.project_id,
                           link=f"/runs/{e.run_id}" if e.run_id else None,
                           extra={"tokens": e.total_tokens, "success": e.success}))
    for d in (await session.exec(select(PolicyDecisionLog).where(
            PolicyDecisionLog.workspace_id == workspace_id,
            PolicyDecisionLog.agent_id == agent_id,
            PolicyDecisionLog.agent_session_id == agent_session_id)
            .order_by(PolicyDecisionLog.created_at.desc()).limit(TIMELINE_LIMIT))).all():
        items.append(_item("denied", d.created_at, f"Refused by {d.rule}: {d.reason}",
                           entity_type="policy_decision", entity_id=d.id, project_id=d.project_id,
                           extra={"action": d.action, "rule": d.rule}))
    items.sort(key=lambda i: i["at"] or datetime.min, reverse=True)
    return {
        "workspace_id": workspace_id, "agent_id": agent_id, "agent_session_id": agent_session_id,
        "session": None if sess is None else {
            "runs": sess.runs, "proposals": sess.proposals, "llm_tokens": sess.llm_tokens,
            "denials": sess.denials, "first_seen_at": sess.first_seen_at,
            "last_seen_at": sess.last_seen_at},
        "items": items[:TIMELINE_LIMIT],
    }


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

class AgentControlBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=ac.MAX_ID_LEN)
    blocked: bool = False
    blocked_reason: Optional[str] = Field(default=None, max_length=500)
    max_runs: Optional[int] = Field(default=None, ge=0, le=_LIMIT_MAX)
    max_llm_tokens: Optional[int] = Field(default=None, ge=0, le=_LIMIT_MAX)
    max_proposals: Optional[int] = Field(default=None, ge=0, le=_LIMIT_MAX)
    max_minutes: Optional[int] = Field(default=None, ge=0, le=_LIMIT_MAX)


def _humans_only(principal: AuthPrincipal) -> None:
    if principal.api_key is not None:
        raise HTTPException(status_code=403, detail="API keys cannot change agent controls")


@router.get("/workspaces/{workspace_id}/agent-controls")
async def get_controls(workspace_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    await require_workspace_admin(session, principal, workspace_id)
    rows = (await session.exec(select(AgentControl).where(
        AgentControl.workspace_id == workspace_id).order_by(AgentControl.agent_id))).all()
    return {"workspace_id": workspace_id, "controls": [_control_read(r) for r in rows],
            "instance_defaults": ac.instance_limits(), "wildcard": ac.WILDCARD}


@router.put("/workspaces/{workspace_id}/agent-controls")
async def put_control(workspace_id: int, body: AgentControlBody = Body(...),
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    _humans_only(principal)
    await require_workspace_admin(session, principal, workspace_id)
    agent_id = ac.clean_id(body.agent_id)
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    row = (await session.exec(select(AgentControl).where(
        AgentControl.workspace_id == workspace_id, AgentControl.agent_id == agent_id))).first()
    before = _control_read(row) or {}
    if row is None:
        row = AgentControl(workspace_id=workspace_id, agent_id=agent_id)
    row.blocked = body.blocked
    row.blocked_reason = (body.blocked_reason or "").strip() or None if body.blocked else None
    for field in ac.LIMIT_FIELDS:
        setattr(row, field, getattr(body, field))
    row.updated_by_id = principal.user.id
    row.updated_at = datetime.utcnow()
    session.add(row)
    await session.flush()
    after = _control_read(row)
    changes = {k: {"old": before.get(k), "new": after[k]}
               for k in ("blocked", "blocked_reason", *ac.LIMIT_FIELDS) if before.get(k) != after[k]}
    if bool(before.get("blocked")) != row.blocked:
        action = "agent_blocked" if row.blocked else "agent_unblocked"
    else:
        action = "agent_controls_updated"
    await audit_record(session, entity_type="agent_control", entity_id=row.id, action=action,
                       workspace_id=workspace_id, principal=principal,
                       changes={"agent_id": agent_id, **changes})
    await session.commit()
    await session.refresh(row)
    return _control_read(row)


@router.delete("/workspaces/{workspace_id}/agent-controls")
async def delete_control(workspace_id: int, agent_id: str,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    _humans_only(principal)
    await require_workspace_admin(session, principal, workspace_id)
    row = (await session.exec(select(AgentControl).where(
        AgentControl.workspace_id == workspace_id, AgentControl.agent_id == agent_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No controls for this agent")
    before = _control_read(row)
    before.pop("updated_at", None)
    await audit_record(session, entity_type="agent_control", entity_id=row.id,
                       action="agent_controls_removed", workspace_id=workspace_id,
                       principal=principal, changes={"agent_id": agent_id, "before": before})
    await session.delete(row)
    await session.commit()
    return {"deleted": True, "agent_id": agent_id}
