#!/usr/bin/env python
"""Bring a database to the current schema, whether it is new or existing.

Behaviour:

  empty database                      -> `alembic upgrade head`
  stamped at a revision on the live   -> `alembic upgrade head`
    chain (app/alembic/versions/)
  stamped at a revision on the LEGACY -> run the legacy chain to its head, which
    chain (versions_legacy/)             is the live chain's root, then continue
                                         with `alembic upgrade head`
  tables but no alembic_version       -> refuse; a human decides

Idempotent, so it is safe to run on every container start. Every replica runs
it and a Postgres advisory lock serialises them (workstream H3).

Why a script and not `alembic upgrade head` in the entrypoint
-----------------------------------------------------------
Until 2026-09 the chain's root (`1f266105057e`) was an empty stub, stamped onto
a database that `SQLModel.metadata.create_all()` had already built, so no
revision created the core tables and Alembic could not build a schema from
scratch. This script papered over that with create_all() + `stamp head`, which
meant fresh installs never ran a migration at all — any DDL that existed only in
a migration (the audit trigger, an explicitly named constraint) was silently
missing on exactly the deployments most likely to be audited.

The root is now a real squashed migration, `e0f1a2b3c4d5`, and fresh installs
take the same path as everyone else: `alembic upgrade head`. The old files were
moved to `app/alembic/versions_legacy/` (off Alembic's `version_locations`).
The one thing plain `alembic upgrade head` cannot do is upgrade a database
stamped somewhere *inside* that legacy history — Alembic would report
"Can't locate revision". This script bridges it: the legacy chain's head IS
the live chain's root (same revision id, same schema), so running the legacy
chain to its head lands the database at the live root with no stamp step.
"""
from __future__ import annotations

import asyncio
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import inspect, text

from app.core.database import engine

ALEMBIC_INI = "alembic.ini"
LEGACY_VERSIONS = "app/alembic/versions_legacy"

# A table that only exists if the real schema was created. Used to tell an empty
# database apart from a populated one that predates Alembic.
SENTINEL_TABLES = ("users", "testrun", "testsuite")


def log(msg: str) -> None:
    print(f"[bootstrap-db] {msg}", flush=True)


def alembic_config(*, legacy: bool = False) -> Config:
    cfg = Config(ALEMBIC_INI)
    # env.py reads settings.DATABASE_URL itself, so no URL override is needed.
    if legacy:
        cfg.set_main_option("version_locations", LEGACY_VERSIONS)
    return cfg


def chain_knows(cfg: Config, revisions: list[str]) -> bool:
    """True if every revision id is on the chain `cfg` points at."""
    script = ScriptDirectory.from_config(cfg)
    try:
        for rev in revisions:
            script.get_revision(rev)
    except CommandError:  # ResolutionError: "Can't locate revision"
        return False
    return True


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


async def current_stamps() -> list[str]:
    """Every row of alembic_version (more than one means an unmerged branch)."""
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            return [row[0] for row in result.all()]
        except Exception:
            return []


async def _async_phase() -> tuple[str, list[str], int]:
    """Do all async DB work, then hand an action back to the sync caller.

    Alembic's env.py calls asyncio.run() itself, so its commands cannot be
    invoked from inside a running event loop. Everything async finishes here and
    the alembic step runs afterwards, outside the loop.
    """
    try:
        has_version, has_tables, table_count = await inspect_state()
        stamps = await current_stamps() if has_version else []
    finally:
        await engine.dispose()

    if has_version:
        return "upgrade", stamps, table_count
    if has_tables:
        return "refuse", [], table_count
    return "create", [], table_count


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
        action, stamps, table_count = asyncio.run(_async_phase())
    except Exception as exc:
        log(f"ERROR: cannot inspect the schema: {exc}")
        log("Check DATABASE_URL and that Postgres is reachable.")
        return 1

    cfg = alembic_config()

    if action == "create":
        log(f"Empty database ({table_count} tables) — building the schema with "
            "`alembic upgrade head`.")
        command.upgrade(cfg, "head")
        log("Schema created.")
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

    # action == "upgrade"
    if not stamps:
        # alembic_version exists but is empty — e.g. `alembic downgrade base`
        # was run. The tables are gone too (the root's downgrade drops them), so
        # a plain upgrade rebuilds everything.
        log("alembic_version is empty — rebuilding with `alembic upgrade head`.")
        command.upgrade(cfg, "head")
        log("Upgrade complete.")
        return 0

    shown = ", ".join(stamps)
    if chain_knows(cfg, stamps):
        log(f"Existing database at revision {shown} — upgrading to head.")
        command.upgrade(cfg, "head")
        log("Upgrade complete.")
        return 0

    legacy_cfg = alembic_config(legacy=True)
    if chain_knows(legacy_cfg, stamps):
        log(f"Existing database at LEGACY revision {shown} — running the legacy "
            f"chain ({LEGACY_VERSIONS}) to its head first.")
        command.upgrade(legacy_cfg, "head")
        log("Legacy chain complete; the database is at the live chain's root. "
            "Continuing with the live chain.")
        command.upgrade(cfg, "head")
        log("Upgrade complete.")
        return 0

    log(f"ERROR: alembic_version holds {shown}, which is on neither the live "
        f"chain (app/alembic/versions) nor the legacy chain ({LEGACY_VERSIONS}).")
    log("This database was migrated by code this image does not contain. "
        "Refusing to guess — restore from snapshot or reconcile by hand.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
