"""Wipe a DEVELOPMENT database and rebuild it from the migrations.

    CONFIRM_RESET=yes python scripts/reset_db.py

Drops the whole `public` schema (tables, enum types, trigger functions and the
alembic_version table alike) and then runs `alembic upgrade head`, so the result
is exactly what a fresh install gets — including the audit trigger and a correct
revision stamp. The previous version used `drop_all()` + `create_all()`, which
left `alembic_version` pointing at a revision the new tables had never been
through and produced a schema with no trigger.
"""
import asyncio
import os
import sys

from sqlalchemy import text

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BACKEND)

from app.core.database import engine  # noqa: E402


async def wipe() -> None:
    print("WARNING: dropping schema `public` — every table and type goes with it.")
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    print("Schema dropped.")


def rebuild() -> None:
    # Alembic's env.py calls asyncio.run() itself, so this must happen outside
    # any running event loop.
    from alembic import command
    from alembic.config import Config

    os.chdir(BACKEND)
    command.upgrade(Config("alembic.ini"), "head")
    print("Schema rebuilt at head.")


if __name__ == "__main__":
    if os.environ.get("CONFIRM_RESET", "no") != "yes":
        print("To reset the database, run with CONFIRM_RESET=yes")
        sys.exit(1)
    asyncio.run(wipe())
    rebuild()
