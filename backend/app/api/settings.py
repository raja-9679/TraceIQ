from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.core.database import get_session
from app.settings_models import UserSettings, UserSettingsRead, UserSettingsUpdate
from app.core.auth import get_current_user
from app.models import User
from datetime import datetime

router = APIRouter()

@router.get("/", response_model=UserSettingsRead)
async def get_user_settings(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get current user's settings"""
    result = await session.exec(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    settings = result.first()
    
    # Create default settings if none exist
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    
    return settings


@router.put("/", response_model=UserSettingsRead)
async def update_user_settings(
    settings_update: UserSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Update current user's settings"""
    result = await session.exec(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    settings = result.first()
    
    # Create settings if none exist
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        session.add(settings)
    
    # Update only provided fields
    update_data = settings_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(settings, key, value)
    
    settings.updated_at = datetime.utcnow()
    
    await session.commit()
    await session.refresh(settings)
    
    return settings
