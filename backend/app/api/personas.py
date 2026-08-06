"""Persona CRUD — workspace-scoped reusable session artifacts.

A Persona is a saved Playwright `storageState` + auth headers + optional
auto-login recipe. Runs that set `persona_id` get the session hydrated into
the browser context before the first step executes.

Surface:
    POST   /api/workspaces/{ws}/personas             create
    GET    /api/workspaces/{ws}/personas             list
    GET    /api/workspaces/{ws}/personas/{id}        fetch (includes session_state)
    PATCH  /api/workspaces/{ws}/personas/{id}        update
    DELETE /api/workspaces/{ws}/personas/{id}        delete
    POST   /api/workspaces/{ws}/personas/{id}/refresh
        → kicks off a Celery task that runs `login_steps` and stores the new
          `session_state`. Returns immediately; clients poll the persona to
          see `last_refreshed_at` change.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    Persona,
    PersonaCreate,
    PersonaRead,
    UserWorkspace,
)

router = APIRouter()


class PersonaPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    session_state: Optional[dict] = None
    auth_headers: Optional[dict] = None
    login_steps: Optional[List[dict]] = None
    refresh_after_hours: Optional[int] = None


async def _ensure_member(workspace_id: int, user_id: int, session: AsyncSession) -> UserWorkspace:
    res = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.user_id == user_id,
        )
    )
    m = res.first()
    if not m:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return m


async def _ensure_editor(workspace_id: int, user_id: int, session: AsyncSession) -> UserWorkspace:
    """Editor+ gate for persona routes that read or write session_state — the
    saved storage-state is live session cookies/tokens for the customer's app,
    so a read-only member must not be able to exfiltrate or overwrite it."""
    from app.services.access_service import _workspace_role_level
    m = await _ensure_member(workspace_id, user_id, session)
    if await _workspace_role_level(m, session) < 2:  # 2 == editor
        raise HTTPException(status_code=403, detail="Editor access required")
    return m


def _to_read(p: Persona) -> PersonaRead:
    return PersonaRead(
        id=p.id, workspace_id=p.workspace_id, project_id=p.project_id,
        name=p.name, description=p.description, auth_headers=p.auth_headers,
        refresh_after_hours=p.refresh_after_hours,
        last_refreshed_at=p.last_refreshed_at,
        created_at=p.created_at, updated_at=p.updated_at,
    )


@router.post(
    "/workspaces/{workspace_id}/personas",
    response_model=PersonaRead,
)
async def create_persona(
    workspace_id: int,
    body: PersonaCreate,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> PersonaRead:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id mismatch")
    await _ensure_editor(workspace_id, principal.user.id, session)
    # A persona's project must live in this workspace (tenant isolation).
    if body.project_id is not None:
        from app.models import Project
        proj = await session.get(Project, body.project_id)
        if not proj or proj.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Project not found in this workspace")

    p = Persona(
        workspace_id=workspace_id,
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        session_state=body.session_state,
        auth_headers=body.auth_headers or {},
        login_steps=body.login_steps or [],
        refresh_after_hours=body.refresh_after_hours,
        created_by_id=principal.user.id,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _to_read(p)


@router.get(
    "/workspaces/{workspace_id}/personas",
    response_model=List[PersonaRead],
)
async def list_personas(
    workspace_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[PersonaRead]:
    await _ensure_member(workspace_id, principal.user.id, session)
    res = await session.exec(
        select(Persona)
        .where(Persona.workspace_id == workspace_id)
        .order_by(Persona.created_at.desc())
    )
    return [_to_read(p) for p in res.all()]


@router.get("/workspaces/{workspace_id}/personas/{persona_id}")
async def get_persona(
    workspace_id: int,
    persona_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Includes session_state (live app credentials) — editor+ only."""
    await _ensure_editor(workspace_id, principal.user.id, session)
    p = await session.get(Persona, persona_id)
    if not p or p.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Persona not found")
    return {
        **_to_read(p).model_dump(),
        "session_state": p.session_state,
        "login_steps": p.login_steps,
    }


@router.patch(
    "/workspaces/{workspace_id}/personas/{persona_id}",
    response_model=PersonaRead,
)
async def patch_persona(
    workspace_id: int,
    persona_id: int,
    body: PersonaPatch,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> PersonaRead:
    await _ensure_editor(workspace_id, principal.user.id, session)
    p = await session.get(Persona, persona_id)
    if not p or p.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Persona not found")
    if body.name is not None: p.name = body.name
    if body.description is not None: p.description = body.description
    if body.session_state is not None: p.session_state = body.session_state
    if body.auth_headers is not None: p.auth_headers = body.auth_headers
    if body.login_steps is not None: p.login_steps = body.login_steps
    if body.refresh_after_hours is not None: p.refresh_after_hours = body.refresh_after_hours
    p.updated_at = datetime.utcnow()
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _to_read(p)


@router.delete("/workspaces/{workspace_id}/personas/{persona_id}")
async def delete_persona(
    workspace_id: int,
    persona_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_editor(workspace_id, principal.user.id, session)
    p = await session.get(Persona, persona_id)
    if not p or p.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Persona not found")
    await session.delete(p)
    await session.commit()
    return {"status": "deleted", "id": persona_id}


@router.post("/workspaces/{workspace_id}/personas/{persona_id}/refresh")
async def refresh_persona(
    workspace_id: int,
    persona_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Kick off a session refresh — runs login_steps in a worker and stores
    the new storageState. Returns immediately with a job id; clients poll
    the persona to observe `last_refreshed_at` change.
    """
    await _ensure_editor(workspace_id, principal.user.id, session)
    p = await session.get(Persona, persona_id)
    if not p or p.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Persona not found")
    if not p.login_steps:
        raise HTTPException(status_code=400, detail="Persona has no login_steps to execute")

    # Lazy import — Celery may not be importable in all contexts (e.g. unit tests).
    try:
        from app.tasks.persona_tasks import refresh_persona_session
        task = refresh_persona_session.delay(persona_id)
        return {"status": "queued", "task_id": getattr(task, "id", None)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "queued_with_warning", "warning": str(exc)}
