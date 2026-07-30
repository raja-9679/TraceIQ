"""Admin CRUD for saved LLM provider configs (multi-provider registry).

Several providers can be active at once; users pick one per analysis, and the
row marked is_default serves calls that don't specify one. With no rows saved
the legacy single-provider instance settings (LLM_PROVIDER etc.) still apply.
API keys are Fernet-encrypted at rest and never returned — only whether one
is set.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.instance_settings import get_current_instance_admin
from app.core.auth import get_current_user
from app.core.database import get_session
from app.core.secrets import encrypt_secret
from app.models import LLMProviderConfig, User

router = APIRouter()

PROVIDER_TYPES = ("anthropic", "openai", "gemini", "ollama", "openai-compatible")


class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    is_active: bool = True
    is_default: bool = False


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    # Empty string = leave the stored key unchanged (masked field in the UI);
    # null clears it only when clear_api_key is set.
    api_key: Optional[str] = None
    clear_api_key: bool = False
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class ProviderRead(BaseModel):
    id: int
    name: str
    provider_type: str
    model: str
    base_url: Optional[str]
    api_key_set: bool
    is_active: bool
    is_default: bool
    updated_at: datetime


class ProviderPublic(BaseModel):
    """What non-admin users see in the analysis picker."""
    id: int
    name: str
    provider_type: str
    model: str
    is_default: bool


def _read(row: LLMProviderConfig) -> ProviderRead:
    return ProviderRead(
        id=row.id, name=row.name, provider_type=row.provider_type,
        model=row.model, base_url=row.base_url,
        api_key_set=bool(row.api_key_encrypted),
        is_active=row.is_active, is_default=row.is_default,
        updated_at=row.updated_at)


def _validate_type(provider_type: str) -> str:
    t = provider_type.strip().lower()
    if t not in PROVIDER_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"provider_type must be one of {list(PROVIDER_TYPES)}")
    return t


async def _clear_other_defaults(session: AsyncSession, keep_id: Optional[int]) -> None:
    rows = (await session.exec(
        select(LLMProviderConfig).where(LLMProviderConfig.is_default == True))).all()  # noqa: E712
    for r in rows:
        if r.id != keep_id:
            r.is_default = False
            session.add(r)


def _invalidate() -> None:
    from app.ai.providers import invalidate_provider_config_cache
    invalidate_provider_config_cache()


@router.get("/admin/llm-providers", response_model=list[ProviderRead])
async def list_providers(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    rows = (await session.exec(
        select(LLMProviderConfig).order_by(LLMProviderConfig.id))).all()
    return [_read(r) for r in rows]


@router.post("/admin/llm-providers", response_model=ProviderRead, status_code=201)
async def create_provider(
    body: ProviderCreate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    existing = (await session.exec(
        select(LLMProviderConfig).where(LLMProviderConfig.name == body.name.strip()))).first()
    if existing:
        raise HTTPException(status_code=400, detail="A provider with this name already exists")
    row = LLMProviderConfig(
        name=body.name.strip(),
        provider_type=_validate_type(body.provider_type),
        model=body.model.strip(),
        base_url=(body.base_url or "").strip() or None,
        api_key_encrypted=encrypt_secret(body.api_key) if body.api_key else None,
        is_active=body.is_active,
        is_default=body.is_default,
        updated_by_id=admin.id,
    )
    session.add(row)
    await session.flush()  # assigns row.id so _clear_other_defaults can skip it
    if body.is_default:
        await _clear_other_defaults(session, keep_id=row.id)
    await session.commit()
    await session.refresh(row)
    _invalidate()
    return _read(row)


@router.patch("/admin/llm-providers/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    row = await session.get(LLMProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    if body.name is not None and body.name.strip():
        dup = (await session.exec(
            select(LLMProviderConfig).where(
                LLMProviderConfig.name == body.name.strip(),
                LLMProviderConfig.id != provider_id))).first()
        if dup:
            raise HTTPException(status_code=400, detail="A provider with this name already exists")
        row.name = body.name.strip()
    if body.provider_type is not None:
        row.provider_type = _validate_type(body.provider_type)
    if body.model is not None:
        row.model = body.model.strip()
    if body.base_url is not None:
        row.base_url = body.base_url.strip() or None
    if body.clear_api_key:
        row.api_key_encrypted = None
    elif body.api_key:  # empty string = unchanged
        row.api_key_encrypted = encrypt_secret(body.api_key)
    if body.is_active is not None:
        row.is_active = body.is_active
    if body.is_default is not None:
        row.is_default = body.is_default
        if body.is_default:
            await _clear_other_defaults(session, keep_id=provider_id)
    row.updated_at = datetime.utcnow()
    row.updated_by_id = admin.id
    session.add(row)
    await session.commit()
    await session.refresh(row)
    _invalidate()
    return _read(row)


@router.delete("/admin/llm-providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    row = await session.get(LLMProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    await session.delete(row)
    await session.commit()
    _invalidate()


@router.post("/admin/llm-providers/{provider_id}/test")
async def test_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    """One tiny completion through THIS saved config, bypassing is_active so a
    provider can be verified before being switched on."""
    import asyncio

    from app.core.secrets import decrypt_secret
    from app.ai.providers import NullProvider, build_provider_from_config

    row = await session.get(LLMProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    api_key = ""
    if row.api_key_encrypted:
        try:
            api_key = decrypt_secret(row.api_key_encrypted)
        except Exception:
            raise HTTPException(status_code=500,
                                detail="Stored API key cannot be decrypted (SECRET_KEY rotated?)")
    built = build_provider_from_config({
        "name": row.name, "provider_type": row.provider_type, "model": row.model,
        "base_url": row.base_url, "api_key": api_key,
    })
    if isinstance(built, NullProvider):
        raise HTTPException(status_code=400,
                            detail="Config incomplete: this provider type needs an API key "
                                   "(or base URL + model for openai-compatible)")
    reply = await asyncio.to_thread(
        built.complete, "Health check. Reply with exactly: ok", max_tokens=10)
    if not reply:
        raise HTTPException(status_code=502,
                            detail=f"LLM test failed: '{row.name}' returned no reply "
                                   "(check the API key / base URL; details are in the backend log)")
    return {"ok": True, "provider": row.name, "model": row.model, "reply": reply.strip()[:100]}


@router.get("/llm-providers/active", response_model=list[ProviderPublic])
async def list_active_providers(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    """Active providers, for the per-analysis picker. Any authenticated user."""
    rows = (await session.exec(
        select(LLMProviderConfig)
        .where(LLMProviderConfig.is_active == True)  # noqa: E712
        .order_by(LLMProviderConfig.is_default.desc(), LLMProviderConfig.name))).all()
    return [ProviderPublic(id=r.id, name=r.name, provider_type=r.provider_type,
                           model=r.model, is_default=r.is_default) for r in rows]
