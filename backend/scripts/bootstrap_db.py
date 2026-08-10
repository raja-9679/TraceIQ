#!/usr/bin/env python
"""Bring a database to the current schema, whether it is new or existing.

Why this exists instead of just `alembic upgrade head`:

The Alembic baseline (`1f266105057e_baseline_with_schedules`) is an empty stub.
It was stamped onto a database that `SQLModel.metadata.create_all()` had already
built, and every later revision only adds to that. So no migration creates the
core tables, and `alembic upgrade head` against an empty database fails as soon
as a revision touches a table that was never created (the first failure is
`CREATE INDEX ... ON testrun` in the performance-indexes revision).

That was invisible while TraceIQ only ever ran on long-lived databases. It became
a hard blocker for self-hosting, where every install starts empty.

Behaviour:

  empty database            -> create_all() to the current model schema, then
                               `alembic stamp head` so later upgrades work
  database with a version   -> `alembic upgrade head` (normal path)
  tables but no version     -> `alembic stamp head` only if the schema already
                               looks current, otherwise refuse and let a human
                               decide

Idempotent, so it is safe to run on every container start.
"""
from __future__ import annotations

import asyncio
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

# Importing app.models registers every table on SQLModel.metadata. Without it
# create_all() would produce an empty schema.
from app import models  # noqa: F401
from sqlmodel import SQLModel

from app.core.database import engine

ALEMBIC_INI = "alembic.ini"

# A table that only exists if the real schema was created. Used to tell an empty
# database apart from a populated one that predates Alembic.
SENTINEL_TABLES = ("users", "testrun", "testsuite")


def log(msg: str) -> None:
    print(f"[bootstrap-db] {msg}", flush=True)


def alembic_config() -> Config:
    cfg = Config(ALEMBIC_INI)
    # env.py reads settings.DATABASE_URL itself, so no URL override is needed.
    return cfg


async def inspect_state() -> tuple[bool, bool, int]:
    """Return (has_alembic_version, has_app_tables, table_count)."""
    async with engine.connect() as conn:
        def _check(sync_conn):
            insp = inspect(sync_conn)
            names = set(insp.get_table_names())
            return (
                "alembic_version" in names,
                any(t in names for t in SENTINEL_TABLES),
                len(names),
            )
        return await conn.run_sync(_check)


async def create_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def current_stamp() -> str | None:
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return row[0] if row else None
        except Exception:
            return None


async def _async_phase() -> tuple[str, str | None, int]:
    """Do all async DB work, then hand an action back to the sync caller.

    Alembic's env.py calls asyncio.run() itself, so its commands cannot be
    invoked from inside a running event loop. Everything async finishes here and
    the alembic step runs afterwards, outside the loop.
    """
    has_version, has_tables, table_count = await inspect_state()

    if has_version:
        stamp = await current_stamp()
        await engine.dispose()
        return "upgrade", stamp, table_count

    if has_tables:
        await engine.dispose()
        return "refuse", None, table_count

    log(f"Empty database ({table_count} tables) — creating the schema from the models.")
    await create_schema()
    await engine.dispose()
    return "stamp", None, table_count


# Postgres advisory-lock key. Arbitrary but stable — every replica must pick the
# same number or the lock does not serialise anything.
_MIGRATION_LOCK_KEY = 8534217601


def _with_migration_lock(fn):
    """Run `fn` while holding a session-scoped Postgres advisory lock.

    Workstream H3. `RUN_MIGRATIONS` defaults to true and every replica ran this,
    with nothing serialising them: two API containers starting together both
    executed `alembic upgrade head` concurrently. Alembic is not safe under
    that — the losers fail on a duplicate DDL, or worse, half-apply while the
    other is mid-migration.

    A blocking lock, not a try-and-skip: a replica that skipped migrating would
    start serving against a schema it has not verified. Waiting is correct;
    starting early is not.
    """
    from sqlalchemy import create_engine, text

    from app.core.config import db_url_for, settings

    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True),
                           pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            # pg_advisory_lock is held for the life of the SESSION, so it must
            # be taken on the same connection that is kept open — not inside a
            # transaction that commits.
            log("Waiting for the schema lock (another replica may be migrating)...")
            conn.execute(text("SELECT pg_advisory_lock(:key)"),
                         {"key": _MIGRATION_LOCK_KEY})
            conn.commit()
            log("Schema lock acquired.")
            try:
                return fn()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"),
                             {"key": _MIGRATION_LOCK_KEY})
                conn.commit()
    finally:
        engine.dispose()


def main() -> int:
    return _with_migration_lock(_migrate)


def _migrate() -> int:
    try:
        action, stamp, table_count = asyncio.run(_async_phase())
    except Exception as exc:
        log(f"ERROR: cannot inspect or create the schema: {exc}")
        log("Check DATABASE_URL and that Postgres is reachable.")
        return 1

    cfg = alembic_config()

    if action == "upgrade":
        log(f"Existing database at revision {stamp or 'unknown'} — upgrading to head.")
        command.upgrade(cfg, "head")
        log("Upgrade complete.")
        return 0

    if action == "refuse":
        # Tables exist but Alembic has no record. Stamping blindly could skip a
        # migration the schema genuinely needs, so require a human decision.
        log(
            f"ERROR: found {table_count} tables but no alembic_version table. "
            "This database predates migration tracking."
        )
        log(
            "If the schema is already current, run `alembic stamp head` once, "
            "then restart. If not, migrate it manually — do not guess."
        )
        return 1

    command.stamp(cfg, "head")
    log("Schema created and stamped at head.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
