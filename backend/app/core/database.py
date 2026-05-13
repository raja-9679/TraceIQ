from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from contextlib import asynccontextmanager

# Connection pool settings to prevent hanging connections
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)

# Module-level sessionmaker — created once, reused for every request.
async_session_factory = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
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
    async with async_session_factory() as session:
        await init_rbac(session)


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_session_context() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
