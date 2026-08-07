"""Workspace-scoped audit history: read, export, verify.

The only way to read the trail used to be `GET /api/audit/{entity_type}/{id}`,
which handled two entity types and fell open on everything else. There was no
way to answer "what happened in this workspace last month", and no way to get
the trail out of the product at all — so the compliance answer to "can you
produce your audit log for the auditor" was "not without database access".

Three endpoints:

    GET /api/workspaces/{id}/audit          paginated, filterable
    GET /api/workspaces/{id}/audit/export   CSV for SIEM ingestion
    GET /api/workspaces/{id}/audit/verify   chain integrity

`verify` is the one that turns the hash chain from a nice property into
evidence. A chain nobody checks proves nothing, so this makes checking a
one-request operation an auditor can run themselves.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import AuditLog, AuditLogRead, UserWorkspace
from app.services.audit import verify_chain
from app.services.rbac_service import rbac_service

router = APIRouter()

#: Columns exported to CSV, in order. Stable because SIEM ingestion pipelines
#: are built against a header row and silently mis-map when it changes.
EXPORT_COLUMNS = (
    "id", "timestamp", "workspace_id", "entity_type", "entity_id", "action",
    "user_id", "actor_type", "actor_label", "ip_address", "user_agent",
    "changes", "row_hash",
)


async def _require_workspace_admin(
    workspace_id: int, principal: AuthPrincipal, session: AsyncSession
) -> None:
    """Audit history is an admin-level read.

    It reveals who did what and from where across the whole workspace, which is
    more than an editor needs and exactly what an attacker with a stolen editor
    session would want in order to find out what they can get away with.
    """
    membership = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.user_id == principal.user.id,
            UserWorkspace.workspace_id == workspace_id,
        )
    )
    if not membership.first():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    if not await rbac_service.has_permission(
        session, principal.user.id, "workspace:manage_users", workspace_id=workspace_id
    ):
        raise HTTPException(
            status_code=403, detail="Workspace admin access required to read audit history")


def _base_query(
    workspace_id: int,
    action: Optional[str],
    entity_type: Optional[str],
    user_id: Optional[int],
    since: Optional[datetime],
    until: Optional[datetime],
):
    query = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    if since:
        query = query.where(AuditLog.timestamp >= since)
    if until:
        query = query.where(AuditLog.timestamp <= until)
    return query


@router.get("/workspaces/{workspace_id}/audit", response_model=List[AuditLogRead])
async def list_workspace_audit(
    workspace_id: int,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    user_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Audit history for a workspace, newest first."""
    await _require_workspace_admin(workspace_id, principal, session)
    query = (
        _base_query(workspace_id, action, entity_type, user_id, since, until)
        .order_by(AuditLog.timestamp.desc())
        .offset(offset).limit(limit)
    )
    result = await session.exec(query)
    return result.all()


@router.get("/workspaces/{workspace_id}/audit/export")
async def export_workspace_audit(
    workspace_id: int,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    user_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """CSV export for SIEM ingestion or an auditor.

    Ordered by id ascending — chain order, not display order — so the export
    can be verified independently by recomputing the hashes.
    """
    await _require_workspace_admin(workspace_id, principal, session)
    query = _base_query(
        workspace_id, action, entity_type, user_id, since, until
    ).order_by(AuditLog.id)
    result = await session.exec(query)
    rows = result.all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([getattr(row, column, "") for column in EXPORT_COLUMNS])
    buffer.seek(0)

    stamp = datetime.utcnow().strftime("%Y%m%d")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="traceiq-audit-ws{workspace_id}-{stamp}.csv"'
        },
    )


@router.get("/workspaces/{workspace_id}/audit/verify")
async def verify_workspace_audit(
    workspace_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Check the hash chain over this workspace's history.

    Note the chain is global, not per-workspace — each row commits to the row
    before it across the whole table — so a workspace-filtered slice cannot be
    verified in isolation. This walks the full chain and then reports whether
    the break, if any, falls inside this workspace.
    """
    await _require_workspace_admin(workspace_id, principal, session)

    result = await session.exec(select(AuditLog).order_by(AuditLog.id))
    rows = result.all()
    ok, broken_index = verify_chain(rows)

    if ok:
        return {
            "verified": True,
            "rows_checked": len(rows),
            "detail": "Every audit row is intact and correctly linked to its predecessor.",
        }

    broken = rows[broken_index] if broken_index is not None and broken_index < len(rows) else None
    unverifiable = broken is not None and not broken.row_hash
    return {
        "verified": False,
        "rows_checked": len(rows),
        "first_bad_row_id": getattr(broken, "id", None),
        "first_bad_row_timestamp": getattr(broken, "timestamp", None),
        "in_this_workspace": getattr(broken, "workspace_id", None) == workspace_id,
        "detail": (
            "This row predates hash chaining, so the trail cannot be verified from "
            "here backwards. Rows written after the upgrade are covered."
            if unverifiable else
            "The chain breaks at this row: it was modified, deleted, or reordered "
            "after being written. Every row after it is also unverifiable."
        ),
    }
