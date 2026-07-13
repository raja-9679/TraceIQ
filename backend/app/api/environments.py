"""Project environments + secrets.

Environments are named deployment targets (dev/staging/prod) whose
`variables` are referenced in test steps as `{{env.KEY}}` and whose
`base_url` prefixes relative goto URLs. Secrets are write-only values
referenced as `{{secret.KEY}}`, encrypted at rest; the API never returns
plaintext — it is decrypted only at dispatch time into job payloads.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.secrets import encrypt_secret
from app.models import ProjectEnvironment, ProjectSecret, User
from app.services.access_service import access_service

router = APIRouter()


class EnvironmentCreate(BaseModel):
    name: str
    base_url: Optional[str] = None
    variables: dict = {}
    is_default: bool = False


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    variables: Optional[dict] = None
    is_default: Optional[bool] = None


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
    return result.all()


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
    return env


@router.put("/environments/{environment_id}")
async def update_environment(environment_id: int, body: EnvironmentUpdate, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    env = await session.get(ProjectEnvironment, environment_id)
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    await _require_editor(current_user.id, env.project_id, session)

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        await _clear_default(env.project_id, session)
    for key, value in data.items():
        setattr(env, key, value)
    env.updated_at = datetime.utcnow()
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


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
