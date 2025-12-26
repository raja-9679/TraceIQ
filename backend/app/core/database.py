from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from contextlib import asynccontextmanager

# SQLite optimization: Enable WAL mode for concurrency
from sqlalchemy import event

# Patch for async engine event listening
import sqlite3

engine = create_async_engine(settings.DATABASE_URL, echo=True, future=True)

async def init_db():
    async with engine.begin() as conn:
        # In production, use Alembic. For now, create tables directly.
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session

@asynccontextmanager
async def get_session_context() -> AsyncSession:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
