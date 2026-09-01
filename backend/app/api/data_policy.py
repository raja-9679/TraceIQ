"""Per-project data-capture policy: read and update.

`Project.data_policy` governs what a run is allowed to record and what is
scrubbed from it. The enforcement landed with workstream B — resolved at
dispatch, clamped to the instance ceiling, honoured by the worker — but nothing
read or wrote the column, so the only way to configure a project was direct
SQL. That made a correct mechanism unusable.

Two endpoints:

    GET /api/projects/{id}/data-policy   stored + effective + what it permits
    PUT /api/projects/{id}/data-policy   partial update, admin only

The GET returns the *effective* policy alongside the stored one because the
instance-wide `MAX_CAPTURE_LEVEL` can clamp it. A screen that displayed `full`
while the instance capped at `standard` would tell an operator they are
recording video and traces when they are not, which is worse than having no
screen at all.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Project
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.data_policy import describe_for_project, validate_data_policy
from app.services.instance_settings import effective as instance_effective

router = APIRouter()


async def _project_or_404(project_id: int, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _ceiling() -> Any:
    """The instance capture ceiling. A DB failure must not break the read."""
    try:
        return instance_effective("MAX_CAPTURE_LEVEL")
    except Exception:  # noqa: BLE001
        return None


@router.get("/projects/{project_id}/data-policy")
async def get_data_policy(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
) -> Dict[str, Any]:
    """What this project records, and what it is actually permitted to record.

    Viewer-level: knowing whether video is being captured is useful to anyone
    who reads the results, and the response contains no secrets — only field
    *names* to redact, never values.
    """
    project = await _project_or_404(project_id, session)
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    return describe_for_project(project, ceiling=_ceiling())


@router.put("/projects/{project_id}/data-policy")
async def set_data_policy(
    project_id: int,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
) -> Dict[str, Any]:
    """Update the policy. Admin-only, partial, and audited.

    Admin rather than editor: raising the capture level decides how much
    customer data this project writes to disk, which is a different kind of
    decision from editing a test.

    Partial by design — only the keys present are changed. Omitted keys are
    left alone so that editing the retention window cannot silently switch off
    redaction, the same reasoning as the proposal-policy endpoint.
    """
    project = await _project_or_404(project_id, session)
    if not await access_service.has_project_access(
        principal.user.id, project_id, session, min_role="admin"
    ):
        raise HTTPException(
            status_code=403,
            detail="Admin role required to change the data-capture policy")

    try:
        patch = validate_data_policy(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    before = dict(project.data_policy or {})
    merged = {**before, **patch}
    # Reassign rather than mutate: SQLAlchemy does not track in-place changes to
    # a JSON column, so mutating `project.data_policy` would not be persisted.
    project.data_policy = merged
    session.add(project)

    changed = {k: {"from": before.get(k), "to": v}
               for k, v in patch.items() if before.get(k) != v}
    if changed:
        await audit_record(
            session,
            entity_type="project", entity_id=project_id,
            action="update_data_policy",
            workspace_id=project.workspace_id,
            principal=principal, request=request,
            changes=changed,
        )

    await session.commit()
    await session.refresh(project)
    return describe_for_project(project, ceiling=_ceiling())
