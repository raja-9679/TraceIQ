"""Project environments + secrets.

Environments are named deployment targets (dev/staging/prod) whose
`variables` are referenced in test steps as `{{env.KEY}}` and whose
`base_url` prefixes relative goto URLs. An `exclusive` environment runs one
TestRun at a time (WP16c; app/services/environment_lock.py) — the list shows
the current holder and an editor can force-release it. Secrets are write-only values
referenced as `{{secret.KEY}}`, encrypted at rest; the API never returns
plaintext — it is decrypted only at dispatch time into job payloads.
"""
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import AuthPrincipal, get_current_principal, get_current_user
from app.core.secrets import encrypt_secret
from app.models import ProjectEnvironment, ProjectSecret, User
from app.services.access_service import access_service

router = APIRouter()


class EnvironmentCreate(BaseModel):
    name: str
    base_url: Optional[str] = None
    variables: dict = {}
    is_default: bool = False
    exclusive: bool = False


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    variables: Optional[dict] = None
    is_default: Optional[bool] = None
    exclusive: Optional[bool] = None


class SecretWrite(BaseModel):
    key: str
    value: str


async def _require_editor(user_id: int, project_id: int, session: AsyncSession):
    if not await access_service.has_project_access(user_id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")


@router.get("/projects/{project_id}/environments")
async def list_environments(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    role = await access_service.get_project_role(current_user.id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
    result = await session.exec(
        select(ProjectEnvironment).where(ProjectEnvironment.project_id == project_id))
    return [await _with_lock(env) for env in result.all()]


async def _holder(env: ProjectEnvironment):
    """The lease holder of an exclusive environment, None when free. Redis
    trouble reads as "unknown" rather than failing the page."""
    if not env.exclusive:
        return None
    from app.services import environment_lock
    try:
        return await asyncio.to_thread(environment_lock.holder, env.id)
    except Exception as exc:  # noqa: BLE001
        return {"run_id": None, "since": None, "error": f"lock state unavailable: {exc}"[:200]}


async def _with_lock(env: ProjectEnvironment) -> dict:
    return {**env.model_dump(), "lock": await _holder(env)}


@router.delete("/environments/{environment_id}/lock")
async def force_release_lock(environment_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                             session: AsyncSession = Depends(get_session)):
    """Force-release an exclusive environment's lease (editor, audited). The
    holder run keeps running; the next waiting run is admitted on its next
    retry. For a run that is stuck or a lease you know is stale."""
    env = await session.get(ProjectEnvironment, environment_id)
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    if principal.api_key is not None:
        from app.api.jobs import api_key_allows_project
        if not api_key_allows_project(principal.api_key, env.project_id):
            raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    await _require_editor(principal.user.id, env.project_id, session)
    from app.services import environment_lock
    try:
        previous = await asyncio.to_thread(environment_lock.force_release, env.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Lock store unavailable: {exc}"[:300])
    if previous is not None:
        from app.models import Project
        from app.services.audit import record as audit_record
        project = await session.get(Project, env.project_id)
        await audit_record(session, entity_type="project_environment", entity_id=env.id,
                           action="lock_force_released", principal=principal,
                           workspace_id=getattr(project, "workspace_id", None),
                           changes={"environment": env.name, "holder_run_id": previous["run_id"],
                                    "held_since": previous.get("since")})
        await session.commit()
    return {"environment_id": env.id, "released": previous is not None, "previous_holder": previous}


@router.post("/projects/{project_id}/environments")
async def create_environment(project_id: int, body: EnvironmentCreate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    await _require_editor(current_user.id, project_id, session)

    if body.is_default:
        await _clear_default(project_id, session)

    env = ProjectEnvironment(project_id=project_id, **body.model_dump())
    session.add(env)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Environment '{body.name}' already exists")
    await session.refresh(env)
    return await _with_lock(env)


@router.put("/environments/{environment_id}")
async def update_environment(environment_id: int, body: EnvironmentUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    env = await session.get(ProjectEnvironment, environment_id)
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    await _require_editor(current_user.id, env.project_id, session)

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        await _clear_default(env.project_id, session)
    if "exclusive" in data and bool(data["exclusive"]) != bool(env.exclusive):
        # Who made an environment exclusive (or stopped it being) is the first
        # question when runs start queueing — or stop queueing — on it.
        from app.models import Project
        from app.services.audit import record as audit_record
        project = await session.get(Project, env.project_id)
        await audit_record(session, entity_type="project_environment", entity_id=env.id,
                           action="exclusive_on" if data["exclusive"] else "exclusive_off",
                           user_id=current_user.id,
                           workspace_id=getattr(project, "workspace_id", None),
                           changes={"environment": env.name})
    for key, value in data.items():
        setattr(env, key, value)
    env.updated_at = datetime.utcnow()
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return await _with_lock(env)


@router.delete("/environments/{environment_id}")
async def delete_environment(environment_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    env = await session.get(ProjectEnvironment, environment_id)
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    await _require_editor(current_user.id, env.project_id, session)
    await session.delete(env)
    await session.commit()
    return {"status": "deleted"}


async def _clear_default(project_id: int, session: AsyncSession):
    result = await session.exec(
        select(ProjectEnvironment).where(
            ProjectEnvironment.project_id == project_id,
            ProjectEnvironment.is_default == True))  # noqa: E712
    for other in result.all():
        other.is_default = False
        session.add(other)


@router.get("/projects/{project_id}/secrets")
async def list_secret_keys(project_id: int, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    """Key names and timestamps only — never values."""
    role = await access_service.get_project_role(current_user.id, project_id, session)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
    result = await session.exec(
        select(ProjectSecret).where(ProjectSecret.project_id == project_id))
    return [
        {"id": s.id, "key": s.key, "created_at": s.created_at, "updated_at": s.updated_at}
        for s in result.all()
    ]


@router.put("/projects/{project_id}/secrets")
async def upsert_secret(project_id: int, body: SecretWrite, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    await _require_editor(current_user.id, project_id, session)
    if not body.key.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Secret keys must be alphanumeric/underscore (used as {{secret.KEY}})")

    result = await session.exec(
        select(ProjectSecret).where(
            ProjectSecret.project_id == project_id,
            ProjectSecret.key == body.key))
    secret = result.first()
    if secret:
        secret.value_encrypted = encrypt_secret(body.value)
        secret.updated_at = datetime.utcnow()
    else:
        secret = ProjectSecret(
            project_id=project_id,
            key=body.key,
            value_encrypted=encrypt_secret(body.value),
        )
    session.add(secret)
    await session.commit()
    return {"status": "stored", "key": body.key}


@router.delete("/projects/{project_id}/secrets/{key}")
async def delete_secret(project_id: int, key: str, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    await _require_editor(current_user.id, project_id, session)
    result = await session.exec(
        select(ProjectSecret).where(
            ProjectSecret.project_id == project_id,
            ProjectSecret.key == key))
    secret = result.first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    await session.delete(secret)
    await session.commit()
    return {"status": "deleted"}
