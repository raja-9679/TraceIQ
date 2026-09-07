"""Reconcile index drift between the legacy chain and the model schema

Revision ID: f2a3b4c5d6e7
Revises: e0f1a2b3c4d5

Two populations of databases arrive at `e0f1a2b3c4d5` with slightly different
indexes, and neither matches the models exactly:

- Databases that were *migrated* through the legacy chain have
  `ix_auditlog_workspace_timestamp` (created by legacy `c8d9e0f1a2b3`) but lack
  `ix_auditlog_user_id` / `ix_auditlog_workspace_id` (added to the model as
  `index=True` without a migration) and `ix_testrun_finalized_at` (legacy
  `a2f4c6d8e0b1` created it, but a database built by `create_all` and stamped
  head never ran that revision).
- Databases *built by `create_all`* had the model indexes and not the
  composite one, which lived only in the migration.

Found by diffing `SQLModel.metadata` against the live database with
`alembic.autogenerate.compare_metadata` — four differences, all indexes, no
column or constraint drift. The composite index is now declared on the model
too, so the squashed root creates all four; this revision exists only to
backfill them onto databases that reached the root by migrating.

`IF NOT EXISTS` makes it a no-op wherever an index is already present, and
`CONCURRENTLY` keeps it from taking a write lock on `testrun`/`auditlog`, which
are the two largest tables in any real deployment. CONCURRENTLY cannot run
inside a transaction, hence the autocommit block (the supported way; legacy
`a7b3c9d2e1f4` issued a bare COMMIT instead).

Downgrade is deliberately a no-op: every one of these indexes belongs to the
schema `e0f1a2b3c4d5` itself describes, so dropping them would move a database
*away* from that revision, not back to it.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEXES = (
    ("ix_auditlog_user_id", "auditlog", "user_id"),
    ("ix_auditlog_workspace_id", "auditlog", "workspace_id"),
    ("ix_auditlog_workspace_timestamp", "auditlog", "workspace_id, timestamp"),
    ("ix_testrun_finalized_at", "testrun", "finalized_at"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in _INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON {table} ({columns})"
            )


def downgrade() -> None:
    # See the module docstring: these indexes are part of e0f1a2b3c4d5's own
    # schema, so there is nothing to undo.
    pass
