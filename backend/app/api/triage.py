"""Failure-triage API: list/inspect/triage failure clusters, and file one
ticket per cluster (item 2 — turns N identical failures into one action)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user, get_current_principal, AuthPrincipal
from app.services.access_service import access_service
from app.models import (
    User, TestRun, TestCaseResult,
    FailureCluster, FailureClusterRead, FailureClusterUpdate, FailureClusterDetail,
    FailureOccurrenceRead, IssueTrackerConfig, IssueTicket, UserWorkspace, Project,
)

router = APIRouter()

_VALID_STATUS = {"open", "investigating", "resolved", "ignored"}


@router.get("/projects/{project_id}/failure-clusters", response_model=List[FailureClusterRead])
async def list_clusters(project_id: int, status: Optional[str] = Query(None),
                        category: Optional[str] = Query(None),
                        session: AsyncSession = Depends(get_session),
                        current_user: User = Depends(get_current_user)):
    if not await access_service.has_project_access(current_user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    stmt = select(FailureCluster).where(FailureCluster.project_id == project_id)
    if status:
        stmt = stmt.where(FailureCluster.status == status)
    if category:
        stmt = stmt.where(FailureCluster.category == category)
    rows = (await session.exec(stmt.order_by(FailureCluster.last_seen_at.desc()))).all()
    return rows


@router.get("/failure-clusters/{cluster_id}", response_model=FailureClusterDetail)
async def get_cluster(cluster_id: int, session: AsyncSession = Depends(get_session),
                      current_user: User = Depends(get_current_user)):
    cluster = await session.get(FailureCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if not await access_service.has_project_access(current_user.id, cluster.project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")

    rows = (await session.exec(
        select(TestCaseResult, TestRun)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestCaseResult.cluster_id == cluster_id)
        .order_by(TestCaseResult.id.desc()).limit(50))).all()
    occurrences = [
        FailureOccurrenceRead(result_id=res.id, run_id=run.id, test_name=res.test_name,
                              status=res.status, created_at=run.created_at)
        for res, run in rows
    ]
    return FailureClusterDetail(**cluster.model_dump(), occurrences=occurrences)


@router.patch("/failure-clusters/{cluster_id}", response_model=FailureClusterRead)
async def update_cluster(cluster_id: int, body: FailureClusterUpdate,
                         session: AsyncSession = Depends(get_session),
                         current_user: User = Depends(get_current_user)):
    cluster = await session.get(FailureCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if not await access_service.has_project_access(current_user.id, cluster.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUS)}")
    for k, v in data.items():
        setattr(cluster, k, v)
    # Stamp resolution time for MTTR; clear if reopened.
    if "status" in data:
        from datetime import datetime as _dt
        if data["status"] == "resolved":
            if not cluster.resolved_at:
                cluster.resolved_at = _dt.utcnow()
        else:
            cluster.resolved_at = None
    session.add(cluster)
    await session.commit()
    await session.refresh(cluster)
    return cluster


@router.post("/failure-clusters/{cluster_id}/ticket")
async def create_cluster_ticket(cluster_id: int, body: dict = Body(...),
                                session: AsyncSession = Depends(get_session),
                                principal: AuthPrincipal = Depends(get_current_principal)):
    """File a single ticket for the whole cluster (attaching the latest
    occurrence's artifacts). One root cause → one ticket."""
    cluster = await session.get(FailureCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if not await access_service.has_project_access(principal.user.id, cluster.project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    config_id = body.get("config_id")
    if not config_id:
        raise HTTPException(status_code=400, detail="config_id is required")
    config = await session.get(IssueTrackerConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Tracker config not found")
    member = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.workspace_id == config.workspace_id,
        UserWorkspace.user_id == principal.user.id))).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of the tracker's workspace")

    summary = body.get("summary") or f"[TraceIQ] {cluster.category}: {cluster.title[:120]}"
    description = (f"Failure cluster #{cluster.id} — {cluster.occurrence_count} occurrence(s), "
                  f"category **{cluster.category}**.\n\n{cluster.sample_error or cluster.title}")

    ticket = IssueTicket(
        config_id=config.id, workspace_id=config.workspace_id, run_id=cluster.last_run_id,
        cluster_id=cluster.id, provider=config.provider, summary=summary,
        status="pending", created_by_id=principal.user.id)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    try:
        from app.tasks.ticket_tasks import create_issue_ticket
        create_issue_ticket.delay(ticket.id, description=description,
                                  attach_trace=True, attach_video=True, attach_screenshots=True)
    except Exception as e:  # noqa: BLE001
        ticket.status = "error"
        ticket.error = f"Could not queue: {e}"
        session.add(ticket)
        await session.commit()

    return {"ticket_id": ticket.id, "status": ticket.status}
