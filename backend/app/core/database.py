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

# Connection pool settings to prevent hanging connections
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Reduce log noise
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,  # Wait max 30s for connection
    pool_recycle=1800,  # Recycle connections after 30 minutes
    pool_pre_ping=True,  # Check connection health before use
)


async def close_db():
    """Dispose of the engine connection pool"""
    await engine.dispose()


async def init_db():
    async with engine.begin() as conn:
        # In production, use Alembic. For now, create tables directly.
        # await conn.run_sync(SQLModel.metadata.create_all)
        pass

    # Initialize RBAC
    from app.core.rbac_init import init_rbac
    async with engine.begin() as conn:
        # We need a session, not just a connection for init_rbac because it uses SQLModel objects
        pass

    # To use session, we need to use the sessionmaker
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await init_rbac(session)


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
