"""Cost intelligence in money (WP12d). Math and rules: app/services/cost.py.

  GET /costs/workspaces/{workspace_id}?days=30[&project_id=]   report (viewer)
  GET /projects/{project_id}/cost-budget                        budget (viewer)
  PUT /projects/{project_id}/cost-budget                        set / clear (project admin)

A workspace report covers only the projects the caller can read in that
workspace (and only its own project for a project-scoped API key). LLM calls
with no project — workspace-level generation, say — are shown to workspace
admins only: a project member's view must not include other teams' spend.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project, ProjectCostBudget, UserWorkspace, Workspace
from app.services import cost as cost_svc
from app.services.access_service import _ROLE_MAP, _workspace_role_level, access_service
from app.services.audit import record as audit_record

router = APIRouter()

MAX_BUDGET = 1_000_000_000


async def _is_workspace_admin(session: AsyncSession, user_id: int, workspace_id: int) -> bool:
    # Held to the caller's API-key scope (see access_service.is_workspace_admin).
    return await access_service.is_workspace_admin(user_id, workspace_id, session)


async def readable_projects(session: AsyncSession, principal: AuthPrincipal,
                            workspace_id: int) -> List[Project]:
    projects = (await session.exec(select(Project).where(
        Project.workspace_id == workspace_id).order_by(Project.name))).all()
    out = []
    for p in projects:
        if principal.api_key is not None and principal.api_key.project_id not in (None, p.id):
            continue
        if await access_service.has_project_access(principal.user.id, p.id, session):
            out.append(p)
    return out


@router.get("/costs/workspaces/{workspace_id}")
async def cost_report(workspace_id: int, days: int = 30, project_id: Optional[int] = None,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    if not await session.get(Workspace, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    projects = await readable_projects(session, principal, workspace_id)
    is_admin = (principal.api_key is None or principal.api_key.project_id is None) and \
        await _is_workspace_admin(session, principal.user.id, workspace_id)
    if not projects and not is_admin:
        raise HTTPException(status_code=403, detail="Access denied")
    if project_id is not None:
        projects = [p for p in projects if p.id == project_id]
        if not projects:
            raise HTTPException(status_code=403, detail="Access denied")
    days = max(1, min(int(days), 365))
    since, until, day_list = cost_svc.window(days)
    pids = [p.id for p in projects]
    # Workspace-level LLM use belongs in the workspace view, for its admins.
    unassigned_ws = workspace_id if (is_admin and project_id is None) else None

    prices = cost_svc.load_prices()
    rows = await session.run_sync(lambda s: cost_svc.collect(
        s, project_ids=pids, since=since, until=until,
        workspace_id_for_unassigned=unassigned_ws))
    report = cost_svc.build_report(prices, days=day_list, **rows)

    # Per-project window totals and this month's budget position.
    window_spend = cost_svc.project_spend(prices, rows["llm_rows"], rows["browser_rows"])
    mstart = cost_svc.month_start()
    month_rows = await session.run_sync(lambda s: cost_svc.collect(
        s, project_ids=pids, since=mstart, until=datetime.utcnow(), with_runs=False))
    month_spend = cost_svc.project_spend(prices, month_rows["llm_rows"], month_rows["browser_rows"])
    budgets = {b.project_id: b for b in (await session.exec(select(ProjectCostBudget).where(
        ProjectCostBudget.project_id.in_(pids or [-1])))).all()}
    cur = prices.currency
    by_project = []
    for p in projects:
        b = budgets.get(p.id)
        spent = month_spend.get(p.id, Decimal(0))
        by_project.append({
            "project_id": p.id, "name": p.name,
            "total": cost_svc.money(window_spend.get(p.id, Decimal(0)), cur),
            "budget": b.monthly_amount if b else None,
            "month_spent": cost_svc.money(spent, cur),
            "budget_pct": (round(float(spent * 100 / Decimal(str(b.monthly_amount))), 1)
                           if b and b.monthly_amount and prices.enabled else None),
        })
    if unassigned_ws is not None:
        report["unassigned_total"] = cost_svc.money(window_spend.get(None, Decimal(0)), cur)
    names = {p.id: p.name for p in projects}
    for r in report["top_runs"]:
        r["project_name"] = names.get(r["project_id"])
    return {"workspace_id": workspace_id, "project_id": project_id, "window_days": days,
            "since": since, "until": until, "month_start": mstart,
            "projects": by_project, "includes_unassigned": unassigned_ws is not None,
            **report}


async def _project(session: AsyncSession, principal: AuthPrincipal, project_id: int,
                   min_role: str) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None:
        from app.api.jobs import api_key_allows_project
        if not api_key_allows_project(principal.api_key, project_id):
            raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied")
    return project


def _budget_view(project_id: int, b: Optional[ProjectCostBudget]) -> Dict[str, Any]:
    return {"project_id": project_id, "monthly_amount": b.monthly_amount if b else None,
            "currency": cost_svc.load_prices().currency,
            "updated_at": b.updated_at if b else None,
            "thresholds": list(cost_svc.BUDGET_THRESHOLDS)}


@router.get("/projects/{project_id}/cost-budget")
async def get_budget(project_id: int, session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    await _project(session, principal, project_id, "viewer")
    b = (await session.exec(select(ProjectCostBudget).where(
        ProjectCostBudget.project_id == project_id))).first()
    return _budget_view(project_id, b)


class BudgetIn(BaseModel):
    # None clears the budget.
    monthly_amount: Optional[float] = None


@router.put("/projects/{project_id}/cost-budget")
async def set_budget(project_id: int, body: BudgetIn, request: Request,
                     session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project(session, principal, project_id, "admin")
    amount = body.monthly_amount
    if amount is not None:
        import math
        if not math.isfinite(amount) or amount <= 0 or amount > MAX_BUDGET:
            raise HTTPException(status_code=400, detail="monthly_amount must be a positive number")
        amount = round(float(amount), 2)
    b = (await session.exec(select(ProjectCostBudget).where(
        ProjectCostBudget.project_id == project_id))).first()
    before = b.monthly_amount if b else None
    if amount is None:
        if b:
            await session.delete(b)
            b = None
    else:
        if b is None:
            b = ProjectCostBudget(project_id=project_id, monthly_amount=amount)
        b.monthly_amount = amount
        b.updated_by_id = principal.user.id
        b.updated_at = datetime.utcnow()
        session.add(b)
    await audit_record(session, entity_type="project", entity_id=project_id,
                       action="cost_budget", principal=principal, request=request,
                       workspace_id=project.workspace_id,
                       changes={"from": before, "to": amount})
    await session.commit()
    return _budget_view(project_id, b)
