"""Shared fixtures for the demo-seed integration tests (not a test module).

Import the fixtures into a test module with
    from tests.integration.demo_seed_fixtures import demo, owner, session  # noqa: F401
"""
from __future__ import annotations

from typing import Dict

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import db_url_for, settings
from app.models import Tenant, User, UserWorkspace, Workspace

#: Tables whose rows a demo delete legitimately leaves behind.
#: auditlog is append-only by design (PURGE_EXEMPT); the owner's own rows
#: (their users row, refresh tokens) are not the demo's.
KEEP_AFTER_DELETE = {"auditlog"}


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def owner(session) -> User:
    """A user with a real tenant + workspace, as an evaluator would have."""
    from app.services.rbac_service import rbac_service
    user = User(email="eval@corp.example.com", full_name="Eval User", hashed_password="x",
                is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    role = await rbac_service.get_role_by_name(session, "Workspace Admin")
    session.add(UserWorkspace(user_id=user.id, workspace_id=ws.id, role="admin", role_id=role.id))
    await session.commit()
    return user


@pytest_asyncio.fixture(loop_scope="function")
async def demo(session, owner) -> Dict:
    """A freshly created demo workspace (the create_demo_workspace summary)."""
    from app.services import demo_workspace as dw
    return await dw.create_demo_workspace(session, owner)


async def count_all_tables(session) -> Dict[str, int]:
    out = {}
    for name in sorted(SQLModel.metadata.tables):
        out[name] = int((await session.exec(text(f'SELECT count(*) FROM "{name}"'))).one()[0])
    return out
